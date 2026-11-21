"""Keep local build paths out of the frozen payload and verify its contents."""
from __future__ import annotations

import argparse
import ast
from importlib.metadata import distribution
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
import zipfile


BUILD_MODULE = 'pymupdf._build'
NATIVE_PROFILE = re.compile(rb'(?i)(?:[a-z]:[\\/]+users[\\/]+|/mnt/[a-z]/users/|/(?:home|Users)/)[\w.-]+[\\/]')
EXECUTABLES = {'apepdfstudio.exe', 'apepdfworker.exe', 'ape-pdf-cli.exe', 'apepdfstudio'}


def local_paths(*extra):
    roots = (str(Path.home()), os.environ.get('USERPROFILE'), os.environ.get('APE_PRIVATE_BUILD_ROOT'), *extra)
    paths = set()
    for root in roots:
        if not root or len(root.rstrip('/\\')) < 5:
            continue
        slash = root.rstrip('/\\').replace('\\', '/')
        variants = {slash, quote(slash, safe='/:'), quote(slash, safe='/')}
        if len(slash) > 2 and slash[1] == ':':
            backslash = slash.replace('/', '\\')
            variants.update((backslash, backslash.replace('\\', '\\\\'), '/mnt/' + slash[0].lower() + slash[2:]))
        for name in variants:
            for encoding in ('utf-8', 'utf-16le'):
                paths.add(name.encode(encoding).lower())
                paths.add(name.lower().encode(encoding))
    return tuple(sorted(paths))


def public_build_metadata(source):
    """Retain runtime/version fields while removing source locations and git diagnostics."""
    tree = ast.parse(source)
    replacements = {
        'mupdf_location': 'bundled MuPDF',
        'pymupdf_git_branch': None,
        'pymupdf_git_comment': None,
        'pymupdf_git_diff': None,
        'pymupdf_git_diff_n': 0,
    }
    seen_location = False
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id in replacements:
                statement.value = ast.Constant(replacements[target.id])
                seen_location |= target.id == 'mupdf_location'
    if not seen_location:
        raise RuntimeError('Unexpected PyMuPDF build metadata format')
    return ast.unparse(tree) + '\n'


def private_metadata(name):
    path = Path(name.replace('\\', '/'))
    return path.name.lower() == 'direct_url.json' and path.parent.name.lower().endswith('.dist-info')


def public_tar_owner(entry):
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = 'root'
    return entry


def public_tables(pure, datas, directory):
    matches = [(source, kind) for module, source, kind in pure if module == BUILD_MODULE]
    if len(matches) != 1:
        raise RuntimeError('Expected one PyMuPDF build metadata module in the frozen application')
    source, kind = matches[0]
    directory.mkdir(parents=True, exist_ok=True)
    replacement = directory / '_build.py'
    replacement.write_text(public_build_metadata(Path(source).read_text(encoding='utf-8')), encoding='utf-8')
    # A fresh list makes PyInstaller compile this source without using the
    # original Analysis code cache for the private metadata module.
    pure = [(module, src, typecode) for module, src, typecode in pure if module != BUILD_MODULE]
    pure.append((BUILD_MODULE, str(replacement), kind))
    return pure, [entry for entry in datas if not private_metadata(entry[0])]


def has_local_path(data, markers):
    normalized = data.lower()
    return any(marker in normalized for marker in markers)


def file_has_local_path(path, markers):
    overlap = max(map(len, markers), default=1) - 1
    tail = b''
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            combined = tail + chunk
            if has_local_path(combined, markers):
                return True
            tail = combined[-overlap:] if overlap else b''
    return False


def embedded_findings(path, markers):
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(path))
    for name, (_, _, _, _, kind) in archive.toc.items():
        if kind == 'z':
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                payload = pyz.extract(module, raw=True)
                if payload and has_local_path(payload, markers):
                    yield f'PYZ:{module}'
        elif kind != 'o':
            payload = archive.extract(name)
            if has_local_path(payload, markers):
                yield name
            if name.lower().endswith('.zip'):
                import io
                with zipfile.ZipFile(io.BytesIO(payload)) as archive_zip:
                    yield from zip_findings(archive_zip, markers)


def zip_findings(archive, markers):
    for name in archive.namelist():
        if private_metadata(name) or has_local_path(archive.read(name), markers):
            yield name


def check_paths(paths, markers):
    findings = []
    for root in paths:
        if not root.exists():
            raise RuntimeError('Privacy check input is missing: ' + root.name)
        for path in root.rglob('*') if root.is_dir() else (root,):
            name = path.relative_to(root).as_posix() if root.is_dir() else path.name
            if path.is_symlink():
                if has_local_path(os.fsencode(os.readlink(path)), markers):
                    findings.append(name)
                continue
            if not path.is_file():
                continue
            if private_metadata(name) or file_has_local_path(path, markers):
                findings.append(name)
            if path.name.lower() in EXECUTABLES:
                findings.extend(f'{name}:{entry}' for entry in embedded_findings(path, markers))
            elif path.suffix.lower() == '.zip':
                with zipfile.ZipFile(path) as archive:
                    findings.extend(f'{name}:{entry}' for entry in zip_findings(archive, markers))
    if findings:
        raise RuntimeError('Local build information in distribution files: ' + ', '.join(sorted(set(findings))))


def check_mupdf_native():
    import pymupdf

    if pymupdf.mupdf_version != '1.28.5':
        raise RuntimeError('The corrected MuPDF 1.28.5 runtime is required')
    package = distribution('PyMuPDF')
    native = [package.locate_file(name) for name in package.files or ()
              if '.so' in name.name or name.suffix.lower() in ('.dylib', '.dll', '.pyd')]
    if not native:
        raise RuntimeError('PyMuPDF native library files were not found')
    markers = local_paths()
    for path in native:
        check_native_data(path.name, path.read_bytes(), markers)


def check_native_data(name, data, markers):
    if has_local_path(data, markers) or NATIVE_PROFILE.search(data):
        raise RuntimeError('Local build path in PyMuPDF native library: ' + name)


def check_mupdf_wheel(path):
    markers = local_paths()
    with zipfile.ZipFile(path) as wheel:
        native = [name for name in wheel.namelist() if name.lower().endswith(('.pyd', '.dll'))]
        if not native:
            raise RuntimeError('PyMuPDF native library files were not found in the wheel')
        for name in native:
            check_native_data(name, wheel.read(name), markers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--mupdf-native', action='store_true', help='check the installed native MuPDF libraries')
    parser.add_argument('--mupdf-wheel', type=Path, help='check the native DLLs in a Windows PyMuPDF wheel')
    parser.add_argument('--quiet', action='store_true', help='report the result only through the exit status')
    args = parser.parse_args()
    if not args.paths and not args.mupdf_native and args.mupdf_wheel is None:
        parser.error('Specify a bundle, archive or installer to verify')
    try:
        if args.mupdf_native:
            check_mupdf_native()
        if args.mupdf_wheel is not None:
            check_mupdf_wheel(args.mupdf_wheel)
        check_paths(args.paths, local_paths())
    except (ImportError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        if not args.quiet:
            print(f'Privacy check failed: {exc}', file=sys.stderr)
        return 1
    if not args.quiet:
        print('Build privacy check passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
