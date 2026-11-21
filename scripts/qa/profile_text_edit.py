"""Measure paragraph editing through the same protected broker as the GUI."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
from time import perf_counter

from ape_pdf_studio.core.broker_editor import BrokerEditor
from ape_pdf_studio.core.private_work import PrivateDirectory
from ape_pdf_studio.core.sandbox import wrap_command


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = (("riflusso_paragrafi.pdf", (62, 140)), ("complex_cjk.pdf", (42, 173)))
ITERATIONS = 30
STAGE_PROBE = """
import cProfile
import json
from pathlib import Path
import pstats
import sys

from ape_pdf_studio.core.editor import PDFEditor
from ape_pdf_studio.core.qt_worker_document import Documents
from ape_pdf_studio.core.security_worker import require_memory_limit

require_memory_limit(renderer=True)
point = json.load(sys.stdin)
root = Path.cwd()
profiler = cProfile.Profile()
with profiler:
    editor = PDFEditor(root / 'input.pdf', workspace=root)
    try:
        target = editor.paragraph_at(0, point)
        editor.replace_paragraph(target, target.text[1:])
        view = Documents(editor, root)
        try:
            view.call('qt_info', [editor.path])
        finally:
            view.close()
    finally:
        editor.close()
stages = {
    'copy': ('security.py', 'copy_checked'),
    'parse_page': ('text_edit.py', 'text_spans'),
    'font': ('text_edit.py', 'replacement_font'),
    'layout': ('paragraph_edit.py', '_layout'),
    'rewrite': ('text_rewrite.py', 'rewrite_page'),
    'resource_transplant': ('text_rewrite.py', 'transplant_page'),
    'preflight': ('security.py', 'check_pdf'),
    'renderer_load': ('qt_worker_document.py', 'document'),
}
stats = pstats.Stats(profiler).stats
print(json.dumps({stage: round(sum(timing[3] * 1000 for (file, _, function), timing in stats.items()
                                 if Path(file).name == module and function == name), 2)
                  for stage, (module, name) in stages.items()}))
"""


def timed(operation, *args, **kwargs):
    start = perf_counter()
    value = operation(*args, **kwargs)
    return value, (perf_counter() - start) * 1000


def stats(values):
    ordered = sorted(values)
    return dict(median_ms=round(median(values), 2), p95_ms=round(ordered[math.ceil(.95 * len(values)) - 1], 2))


def phases(source, point):
    with PrivateDirectory() as folder:
        work = Path(folder)
        shutil.copyfile(source, work / 'input.pdf')
        command = wrap_command([sys.executable, '-c', STAGE_PROBE], work, timeout=30000)
        result = subprocess.run(command, cwd=work, input=json.dumps(point), encoding='utf-8',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35, check=True)
        return json.loads(result.stdout)


def profile(name, point):
    source = ROOT / 'examples' / name
    original = sha256(source.read_bytes()).digest()
    editor, opening = timed(BrokerEditor, source)
    try:
        target, selecting = timed(editor.paragraph_at, 0, point)
        draft = target.text[1:]
        args = (target, draft, target.size, target.alignment)
        (first, _), preview = timed(editor.preview_text, *args)
        assert first['fits'], first['message']
        (first, _), raster = timed(editor.preview_text, *args, raster=True)
        assert first['fits'], first['message']
        measurements = {key: [] for key in ('boxes', 'target', 'layout', 'preview_rgb', 'commit', 'renderer_load', 'undo')}
        for i in range(ITERATIONS):
            _, boxes = timed(editor.paragraph_boxes, 0)
            target, selected = timed(editor.paragraph_at, 0, point)
            start, stop = (1, len(target.text)) if i % 2 else (0, len(target.text) - 1)
            args = (target, target.text[start:stop], target.size, target.alignment)
            (_, _), layout = timed(editor.preview_text, *args)
            (info, _), rgb = timed(editor.preview_text, *args, raster=True)
            assert info['fits'], info['message']
            _, commit = timed(editor.replace_paragraph, *args)
            size = editor.path.stat().st_size
            _, reload_time = timed(editor.call, 'qt_info', editor.path)
            _, undo = timed(editor.undo)
            for key, elapsed in zip(measurements, (boxes, selected, layout, rgb, commit, reload_time, undo)):
                measurements[key].append(elapsed)
    finally:
        editor.close()
    assert sha256(source.read_bytes()).digest() == original
    return dict(cold_ms=dict(open=round(opening, 2), selection=round(selecting, 2),
                             layout=round(preview, 2), preview_rgb=round(raster, 2)),
                steady_state={key: stats(values) for key, values in measurements.items()},
                original_bytes=source.stat().st_size, edited_bytes=size,
                nested_phase_cumulative_ms=phases(source, point))


if __name__ == '__main__':
    import platform
    import pymupdf
    from ape_pdf_studio import __version__

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    print(json.dumps(dict(application=__version__, python=platform.python_version(),
                          mupdf=pymupdf.mupdf_version, platform=platform.platform(), iterations=ITERATIONS,
                          examples={name: profile(name, point) for name, point in SAMPLES}), indent=2))
