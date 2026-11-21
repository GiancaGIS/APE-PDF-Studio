"""Exercise the real bundled CLI, preventive check and Qt PDF worker."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from ape_pdf_studio.core.broker_editor import BrokerEditor


def smoke(executable):
    executable = executable.resolve(strict=True)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

    def worker(kind, work, *arguments, **limits):
        return [str(executable), '--ape-worker', kind, *map(str, arguments)]

    with tempfile.TemporaryDirectory(prefix='ape-unix-smoke-') as directory:
        folder = Path(directory)
        source = folder / 'Source with spaces.pdf'
        output = folder / 'Rotated.pdf'
        writer = PdfWriter()
        writer.add_blank_page(200, 300)
        writer.write(source)
        original = source.read_bytes()
        command = [str(executable), '--ape-cli']
        subprocess.run([*command, str(source), 'rotate', '1', '-o', str(output)],
                       cwd=folder, timeout=60, check=True)
        with PdfReader(output) as reader:
            assert reader.pages[0].rotation == 90
        assert source.read_bytes() == original
        # Broker can validate the exported raster from the frozen Qt worker.
        with patch('ape_pdf_studio.core.broker_editor.sandbox_command', worker):
            editor = BrokerEditor(source)
            try:
                assert editor.call('qt_info', editor.path)['sizes'] == [(200, 300)]
                raster = editor.path.parent / 'smoke.rgba'
                editor.call('qt_render', editor.path, 0, 40, 60, {}, raster)
                assert raster.stat().st_size == 40 * 60 * 4
            finally:
                editor.close()
        writer.root_object[NameObject('/OpenAction')] = DictionaryObject({NameObject('/S'): NameObject('/Launch')})
        blocked = folder / 'Blocked action.pdf'
        writer.write(blocked)
        result = subprocess.run([*command, str(blocked), 'info'], cwd=folder, timeout=30, capture_output=True)
        assert result.returncode == 2 and b'PDF' in result.stderr, result.stderr
    print('Bundled CLI, preventive check and Qt PDF rendering passed.')


if __name__ == '__main__':
    smoke(Path(sys.argv[1]))
