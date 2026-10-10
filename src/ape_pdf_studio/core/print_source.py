"""Private print copies of already checked revisions; never edit the document."""
from .localization import tr
from pathlib import Path
import tempfile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject

from .errors import PDFOperationError


class PrintSource:
    def __init__(self, source, password=None):
        self._temp = tempfile.TemporaryDirectory(prefix="apex_print_")
        self.original = Path(self._temp.name) / "source.pdf"
        self.original.write_bytes(Path(source).read_bytes())
        self.password = password
        self._copies = {}

    def path(self, comments=True):
        if comments in self._copies:
            return self._copies[comments]
        destination = Path(self._temp.name) / ("comments.pdf" if comments else "document.pdf")
        with PdfReader(self.original) as reader:
            if reader.is_encrypted and not reader.decrypt(self.password or ""):
                raise PDFOperationError(tr("Password necessaria per stampare il PDF."))
            writer = PdfWriter()
            # Only page content goes to the printer: no document actions or attachments.
            for page in reader.pages:
                added = writer.add_page(page)
                kept = []
                for reference in added.get("/Annots", []):
                    annotation = reference.get_object()
                    flags = int(annotation.get("/F", 0))
                    if flags & 4 and not flags & (1 | 2) and (
                            comments or annotation.get("/Subtype") == "/Widget"):
                        # NoView hides an annotation on screen, not on printed paper.
                        annotation[NameObject("/F")] = NumberObject(flags & ~32)
                        kept.append(reference)
                if kept:
                    added[NameObject("/Annots")] = ArrayObject(kept)
                else:
                    added.pop("/Annots", None)
            writer.write(destination)
        self._copies[comments] = destination
        return destination

    def close(self):
        self.password = None
        self._temp.cleanup()
