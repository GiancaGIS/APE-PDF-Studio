"""Optional image compression and lossless PDF optimization, inside a worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .errors import PDFOperationError
from .localization import tr


def compression_profile(level):
    """0 preserves image samples; 1..100 progressively lower JPEG quality/DPI."""
    if type(level) is not int or not 0 <= level <= 100:
        raise PDFOperationError(tr('Scegli un livello di compressione da 0 a 100.'))
    if not level:
        return None
    return dict(quality=round(95 - level * .25), dpi_target=round(300 - level * 1.8))


def compress_pdf(source, destination, level, progress=None):
    """Write only a completed, checked PDF; never alter the input or flatten pages.

    The caller supplies a private snapshot and a private output. Publication to
    a user-selected destination belongs to the trusted broker/GUI job.
    """
    from pypdf import PdfReader
    from .pdf_runtime import open_static_pdf
    from .security import check_pdf, MAX_FILE_BYTES
    from .private_work import check_budget

    profile = compression_profile(level)
    source, destination = Path(source), Path(destination)
    if source == destination or destination.exists():
        raise PDFOperationError(tr('La compressione richiede una nuova copia PDF.'))
    notify = progress or (lambda *_: None)
    baseline = destination.with_suffix('.optimized.pdf')
    candidate = destination.with_suffix('.compressed.pdf')
    try:
        check_pdf(source)
        with PdfReader(source) as reader:
            if reader.is_encrypted:
                raise PDFOperationError(tr('I PDF protetti sono in sola lettura.'))
            pages = len(reader.pages)
        notify(0, 3, tr('Ottimizzazione senza perdita…'))
        # Keep clean=False: do not sanitize/rewrite page operators or flatten
        # fields, annotations, layers, links, metadata or embedded fonts.
        with open_static_pdf(source) as document:
            document.save(baseline, garbage=3, deflate=True, deflate_images=True,
                          deflate_fonts=True, use_objstms=1, compression_effort=75,
                          raise_on_repair=True)
            check_budget(destination.parent)
            best = baseline if baseline.stat().st_size < source.stat().st_size else source
            if profile:
                notify(1, 3, tr('Compressione delle immagini…'))
                # Leave bitonal images untouched (e.g. scanned small print).
                # Preserve color and transparency; no page rasterization.
                import pymupdf
                options = pymupdf.mupdf.PdfImageRewriterOptions()
                for kind in ('color_lossless', 'color_lossy', 'gray_lossless', 'gray_lossy'):
                    prefix = kind + '_image_'
                    setattr(options, prefix + 'recompress_method', pymupdf.mupdf.FZ_RECOMPRESS_JPEG)
                    setattr(options, prefix + 'recompress_quality', str(profile['quality']))
                    # Bicubic resampling allows gradual DPI changes; averaging
                    # can retain the input size until an integer shrink factor.
                    setattr(options, prefix + 'subsample_method', pymupdf.mupdf.FZ_SUBSAMPLE_BICUBIC)
                    setattr(options, prefix + 'subsample_threshold', round(profile['dpi_target'] * 1.25))
                    setattr(options, prefix + 'subsample_to', profile['dpi_target'])
                document.rewrite_images(options=options)
                document.save(candidate, garbage=3, deflate=True, deflate_images=True,
                              deflate_fonts=True, use_objstms=1, compression_effort=75,
                              raise_on_repair=True)
                check_budget(destination.parent)
                if candidate.stat().st_size < best.stat().st_size:
                    best = candidate
        notify(2, 3, tr('Verifica del PDF compresso…'))
        if not 0 < best.stat().st_size <= MAX_FILE_BYTES:
            raise PDFOperationError(tr('Output non valido o superiore al limite di sicurezza.'))
        check_pdf(best)
        with PdfReader(best) as reader:
            if len(reader.pages) != pages:
                raise PDFOperationError(tr('Il numero di pagine del PDF compresso non corrisponde.'))
        shutil.copyfile(best, destination)
        check_budget(destination.parent)
        notify(3, 3, tr('PDF compresso pronto.'))
        return {'pages': pages}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        baseline.unlink(missing_ok=True)
        candidate.unlink(missing_ok=True)


def main():
    from .security_worker import require_memory_limit
    from .sandbox import in_appcontainer
    from .rpc_worker import checked_path
    import os
    require_memory_limit()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('level', type=int, choices=range(101))
    args = parser.parse_args()
    if os.name == 'nt' and not in_appcontainer():
        return 2
    def send(payload):
        print(json.dumps(payload, ensure_ascii=True), flush=True)
    try:
        root = Path.cwd().resolve()
        source = checked_path(args.source, root)
        destination = checked_path(args.destination, root)
        result = compress_pdf(source, destination, args.level,
                              lambda done, total, label: send(dict(progress=done, total=total, label=label)))
        send({'result': result})
        return 0
    except Exception as exc:
        send({'error': str(exc)[:4096]})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
