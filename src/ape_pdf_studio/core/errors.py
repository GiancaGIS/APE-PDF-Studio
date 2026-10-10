"""Shared errors, without importing a PDF parser or the GUI."""


class PDFOperationError(Exception):
    """An operation cannot safely be completed."""


class PDFPasswordRequired(PDFOperationError):
    """A valid PDF password must be provided for reading."""


class PDFSecurityError(PDFOperationError):
    """The preventive security check rejected a PDF."""
