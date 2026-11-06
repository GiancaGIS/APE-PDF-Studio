"""Compile every application module, leaving the editable source tree intact."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]


def modules(source):
    result = {}
    for path in sorted(source.rglob('*.py')):
        parts = path.relative_to(source.parent).with_suffix('').parts
        result['.'.join(parts)] = path
    if not result or 'ape_pdf_studio.__init__' not in result:
        raise RuntimeError('Application sources are missing')
    return result


def imports(paths):
    """Cython hides Python imports from PyInstaller's bytecode analysis."""
    names = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.Import):
                names.update(part.name for part in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names.add(node.module)
    return sorted(names)


def compile_modules(destination, jobs=4):
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        raise RuntimeError('PYD builds require Windows x64')
    import Cython
    from Cython.Build import cythonize
    from setuptools import Extension, setup
    destination = destination.resolve()
    allowed = (ROOT / 'build').resolve()
    if not destination.is_relative_to(allowed) or destination == allowed:
        raise ValueError('Compilation output must be a directory within build/')
    destination.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'src/ape_pdf_studio'
    records = modules(source)
    library = destination / 'lib'
    extensions = [Extension(name, [path.relative_to(ROOT).as_posix()],
                            extra_compile_args=['/O2', '/guard:cf'],
                            extra_link_args=['/DYNAMICBASE', '/NXCOMPAT', '/GUARD:CF'])
                  for name, path in records.items()]
    previous = Path.cwd()
    try:
        os.chdir(ROOT)
        extensions = cythonize(extensions, build_dir=str(destination / 'generated'), nthreads=jobs,
            compiler_directives=dict(language_level=3, annotation_typing=False, infer_types=False,
                                     binding=True, emit_code_comments=False))
        setup(name='ape-native-build', ext_modules=extensions,
              script_args=['build_ext', '--build-lib', str(library), '--build-temp', str(destination/'objects'),
                           '--parallel', str(jobs)])
    finally:
        os.chdir(previous)
    suffix = sysconfig.get_config_var('EXT_SUFFIX')
    expected = {Path(*name.split('.')).with_suffix(suffix).as_posix() for name in records}
    actual = {path.relative_to(library).as_posix() for path in library.rglob('*.pyd')}
    if actual != expected:
        raise RuntimeError('Compiled module set differs from source inventory')
    for folder in ('assets', 'locales'):
        shutil.copytree(source/folder, library/'ape_pdf_studio'/folder, dirs_exist_ok=True)
    inventory = dict(schema=1, python=sys.implementation.cache_tag, cython=Cython.__version__,
                     suffix=suffix, modules=sorted(records), hiddenimports=imports(records.values()),
                     sources={path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in records.values()})
    (destination/'compiled-modules.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    print(f'Compiled {len(records)} application modules into {library}', flush=True)
    return library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/windows/cython')
    parser.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error('--jobs must be between 1 and 16')
    compile_modules(args.build_dir, args.jobs)


if __name__ == '__main__':
    main()
