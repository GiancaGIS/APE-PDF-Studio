"""Assemble and verify a type-2 AppImage from the existing Linux bundle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from prepare_release import digest, download
from release_privacy import check_paths, file_has_local_path, local_paths

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ID = 'it.giancagis.apepdfstudio'
RUNTIME_COMMIT = 'dd6cebedcbddde9c82f89b011e8e1d40b6e43868'
RUNTIME_RECIPE = ROOT / 'build_tools/linux/appimage'
RUNTIME_RECIPE_FILES = ('Dockerfile', 'build-runtime.sh')
SOURCES = (
    (f'type2-runtime-{RUNTIME_COMMIT}', '.tar.gz',
     f'https://codeload.github.com/AppImage/type2-runtime/tar.gz/{RUNTIME_COMMIT}',
     'f5fec23be76e50e2445ed2d018bac49b367490fc483c62c4637e99ec705d27ba', ('LICENSE',)),
    ('fuse-3.15.0', '.tar.xz', 'https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz',
     '70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0', ('LICENSE', 'LGPL2.txt')),
    ('squashfuse-0.5.2', '.tar.gz', 'https://codeload.github.com/vasi/squashfuse/tar.gz/refs/tags/0.5.2',
     'db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3', ('LICENSE',)),
    ('zstd-1.5.7', '.tar.gz', 'https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz',
     'eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3', ('LICENSE',)),
)


def runtime_payload(stage, machine, jobs):
    """Compile an AppImage launcher from fixed sources and record its provenance."""
    archives = []
    source_records = []
    for prefix, extension, source_url, source_hash, notices in SOURCES:
        filename = prefix + extension
        archives.append(download(source_url, filename, source_hash))
        source_records.append({'filename': filename, 'url': source_url, 'sha256': source_hash,
                               'notices': [f'{prefix}-{notice}' for notice in notices]})

    # The Docker context contains only the recipe. The compiler container gets
    # verified source archives, an empty output directory, and no network.
    with tempfile.TemporaryDirectory(prefix='ape-appimage-runtime-') as directory:
        work = Path(directory)
        context, inputs, output = (work / name for name in ('context', 'inputs', 'out'))
        for folder in (context, inputs, output):
            folder.mkdir()
        dockerfile = RUNTIME_RECIPE / 'Dockerfile'
        script = RUNTIME_RECIPE / 'build-runtime.sh'
        shutil.copyfile(dockerfile, context / dockerfile.name)
        shutil.copyfile(script, inputs / script.name)
        for archive in archives:
            shutil.copyfile(archive, inputs / archive.name)
        image_file = work / 'builder-image-id'
        subprocess.run(['docker', 'build', '--iidfile', image_file, context], check=True)
        builder = image_file.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', builder):
            raise RuntimeError('Unexpected AppImage builder image ID')
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                        '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,exec', '--env', 'HOME=/tmp',
                        '--user', f'{os.getuid()}:{os.getgid()}', '-v', f'{inputs}:/inputs:ro',
                        '-v', f'{output}:/out', builder, 'sh', '/inputs/build-runtime.sh', str(jobs)], check=True)
        runtime = stage / 'appimage-runtime'
        shutil.copyfile(output / 'runtime', runtime)
        runtime.chmod(0o755)
        dynamic_libraries = (output / 'dynamic-libraries.txt').read_text(encoding='ascii').splitlines()
        packages = dict(row.split('\t', 1) for row in (output / 'build-packages.tsv').read_text(encoding='utf-8').splitlines())

    record = {'runtime': {'commit': RUNTIME_COMMIT, 'architecture': machine, 'sha256': digest(runtime),
                          'static_libraries': ['libfuse', 'Squashfuse', 'Zstandard'],
                          'dynamic_libraries': dynamic_libraries},
              'builder': {'base_image': dockerfile.read_text(encoding='utf-8').splitlines()[0].removeprefix('FROM '),
                          'image_id': builder, 'packages': packages,
                          'recipes': {path.name: digest(path) for path in (dockerfile, script)}},
              'sources': source_records}
    return runtime, archives, record


def build_appimage(bundle, output, stage, version, machine, runtime_inputs, jobs):
    runtime, archives, record = runtime_inputs
    appdir = stage / 'APEPDFStudio.AppDir'
    target = appdir / 'usr/lib/ape-pdf-studio'
    shutil.copytree(bundle, target, symlinks=True)
    launcher = appdir / 'AppRun'
    launcher.write_text('#!/bin/sh\nbase="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1\n'
                        'exec "$base/usr/lib/ape-pdf-studio/APEPDFStudio" "$@"\n', encoding='utf-8')
    launcher.chmod(0o755)
    desktop = f'{DESKTOP_ID}.desktop'
    icon = f'{DESKTOP_ID}.png'
    shutil.copyfile(ROOT / 'build_tools/linux' / desktop, appdir / desktop)
    shutil.copyfile(ROOT / 'src/ape_pdf_studio/assets/app-icon.png', appdir / icon)
    (appdir / '.DirIcon').symlink_to(icon)

    legal = appdir / 'usr/share/doc/ape-pdf-studio/appimage-runtime'
    legal.mkdir(parents=True)
    for archive, (prefix, _, _, _, notices) in zip(archives, SOURCES, strict=True):
        with tarfile.open(archive) as source:
            for name in notices:
                with source.extractfile(f'{prefix}/{name}') as stream:
                    (legal / f'{prefix}-{name}').write_bytes(stream.read())
    (legal / 'sources.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')

    image = output / f'APEPDFStudio-{version}-linux-{machine}.AppImage'
    squashfs = stage / 'appimage.squashfs'
    subprocess.run(['mksquashfs', appdir, squashfs, '-noappend', '-all-root', '-no-xattrs', '-no-progress',
                    '-processors', str(jobs), '-comp', 'zstd'], check=True,
                   env={**os.environ, 'SOURCE_DATE_EPOCH': '0'})
    with image.open('xb') as assembled:
        with runtime.open('rb') as executable:
            shutil.copyfileobj(executable, assembled)
        with squashfs.open('rb') as payload:
            shutil.copyfileobj(payload, assembled)
    image.chmod(0o755)

    # The runtime is verified before assembly. Inspect the generated image and
    # its actual compressed payload in addition to the initial bundle scan.
    markers = local_paths(str(ROOT))
    if file_has_local_path(image, markers):
        raise RuntimeError('Local build information in AppImage bytes')
    offset = str(runtime.stat().st_size)
    listing = subprocess.check_output(['unsquashfs', '-lln', '-offset', offset, image], text=True)
    entries = listing.splitlines()
    if not entries or any(line.split(maxsplit=2)[1:2] != ['0/0'] for line in entries):
        raise RuntimeError('AppImage filesystem has non-neutral ownership')
    extracted = stage / 'appimage-privacy'
    subprocess.run(['unsquashfs', '-no-progress', '-processors', str(jobs), '-offset', offset,
                    '-d', extracted, image], check=True)
    check_paths([extracted], markers)

    env = {**os.environ, 'APPIMAGE_EXTRACT_AND_RUN': '1'}
    subprocess.run([image, '--ape-cli', '--help'], env=env, timeout=90, check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, ROOT / 'build_tools/checks/smoke_unix_bundle.py', extracted / 'AppRun'], check=True)
