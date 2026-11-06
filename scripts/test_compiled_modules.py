"""Check every staged PYD and its real imports without loading PDF documents."""
import importlib
import json
import os
from pathlib import Path
import sys

os.environ['QT_QPA_PLATFORM']='offscreen'
root=Path(__file__).resolve().parents[1]
build=root/'build/windows/cython'
sys.path.insert(0,str(build/'lib'))
records=json.loads((build/'compiled-modules.json').read_text(encoding='utf-8'))
for name in records['modules']:
    if name.endswith('.__main__'):
        continue  # This entry point intentionally starts the complete GUI.
    module=importlib.import_module(name.removesuffix('.__init__'))
    if Path(module.__file__).suffix.lower()!='.pyd':
        raise RuntimeError('Unexpected source/bytecode module: '+name)
from PySide6.QtWidgets import QApplication
from ape_pdf_studio.core.rpc_codec import dumps,read
from ape_pdf_studio.core.security import SecurityReport
import io
report=SecurityReport(('javascript',),True)
assert read(io.BytesIO(dumps(report)))==report
from ape_pdf_studio.ui.main_window import MainWindow
from PySide6.QtCore import QSettings
import tempfile
with tempfile.TemporaryDirectory(dir=root/'build',prefix='compiled-qt-') as temporary:
    app=QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,temporary)
    window=MainWindow()
    window.show()
    app.processEvents()
    assert not window.windowIcon().isNull()
    window.close()
    window.deleteLater()
    app.processEvents()
print(f'Native imports ({len(records["modules"])} modules), typed IPC and Qt construction passed.')
