"""Validate the build interpreter and return the canonical release version."""
import ast
from pathlib import Path
import platform
import struct
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Serve Python 3.11 o successivo.")
if sys.platform != "win32" or struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
    raise SystemExit("Serve Python Windows x64.")

import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
initialization = ast.parse((root / "src/ape_pdf_studio/__init__.py").read_text(encoding="utf-8"))
package_version = next(ast.literal_eval(node.value) for node in initialization.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
if package_version != version:
    raise SystemExit("Allinea __version__ e pyproject.toml prima di creare l'installer.")
print(version)
