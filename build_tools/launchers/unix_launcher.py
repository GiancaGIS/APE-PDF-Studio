"""Entry point for Unix GUI, CLI and PDF workers in one native bundle."""
import os
import sys


def main():
    mode = sys.argv[1:2]
    if mode == ['--ape-worker']:
        del sys.argv[1]
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        # BLAS thread pools multiply virtual memory use inside bounded workers.
        os.environ['OPENBLAS_NUM_THREADS'] = '1'
        from ape_pdf_studio.core.worker_entry import main as worker_main
        return worker_main()
    if mode == ['--ape-cli']:
        del sys.argv[1]
        from ape_pdf_studio.core.cli import main as cli_main
        return cli_main()
    from ape_pdf_studio.app import main as app_main
    return app_main()


if __name__ == '__main__':
    raise SystemExit(main())
