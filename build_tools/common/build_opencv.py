"""Prepare and verify the OpenCV wheel used by the native installers."""
from __future__ import annotations

import argparse
from copy import copy
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = 'opencv-python-headless'
SOURCES = {
    'win32': ('5.0.0.93', 'b82f9831daab90b725c7c1ee1b36cb5732c367096ac76d119e64e14eb70d5f3c'),
    'linux': ('5.1.0.95', '9002add14b121b9a0d64dcaff48e5cac69a7987efcf2847f7db075c2d3b9633b'),
    'darwin': ('5.1.0.95', '9002add14b121b9a0d64dcaff48e5cac69a7987efcf2847f7db075c2d3b9633b'),
}
OPTIONS = (
    '-DBUILD_LIST=core,imgproc,imgcodecs,python3',
    '-DWITH_IPP=OFF', '-DBUILD_IPP_IW=OFF', '-DWITH_FFMPEG=OFF',
    '-DWITH_AVIF=OFF', '-DWITH_GSTREAMER=OFF', '-DWITH_ITT=OFF',
    '-DWITH_LAPACK=OFF', '-DWITH_OPENCL=OFF', '-DBUILD_TESTS=OFF',
)
TOOLS = ('cmake==3.31.10', 'ninja==1.13.0', 'scikit-build==0.18.1',
         'setuptools==69.5.1', 'wheel==0.48.0', 'numpy==2.3.2')
