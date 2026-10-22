"""MuPDF documents used by APE always have their JavaScript runtime disabled."""
from .errors import PDFSecurityError
from .localization import tr


def disable_javascript(document):
    import pymupdf
    if document.is_pdf:
        native = pymupdf.mupdf.pdf_document_from_fz_document(document.this)
        pymupdf.mupdf.pdf_disable_js(native)
        if pymupdf.mupdf.pdf_js_supported(native):
            raise PDFSecurityError(tr("Impossibile disabilitare JavaScript nel motore PDF."))


def open_static_pdf(*args, **kwargs):
    import pymupdf
    document = pymupdf.open(*args, **kwargs)
    try:
        disable_javascript(document)
        return document
    except Exception:
        document.close()
        raise
