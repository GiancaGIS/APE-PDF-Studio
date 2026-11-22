"""Inspect or prepare the pinned Windows build environment, using a venv only."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def requirements(root):
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
    lines = (root / 'requirements-release.txt').read_text(encoding='utf-8')
    pinned = [Requirement(line.strip()) for line in lines.splitlines() if line.strip() and not line.startswith('#')]
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    return pinned + [Requirement(value) for value in project['build-system']['requires']]


def required_python(root):
    text = (root / 'requirements-release.txt').read_text(encoding='utf-8')
    match = re.search(r'CPython (\d+\.\d+\.\d+)', text)
    if not match:
        raise RuntimeError('Pinned CPython version is missing from requirements-release.txt.')
    return match[1]


def inspect_environment(root=ROOT):
    root = root.resolve()
    issues = []
    for requirement in requirements(root):
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            found = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            issues.append(f'{requirement.name}: not installed')
        else:
            if not requirement.specifier.contains(found):
                issues.append(f'{requirement.name}: {found}, required {requirement.specifier}')
    try:
        import pymupdf
        if pymupdf.mupdf_version != '1.28.5':
            issues.append('Native MuPDF: expected corrected version 1.28.5')
    except ImportError:
        issues.append('Native MuPDF: not installed')
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    try:
        package = metadata.distribution(project['name'])
        direct = json.loads(package.read_text('direct_url.json') or '{}')
        expected = root.as_uri().rstrip('/')
        actual = direct.get('url', '').rstrip('/')
        if os.name == 'nt':
            actual, expected = actual.casefold(), expected.casefold()
        if actual != expected or not direct.get('dir_info', {}).get('editable') or package.version != project['version']:
            issues.append('Application editable metadata does not match this checkout')
    except (metadata.PackageNotFoundError, ValueError, OSError):
        issues.append('Application editable metadata is missing')
    return dict(python=sys.executable, prefix=sys.prefix, version='.'.join(map(str, sys.version_info[:3])),
                virtualenv=sys.prefix != sys.base_prefix, required_python=required_python(root), issues=list(dict.fromkeys(issues)))


def run(command, root):
    subprocess.run(list(map(str, command)), cwd=root, check=True)


def capture(command, root):
    return json.loads(subprocess.check_output(list(map(str, command)), cwd=root, text=True))


def prepare(root=ROOT, *, check_only=False):
    if os.name != 'nt':
        raise RuntimeError('This procedure prepares Windows x64 builds only. See docs/distribution/README.md.')
    root = root.resolve()
    python = Path(sys.executable).absolute() if sys.prefix != sys.base_prefix else root / '.venv-build/Scripts/python.exe'
    if not python.is_file():
        if check_only:
            raise RuntimeError('Build virtualenv missing; run without -SkipDependencyInstall first.')
        run([sys.executable, '-I', '-m', 'venv', root / '.venv-build'], root)
    identity = capture([python, '-I', '-c',
        'import json,sys,struct;print(json.dumps(dict(prefix=sys.prefix,base=sys.base_prefix,version=".".join(map(str,sys.version_info[:3])),bits=8*struct.calcsize("P"))))'], root)
    if Path(identity['prefix']).resolve() == Path(identity['base']).resolve():
        raise RuntimeError('Refusing to install build dependencies into global Python.')
    if identity['bits'] != 64 or identity['version'] != required_python(root):
        raise RuntimeError(f'Build requires CPython {required_python(root)} x64; found {identity["version"]} ({identity["bits"]} bit).')
    inspect = [python, '-I', Path(__file__).resolve(), '--inspect', '--project-root', root]
    information = capture(inspect, root)
    if information['issues']:
        if check_only:
            raise RuntimeError('Build environment needs preparation: ' + '; '.join(information['issues']))
        print('Preparing pinned dependencies in virtualenv:', python, flush=True)
        # Never substitute an upstream wheel for the corrected native engine.
        run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
             root / 'build_tools/windows/build_patched_mupdf.ps1', '-PythonExe', python], root)
        run([python, '-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
             '--find-links', root / '.dev-deps/patched-wheels', '-r', root / 'requirements-release.txt', '-e', str(root) + '[dev]'], root)
        # pytest puts src first; refresh stale egg-info there as well as dist-info.
        run([python, '-I', '-c', "from setuptools import setup; setup(script_args=['egg_info'])"], root)
        information = capture(inspect, root)
        if information['issues']:
            raise RuntimeError('Build environment is still incompatible: ' + '; '.join(information['issues']))
    run([python, '-I', '-m', 'pip', 'check'], root)
    return information


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--project-root', type=Path, default=ROOT)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    try:
        information = inspect_environment(args.project_root) if args.inspect else prepare(args.project_root, check_only=args.check_only)
        if args.inspect:
            print(json.dumps(information))
        elif args.result:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(information), encoding='utf-8')
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print('[ERROR] Build environment preparation failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