RECORD = 'ape-opencv-build.json'
UNWANTED_LIBRARY = re.compile(
    r'^(opencv_videoio_ffmpeg|ippicv|libippicv|lib(?:avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)[.\-])',
    re.IGNORECASE,
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def recipe():
    version, checksum = SOURCES[sys.platform]
    cmake_options = list(OPTIONS)
    if sys.platform != 'win32':
        cmake_options.append('-DCMAKE_SKIP_INSTALL_RPATH=ON')
    return {'version': version, 'source': f'opencv_python_headless-{version}.tar.gz',
            'source_sha256': checksum, 'cmake_options': cmake_options,
            'build_requirements': list(TOOLS), 'platform': sysconfig.get_platform(),
            'python': sys.implementation.cache_tag, 'revision': 2 if sys.platform == 'win32' else 1}


def verify_features(information):
    for line in information.splitlines():
        key, _, value = line.strip().partition(':')
        if key in ('Intel IPP', 'Intel IPP IW', 'FFMPEG', 'GStreamer') and value.strip() != 'NO':
            raise RuntimeError(f'OpenCV includes a disabled component: {key}')


def verify_payload(files):
    for file in files:
        if UNWANTED_LIBRARY.match(Path(file).name):
            raise RuntimeError(f'Unexpected OpenCV/video library in the package: {Path(file).name}')


def verify_bundle(bundle, info):
    files = [file.relative_to(bundle) for file in bundle.rglob('*') if file.is_file()]
    verify_payload(files)
    # macOS freezes Mach-O extensions with platform-specific changes/signatures.
    if sys.platform != 'darwin':
        candidates = [file for file in files if file.parent.name == 'cv2' and
                      file.name.startswith('cv2') and file.suffix in ('.pyd', '.so')]
        if len(candidates) != 1 or digest(bundle / candidates[0]) != info.get('extension_sha256'):
            raise RuntimeError('Bundle does not contain the verified OpenCV extension')


def native_extension(files):
    native = [file for file in files if Path(file).parts[0] == 'cv2'
              and Path(file).name.startswith('cv2') and Path(file).suffix in ('.pyd', '.so')]
    if len(native) != 1:
        raise RuntimeError('Expected exactly one OpenCV native extension')
    return native[0]


def verify_installed():
    dist = metadata.distribution(PACKAGE)
    expected = recipe()
    info = json.loads(dist.read_text(RECORD) or '{}')
    if info.get('recipe') != expected or dist.version != expected['version']:
        raise RuntimeError('Prepare the pinned OpenCV wheel with build_tools/common/build_opencv.py')
    files = dist.files or ()
    verify_payload(files)
    if digest(dist.locate_file(native_extension(files))) != info.get('extension_sha256'):
        raise RuntimeError('Installed OpenCV extension differs from its build record')
    import cv2
    verify_features(cv2.getBuildInformation())
    return info


def compiler_environment():
    env = os.environ.copy()
    if sys.platform != 'win32' or shutil.which('cl.exe'):
        return env
    vswhere = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    install = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip()
    if not install:
        raise RuntimeError('Install Visual Studio with the C++ x64 build tools')
    env['APE_OPENCV_VCVARS'] = str(Path(install) / 'VC/Auxiliary/Build/vcvars64.bat')
    output = subprocess.check_output('cmd.exe /d /s /u /c "call "%APE_OPENCV_VCVARS%" >nul && set"',
                                     env=env, encoding='utf-16le')
    return {name: value for line in output.splitlines() if '=' in line and not line.startswith('=')
            for name, value in [line.split('=', 1)]}


def patch_build_information(source):
    path = source / 'opencv/cmake/OpenCVUtils.cmake'
    text = path.read_text(encoding='utf-8')
    before = 'function(ocv_output_status msg)\n  message(STATUS "${msg}")\n'
    after = before + '''  foreach(root "$ENV{APE_OPENCV_BUILD_ROOT}" "$ENV{APE_OPENCV_BUILD_HOME}")
    if(NOT root STREQUAL "")
      string(REPLACE "${root}" "<build>" msg "${msg}")
      file(TO_CMAKE_PATH "${root}" normalized_root)
      string(REPLACE "${normalized_root}" "<build>" msg "${msg}")
    endif()
  endforeach()
'''
    if text.count(before) != 1:
        raise RuntimeError('Unexpected OpenCV build-information template')
    path.write_text(text.replace(before, after), encoding='utf-8')


def patch_windows_wrapper(source):
    # The upstream wheel build normally selects Visual Studio with -A x64.
    # Ninja uses the x64 compiler environment already selected above.
    path = source / 'setup.py'
    text = path.read_text(encoding='utf-8')
    before = 'else ["-DCMAKE_GENERATOR_PLATFORM=x64"] if is64 and sys.platform == "win32"'
    video_files = r'''        "cv2": (
            [r"bin/opencv_videoio_ffmpeg\d{3}%s\.dll" % ("_64" if is64 else "")]
            if os.name == "nt"
            else []
        )
        +
'''
    if text.count(before) != 1 or text.count(video_files) != 1:
        raise RuntimeError('Unexpected OpenCV Windows wheel configuration')
    path.write_text(text.replace(before, '').replace(video_files, '        "cv2":\n'), encoding='utf-8')


def patch_windows_sample_paths(source):
    # APE does not install OpenCV's samples. Keep the temporary compiler and
    # installation directories out of cv2's native sample lookup settings.
    core = source / 'opencv/modules/core'
    path = core / 'CMakeLists.txt'
    text = path.read_text(encoding='utf-8')
    for definition in (r'#define OPENCV_INSTALL_PREFIX \"${CMAKE_INSTALL_PREFIX}\"',
                       r'#define OPENCV_BUILD_DIR \"${CMAKE_BINARY_DIR}\"'):
        if text.count(definition) != 1:
            raise RuntimeError('Unexpected OpenCV sample path definition')
    text = text.replace(r'#define OPENCV_INSTALL_PREFIX \"${CMAKE_INSTALL_PREFIX}\"', '')
    text = text.replace(r'#define OPENCV_BUILD_DIR \"${CMAKE_BINARY_DIR}\"', r'#define OPENCV_BUILD_DIR \"\"')
    path.write_text(text, encoding='utf-8')

    path = core / 'src/utils/datafile.cpp'
    text = path.read_text(encoding='utf-8')
    before = '    if (isSubDirectory(build_dir, cwd) || isSubDirectory(utils::fs::canonical(build_dir), utils::fs::canonical(cwd)))'
    after = ('    if (!build_dir.empty() &&\n'
             '        (isSubDirectory(build_dir, cwd) ||\n'
             '         isSubDirectory(utils::fs::canonical(build_dir), utils::fs::canonical(cwd))))')
    if text.count(before) != 1:
        raise RuntimeError('Unexpected OpenCV sample lookup condition')
    path.write_text(text.replace(before, after), encoding='utf-8')


def patch_optional_typing(source):
    # These upstream aliases refer to features outside our image modules.
    directory = source / 'opencv/modules/python/src2/typing_stubs_generation'
    path = directory / 'predefined_types.py'
    text = path.read_text(encoding='utf-8')
    for name in ('FeatureDetector', 'DescriptorExtractor', 'FeatureExtractor'):
        alias = f'    AliasTypeNode.class_("{name}", "Feature2D",\n                         export_name="{name}"),\n'
        if text.count(alias) != 1:
            raise RuntimeError(f'Unexpected OpenCV type alias: {name}')
        text = text.replace(alias, '')
    path.write_text(text, encoding='utf-8')
    if sys.platform == 'win32':
        # The 5.1 generator already handles members of disabled modules.
        path = directory / 'api_refinement.py'
        text = path.read_text(encoding='utf-8')
        imports = 'for_each_function_overload)'
        refinement = '        refine_symbol(root, symbol_name)\n'
        if text.count(imports) != 1 or text.count(refinement) != 1:
            raise RuntimeError('Unexpected OpenCV typing refinement')
        text = text.replace(imports, 'for_each_function_overload, ScopeNotFoundError, SymbolNotFoundError)')
        text = text.replace(refinement, '        try:\n            refine_symbol(root, symbol_name)\n'
                            '        except (ScopeNotFoundError, SymbolNotFoundError):\n            continue\n')
        path.write_text(text, encoding='utf-8')


def source_archive(expected):
    from prepare_release import download, read_url

    metadata_url = f'https://pypi.org/pypi/{PACKAGE}/{expected["version"]}/json'
    source_cache = ROOT / '.dev-deps/legal/sources' / expected['source']
    if source_cache.is_file() and digest(source_cache) == expected['source_sha256']:
        return source_cache
    index = json.loads(read_url(metadata_url))
    record = next(row for row in index['urls'] if row['filename'] == expected['source'])
    return download(record['url'], expected['source'], expected['source_sha256'])


def unpack_source(source, work):
    prefix = source.name.removesuffix('.tar.gz')

    def into_short_path(member, destination):
        if (member.name != prefix and not member.name.startswith(prefix + '/')) or not (member.isfile() or member.isdir()):
            raise RuntimeError('Unexpected OpenCV source archive entry')
        member = copy(member)
        member.name = 'src' + member.name[len(prefix):]
        return tarfile.data_filter(member, destination)

    with tarfile.open(source) as archive:
        archive.extractall(work, filter=into_short_path)
    return work / 'src'


def build_wheel(cache, expected):
    source = source_archive(expected)
    # MSVC probes and C++ object paths need room under the Windows path limit.
    work_root = ROOT / 'build'
    work_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cv-', dir=work_root) as directory:
        work = Path(directory)
        builder = work / 'env'
        venv.EnvBuilder(with_pip=True).create(builder)
        bin_dir = builder / ('Scripts' if sys.platform == 'win32' else 'bin')
        python = bin_dir / ('python.exe' if sys.platform == 'win32' else 'python')
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', *TOOLS], check=True)
        env = compiler_environment()
        path_key = next(key for key in env if key.upper() == 'PATH')
        env[path_key] = str(bin_dir) + os.pathsep + env[path_key]
        env.update(ENABLE_HEADLESS='1', ENABLE_CONTRIB='0', CMAKE_GENERATOR='Ninja',
                   CMAKE_ARGS=' '.join(expected['cmake_options']),
                   APE_OPENCV_BUILD_ROOT=str(ROOT), APE_OPENCV_BUILD_HOME=str(Path.home()),
                   CMAKE_BUILD_PARALLEL_LEVEL=str(min(6, os.cpu_count() or 1)))
        if sys.platform == 'win32':
            env['_CL_'] = f'/d1trimfile:"{ROOT}"'
            env['LINK'] = '/PDBALTPATH:%_PDB%'
            env['DISTUTILS_USE_SDK'] = '1'
        else:
            prefix = f'-ffile-prefix-map={work}=.'
            env.update(CFLAGS=f'-g0 {prefix}', CXXFLAGS=f'-g0 {prefix}')
        # Unpack the verified tarball in our own work directory so compiler
        # paths can be normalized and pip does not compile in a profile temp dir.
        checkout = unpack_source(source, work)
        patch_build_information(checkout)
        patch_optional_typing(checkout)
        if sys.platform == 'win32':
            patch_windows_wrapper(checkout)
            patch_windows_sample_paths(checkout)
        settings = []
        if sys.platform == 'win32':
            # scikit-build 0.18's VS version probe predates MSVC 19.5x.
            # The actual Ninja build tests the explicitly configured compiler.
            settings = ['--config-settings=--build-option=-G', '--config-settings=--build-option=Ninja',
                        '--config-settings=--build-option=--skip-generator-test']
        subprocess.run([str(python), '-m', 'pip', 'wheel', '--no-index', '--no-deps',
                        '--no-build-isolation', *settings, '--wheel-dir', str(work), str(checkout)],
                       env=env, check=True)
        wheel, = work.glob('opencv_python_headless-*.whl')
        with zipfile.ZipFile(wheel) as archive:
            from release_privacy import has_local_path, local_paths

            verify_payload(archive.namelist())
            extension = native_extension(archive.namelist())
            binary = archive.read(extension)
            if has_local_path(binary, local_paths()):
                raise RuntimeError('OpenCV extension still contains a private build path')
            extension_hash = hashlib.sha256(binary).hexdigest()
        record = {'recipe': expected, 'wheel_sha256': digest(wheel), 'extension_sha256': extension_hash}
        record_file = work / RECORD
        record_file.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        wheel.replace(cache / wheel.name)
        record_file.replace(cache / RECORD)


def prepare():
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Use a dedicated build virtualenv')
    try:
        return verify_installed()
    except (ImportError, OSError, RuntimeError, ValueError):
        pass
    expected = recipe()
    key = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()[:16]
    cache = ROOT / '.dev-deps/patched-wheels/opencv' / key
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / RECORD).is_file():
        build_wheel(cache, expected)
    wheel, = cache.glob('opencv_python_headless-*.whl')
    info = json.loads((cache / RECORD).read_text(encoding='utf-8'))
    if info.get('recipe') != expected or digest(wheel) != info.get('wheel_sha256'):
        raise RuntimeError('Cached OpenCV wheel does not match its build record')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                    '--force-reinstall', str(wheel)], check=True)
    dist = metadata.distribution(PACKAGE)
    metadata_file = next(file for file in dist.files or () if file.name == 'METADATA')
    shutil.copyfile(cache / RECORD, dist.locate_file(metadata_file).with_name(RECORD))
    return verify_installed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    info = verify_installed() if args.check else prepare()
    print(f'OpenCV {info["recipe"]["version"]} verified without Intel IPP or FFmpeg')


if __name__ == '__main__':
    main()
