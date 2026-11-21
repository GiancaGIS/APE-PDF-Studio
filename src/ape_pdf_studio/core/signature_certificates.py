"""Read embedded X.509 data in the document worker."""
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes

from .document_info import MAX_SIGNATURE_CERTIFICATES, SignatureCertificate, inspect_document, pdf_string_bytes

MAX_SIGNATURE_BYTES = 4 * 1024 * 1024
CMS_SUBFILTERS = ("/adbe.pkcs7.detached", "/adbe.pkcs7.sha1",
                  "/ETSI.CAdES.detached", "/ETSI.RFC3161")
SIGNED_DATA_OID = b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02"


class _DerReader:
    """Read bounded, definite-length values from the CMS envelope (RFC 5652)."""

    def __init__(self, data):
        self.remaining = memoryview(data)

    def peek(self):
        return self.remaining[0] if self.remaining else None

    def read(self, expected=None, *, encoded=False):
        data = self.remaining
        if len(data) < 2 or data[0] & 0x1f == 0x1f:
            raise ValueError("Invalid DER tag")
        tag, length = data[:2]
        if expected is not None and tag != expected:
            raise ValueError("Unexpected DER tag")
        start = 2
        if length >= 0x80:
            count = length & 0x7f
            start += count
            if not 1 <= count <= 4 or len(data) < start or data[2] == 0:
                raise ValueError("Invalid DER length")
            length = int.from_bytes(data[2:start], "big")
            if length < 0x80:
                raise ValueError("Non-minimal DER length")
        end = start + length
        if end > len(data):
            raise ValueError("Truncated DER value")
        self.remaining = data[end:]
        return data[:end] if encoded else data[start:end]

    def require_end(self):
        if self.remaining:
            raise ValueError("Unexpected trailing DER values")


def _cms_certificates(contents):
    if len(contents) > MAX_SIGNATURE_BYTES:
        return (), set()
    outer = _DerReader(contents)
    info = _DerReader(outer.read(0x30))
    if any(outer.remaining) or info.read(0x06) != SIGNED_DATA_OID:
        raise ValueError("Invalid CMS ContentInfo")
    wrapper = _DerReader(info.read(0xa0))
    info.require_end()
    signed = _DerReader(wrapper.read(0x30))
    wrapper.require_end()
    signed.read(0x02)  # Version
    signed.read(0x31)  # Digest algorithms
    signed.read(0x30)  # Encapsulated content
    if signed.peek() != 0xa0:
        return (), set()
    choices = _DerReader(signed.read(0xa0))
    certificates = []
    for _ in range(MAX_SIGNATURE_CERTIFICATES):
        tag = choices.peek()
        if tag is None:
            break
        entry = choices.read(encoded=True)
        if tag == 0x30:  # X.509 Certificate choice
            certificates.append(entry)
    choices.require_end()
    if signed.peek() == 0xa1:
        signed.read(0xa1)  # Revocation information
    signers = _DerReader(signed.read(0x31))
    signed.require_end()
    return certificates, _signer_identifiers(signers)


def _signer_identifiers(signers):
    identifiers = set()
    for _ in range(MAX_SIGNATURE_CERTIFICATES):
        if signers.peek() is None:
            return identifiers
        signer = _DerReader(signers.read(0x30))
        signer.read(0x02)
        if signer.peek() == 0x80:
            digest = signer.read(0x80)
            if 0 < len(digest) <= 128:
                identifiers.add(bytes(digest))
        else:
            identity = _DerReader(signer.read(0x30))
            issuer = identity.read(0x30, encoded=True)
            serial = identity.read(0x02)
            identity.require_end()
            if len(issuer) <= 16384 and 0 < len(serial) <= 32:
                identifiers.add((bytes(issuer), int.from_bytes(serial, "big", signed=True)))
        signer.read(0x30)  # Digest algorithm
        if signer.peek() == 0xa0:
            signer.read(0xa0)
        signer.read(0x30)  # Signature algorithm
        signer.read(0x04)  # Signature value
        if signer.peek() == 0xa1:
            signer.read(0xa1)
        signer.require_end()
    signers.require_end()
    return identifiers


def _legacy_certificates(value):
    if hasattr(value, "get_object"):
        value = value.get_object()
    entries = value if isinstance(value, list) else [value]
    if len(entries) > MAX_SIGNATURE_CERTIFICATES:
        return []
    certificates = []
    total = 0
    for entry in entries:
        if hasattr(entry, "get_object"):
            entry = entry.get_object()
        data = pdf_string_bytes(entry)
        total += len(data)
        if total > MAX_SIGNATURE_BYTES:
            return []
        certificates.append(data)
    return certificates


def _certificate_identifiers(certificate, signer_ids):
    identity = (certificate.issuer.public_bytes(), certificate.serial_number)
    matched = {identity} & signer_ids
    if any(isinstance(identity, bytes) for identity in signer_ids):
        try:
            extension = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
            matched.update({extension.value.digest} & signer_ids)
        except (x509.ExtensionNotFound, x509.DuplicateExtension, ValueError):
            pass
    return matched


def _describe(certificates, signer_ids, legacy):
    decoded = {}
    for index, data in enumerate(certificates):
        try:
            certificate = x509.load_der_x509_certificate(bytes(data))
            fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
            if fingerprint in decoded:
                continue
            matched = {None} if legacy and index == 0 else _certificate_identifiers(certificate, signer_ids)
            decoded[fingerprint] = (certificate, matched)
        except (ValueError, UnsupportedAlgorithm):
            continue

    # Conflicting identifiers can be embedded by the PDF. Only label a unique match.
    counts = {}
    for _, matched in decoded.values():
        for identity in matched:
            counts[identity] = counts.get(identity, 0) + 1
    result = []
    for fingerprint, (certificate, matched) in decoded.items():
        try:
            result.append(SignatureCertificate(
                subject=certificate.subject.rfc4514_string()[:4096],
                issuer=certificate.issuer.rfc4514_string()[:4096],
                serial=format(certificate.serial_number, "X")[:128],
                valid_from=certificate.not_valid_before_utc.isoformat(),
                valid_until=certificate.not_valid_after_utc.isoformat(),
                sha256=fingerprint,
                referenced=any(counts[identity] == 1 for identity in matched),
            ))
        except (ValueError, UnsupportedAlgorithm):
            continue
    return tuple(sorted(result, key=lambda certificate: not certificate.referenced))


def read_signature_certificates(reader, index):
    selected = []

    def select(position, value, contents):
        if position == index:
            selected.append((value, contents))

    inspect_document(reader, on_signature=select)
    if not selected:
        return ()
    value, contents = selected[0]
    subfilter = value.get("/SubFilter")
    try:
        if subfilter in CMS_SUBFILTERS:
            certificates, signer_ids = _cms_certificates(contents)
            return _describe(certificates, signer_ids, legacy=False)
        if subfilter == "/adbe.x509.rsa_sha1":
            return _describe(_legacy_certificates(value.get("/Cert")), set(), legacy=True)
    except (ValueError, UnsupportedAlgorithm):
        pass
    return ()
