"""Compile, smoke-test and package APE PDF Studio on native Linux or macOS."""
from __future__ import annotations

import argparse
import ast
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from xml.etree import ElementTree
import zipfile

from compile_app_modules import compile_modules
from release_privacy import check_mupdf_native, check_paths, local_paths, public_tar_owner

ROOT = Path(__file__).resolve().parents[2]
APP = 'APE PDF Studio'
IDENTIFIER = 'it.giancagis.apepdfstudio'
PACKAGE = 'ape-pdf-studio'


def run(*args, **kwargs):
    print('==>', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def version():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    value = project['project']['version']
    tree = ast.parse((ROOT / 'src/ape_pdf_studio/__init__.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == '__version__' for item in node.targets):
            if ast.literal_eval(node.value) == value and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
                return value
    raise RuntimeError('pyproject.toml and the application version do not match.')


def linux_package(bundle, output, stage, app_version, machine, *, skip_deb=False):
    stem = f'APEPDFStudio-{app_version}-linux-{machine}'
    for name, command in (('ape-pdf-studio', ''), ('ape-pdf-cli', ' --ape-cli')):
        launcher = bundle / name
        launcher.write_text('#!/bin/sh\nbase="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1\n'
                            f'exec "$base/APEPDFStudio"{command} "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    with tarfile.open(output / (stem + '.tar.gz'), 'w:gz') as archive:
        archive.add(bundle, arcname='APEPDFStudio', filter=public_tar_owner)
    if skip_deb:
        return
    package = stage / 'deb'
    target = package / 'opt/ape-pdf-studio'
    shutil.copytree(bundle, target, symlinks=True)
    for name in ('ape-pdf-studio', 'ape-pdf-cli'):
        wrapper = package / 'usr/bin' / name
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(f'#!/bin/sh\nexec /opt/ape-pdf-studio/{name} "$@"\n', encoding='utf-8')
        wrapper.chmod(0o755)
    desktop = package / 'usr/share/applications' / (IDENTIFIER + '.desktop')
    desktop.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'build_tools/linux' / desktop.name, desktop)
    icon = package / 'usr/share/pixmaps' / (IDENTIFIER + '.png')
    icon.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'src/ape_pdf_studio/assets/app-icon.png', icon)
    copyright_file = package / 'usr/share/doc' / PACKAGE / 'copyright'
    copyright_file.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'LICENSE', copyright_file)
    control = package / 'DEBIAN/control'
    control.parent.mkdir()
    libc = os.confstr('CS_GNU_LIBC_VERSION')
    if not libc or not re.fullmatch(r'glibc [0-9.]+', libc):
        raise RuntimeError('The Linux installer requires a glibc build host.')
    architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}[machine]
    control.write_text(
        f'Package: {PACKAGE}\nVersion: {app_version}\nArchitecture: {architecture}\n'
        'Maintainer: GiancaGIS\nSection: graphics\nPriority: optional\n'
        f'Depends: libc6 (>= {libc.split()[1]}), libstdc++6, libgl1, libegl1, libx11-xcb1, libxcb-cursor0, '
        'libxkbcommon-x11-0, libxcb-icccm4, libxcb-keysyms1, libxcb-render-util0, libxcb-xkb1\n'
        'Description: APE PDF Studio\n Local PDF reading and editing application.\n',
        encoding='utf-8')
    installer = output / f'{PACKAGE}_{app_version}_{architecture}.deb'
    run('dpkg-deb', '--root-owner-group', '--build', package, installer)
    run('dpkg-deb', '--info', installer)
    with tempfile.TemporaryDirectory(prefix='deb-privacy-', dir=stage) as extracted:
        run('dpkg-deb', '--raw-extract', installer, extracted)
        check_paths([Path(extracted)], local_paths())


def macos_package(bundle, output, stage, app_version, machine, installer_identity):
    stem = f'APEPDFStudio-{app_version}-macOS-{machine}'
    run('codesign', '--verify', '--deep', '--strict', bundle)
    run('ditto', '-c', '-k', '--keepParent', bundle, output / (stem + '.zip'))
    payload = stage / 'applications'
    payload.mkdir()
    run('ditto', bundle, payload / bundle.name)
    component = stage / 'component.pkg'
    run('pkgbuild', '--root', payload, '--install-location', '/Applications',
        '--identifier', IDENTIFIER, '--version', app_version, component)
    distribution = stage / 'Distribution.xml'
    run('productbuild', '--synthesize', '--package', component, distribution)
    resources = stage / 'resources'
    resources.mkdir()
    shutil.copyfile(ROOT / 'LICENSE', resources / 'LICENSE.txt')
    tree = ElementTree.parse(distribution)
    ElementTree.SubElement(tree.getroot(), 'title').text = APP
    ElementTree.SubElement(tree.getroot(), 'license', {'file': 'LICENSE.txt', 'mime-type': 'text/plain'})
    tree.write(distribution, encoding='utf-8', xml_declaration=True)
    sign = ['--sign', installer_identity] if installer_identity else []
    installer = output / (stem + '.pkg')
    run('productbuild', *sign, '--distribution', distribution, '--resources', resources,
        '--package-path', stage, installer)
    if installer_identity:
        run('pkgutil', '--check-signature', installer)


