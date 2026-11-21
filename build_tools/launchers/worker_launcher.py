"""Console helper used exclusively through hidden, redirected worker processes."""
from ape_pdf_studio.core.worker_entry import main

if __name__ == "__main__":
    raise SystemExit(main())