def source_assets(output, target, app_version, machine):
    import pymupdf
    from prepare_release import digest, project_files
    stem = f'APEPDFStudio-{app_version}-{target}-{machine}'
    inventory = {'platform': platform.platform(), 'python': platform.python_version(),
                 'MuPDF': pymupdf.mupdf_version,
                 'environment': {dist.metadata['Name']: dist.version for dist in metadata.distributions()
                                 if dist.metadata.get('Name')}}
    (output / (stem + '-build-environment.json')).write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n',
                                                             encoding='utf-8')
    with zipfile.ZipFile(output / (stem + '-Source.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in project_files():
            archive.write(path, f'APE-PDF-Studio-{app_version}/' + path.relative_to(ROOT).as_posix())
    checksums = [f'{digest(path)}  {path.name}' for path in sorted(output.iterdir())]
    (output / (stem + '-SHA256SUMS.txt')).write_text('\n'.join(checksums) + '\n', encoding='ascii')


def build(args):
    target = args.target
    expected_platform = {'linux': 'linux', 'macos': 'darwin'}[target]
    if sys.platform != expected_platform:
        raise RuntimeError(f'{target} packaging must run on its native host.')
    if sys.version_info < (3, 11) or sys.prefix == sys.base_prefix:
        raise RuntimeError('Use a prepared Python 3.11+ virtualenv.')
    machine = platform.machine().lower()
    architectures = {'linux': {'x86_64', 'aarch64'}, 'macos': {'x86_64', 'arm64'}}
    if machine not in architectures[target]:
        raise RuntimeError(f'Unsupported {target} architecture: {machine}')
    tools = ['dpkg-deb'] if target == 'linux' and not args.skip_deb else []
    if target == 'macos':
        tools = ['ditto', 'codesign', 'pkgbuild', 'productbuild', 'pkgutil']
    for tool in tools:
        if not shutil.which(tool):
            raise RuntimeError('Missing native packaging tool: ' + tool)

    import pymupdf
    from ape_pdf_studio import __version__
    app_version = version()
    if __version__ != app_version or pymupdf.mupdf_version != '1.28.5':
        raise RuntimeError('Prepare this checkout and corrected MuPDF with prepare_unix.sh.')
    check_mupdf_native()
    work = ROOT / 'build' / target
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='package-', dir=work) as directory:
        stage = Path(directory)
        compiled = stage / 'cython'
        compile_modules(compiled, jobs=args.jobs)
        frozen = stage / 'bundle'
        env = {**os.environ, 'APE_BUILD_VERSION': app_version, 'APE_UNIX_COMPILED_DIR': str(compiled),
               'APE_MACOS_CODESIGN_IDENTITY': args.codesign_identity or ''}
        run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', frozen,
            '--workpath', stage / 'pyinstaller', ROOT / 'build_tools/common/APEPDFStudio-unix.spec', env=env)
        bundle = frozen / (APP + '.app' if target == 'macos' else 'APEPDFStudio')
        executable = bundle / ('Contents/MacOS/APEPDFStudio' if target == 'macos' else 'APEPDFStudio')
        check_paths([bundle], local_paths())
        run(sys.executable, ROOT / 'build_tools/checks/smoke_unix_bundle.py', executable)
        artifacts = stage / 'artifacts'
        artifacts.mkdir()
        if target == 'macos':
            macos_package(bundle, artifacts, stage, app_version, machine, args.installer_identity)
        else:
            linux_package(bundle, artifacts, stage, app_version, machine, skip_deb=args.skip_deb)
        source_assets(artifacts, target, app_version, machine)
        destination = ROOT / 'dist' / target
        destination.mkdir(parents=True, exist_ok=True)
        for path in sorted(artifacts.iterdir()):
            path.replace(destination / path.name)
        print('Packages:', destination, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['linux', 'macos'])
    parser.add_argument('--python', type=Path, help='Path to the prepared virtualenv interpreter')
    parser.add_argument('--skip-deb', action='store_true', help='Linux only: create the portable archive without the .deb')
    parser.add_argument('--jobs', type=int, choices=range(1, 17), default=min(4, os.cpu_count() or 1), metavar='1..16')
    parser.add_argument('--codesign-identity', help='macOS Developer ID Application identity')
    parser.add_argument('--installer-identity', help='macOS Developer ID Installer identity')
    args = parser.parse_args()
    if args.target == 'macos' and args.skip_deb:
        parser.error('--skip-deb applies only to Linux.')
    if args.target == 'linux' and (args.codesign_identity or args.installer_identity):
        parser.error('The selected build option does not apply to this platform.')
    if args.python:
        python = args.python.absolute()
        if python != Path(sys.executable).absolute():
            # Preserve the virtualenv path even when its interpreter is a symlink.
            arguments = [sys.executable, __file__, args.target, '--jobs', str(args.jobs)]
            if args.skip_deb:
                arguments.append('--skip-deb')
            for option, value in (('--codesign-identity', args.codesign_identity), ('--installer-identity', args.installer_identity)):
                if value:
                    arguments.extend((option, value))
            return subprocess.run([str(python), *arguments[1:]], cwd=ROOT).returncode
    try:
        build(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Build failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
