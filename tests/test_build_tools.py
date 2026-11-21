"""Build entry points, venv-only preparation and corresponding-source layout."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare_build_tested', 'scripts/prepare_build.py')
build = load('build_entry_tested', 'scripts/build.py')
privacy = load('release_privacy_tested', 'build_tools/common/release_privacy.py')


def test_environment_inspection_is_read_only_and_checks_native_version(monkeypatch):
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *a, **k: pytest.fail('Inspection must not run installers'))
    import pymupdf
    monkeypatch.setattr(pymupdf, 'mupdf_version', '1.28.0')
    report = prepare.inspect_environment()
    assert 'Native MuPDF: expected corrected version 1.28.5' in report['issues']
    assert report['required_python'] == '3.14.5'


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare.sys, 'prefix', 'global')
    monkeypatch.setattr(prepare.sys, 'base_prefix', 'global')
    python = tmp_path / '.venv-build/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    commands = []
    monkeypatch.setattr(prepare, 'run', lambda command, root: commands.append(list(map(str, command))))
    monkeypatch.setattr(prepare, 'required_python', lambda root: '3.14.5')
    return tmp_path, commands


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows build bootstrap')
@pytest.mark.parametrize('identity', [
    dict(prefix='global', base='global', version='3.14.5', bits=64),
    dict(prefix='venv', base='global', version='3.14.4', bits=64),
    dict(prefix='venv', base='global', version='3.14.5', bits=32),
])
def test_invalid_interpreter_never_installs_packages(bootstrap, monkeypatch, identity):
    root, commands = bootstrap
    monkeypatch.setattr(prepare, 'capture', lambda *a: identity)
    with pytest.raises(RuntimeError):
        prepare.prepare(root)
    assert not commands


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows build bootstrap')
@pytest.mark.parametrize('check_only', [False, True])
def test_suitable_environment_is_reused_without_reinstalling(bootstrap, monkeypatch, check_only):
    root, commands = bootstrap
    replies = iter([dict(prefix='venv', base='global', version='3.14.5', bits=64),
                    dict(python='venv/python', issues=[])])
    monkeypatch.setattr(prepare, 'capture', lambda *a: next(replies))
    assert prepare.prepare(root, check_only=check_only)['issues'] == []
    assert len(commands) == 1 and commands[0][-3:] == ['-m', 'pip', 'check']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows build bootstrap')
def test_check_only_never_repairs_or_downloads(bootstrap, monkeypatch):
    root, commands = bootstrap
    replies = iter([dict(prefix='venv', base='global', version='3.14.5', bits=64), dict(issues=['missing package'])])
    monkeypatch.setattr(prepare, 'capture', lambda *a: next(replies))
    with pytest.raises(RuntimeError, match='needs preparation'):
        prepare.prepare(root, check_only=True)
    assert not commands


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows build bootstrap')
def test_repair_uses_corrected_mupdf_before_pinned_install(bootstrap, monkeypatch):
    root, commands = bootstrap
    replies = iter([dict(prefix='venv', base='global', version='3.14.5', bits=64),
                    dict(issues=['missing package']), dict(issues=[])])
    monkeypatch.setattr(prepare, 'capture', lambda *a: next(replies))
    prepare.prepare(root)
    assert commands[0][0] == 'powershell.exe'
    assert str(root / 'build_tools/windows/build_patched_mupdf.ps1') in commands[0]
    assert '-r' in commands[1] and str(root / 'requirements-release.txt') in commands[1]
    assert '--find-links' in commands[1] and str(root / '.dev-deps/patched-wheels') in commands[1]
    assert 'egg_info' in commands[2][-1] and commands[-1][-1] == 'check'


def test_forwarder_preserves_arguments_and_explicit_interpreter(monkeypatch):
    monkeypatch.setattr(build.sys, 'platform', 'win32')
    command = build.command(['-PythonExe', 'C:/Python With Spaces/python.exe', '-OfflineSources'])
    assert command.count('-PythonExe') == 1
    assert command[-3:] == ['-PythonExe', 'C:/Python With Spaces/python.exe', '-OfflineSources']
    assert build.command(['--help'])[-1] == '-Help'


def test_unsupported_packaging_fails_explicitly(monkeypatch):
    monkeypatch.setattr(build.sys, 'platform', 'freebsd')
    with pytest.raises(RuntimeError, match='supports Windows, Linux and macOS'):
        build.command([])


@pytest.mark.parametrize('script', ['run_linux.sh', 'run_mac.command',
                                    'build_tools/common/prepare_unix.sh',
                                    'build_tools/linux/build_linux_installer.sh',
                                    'build_tools/linux/build_linux_wsl.sh',
                                    'build_tools/macos/build_macos_app.sh'])
def test_unix_shell_scripts_use_lf(script):
    assert b'\r' not in (ROOT / script).read_bytes()


@pytest.mark.parametrize('platform,script', [('linux', 'build_tools/linux/build_linux_installer.sh'),
                                            ('darwin', 'build_tools/macos/build_macos_app.sh')])
def test_native_build_dispatcher_uses_platform_script(monkeypatch, platform, script):
    monkeypatch.setattr(build.sys, 'platform', platform)
    assert build.command(['--jobs', '2']) == ['bash', str(ROOT / script), '--python', sys.executable, '--jobs', '2']
    assert build.command(['--python', '/some env/bin/python'])[2:] == ['--python', '/some env/bin/python']


@pytest.mark.skipif(sys.platform != 'win32', reason='PowerShell entry points')
@pytest.mark.parametrize('entry', ['python', 'powershell'])
def test_help_works_from_another_directory(tmp_path, entry):
    if entry == 'python':
        command = [sys.executable, str(ROOT / 'scripts/build.py'), '--help']
    else:
        relative = 'build_tools/windows/build_windows_installer.ps1'
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / relative), '-Help']
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert b'windows-build.log' in result.stdout


def test_corresponding_source_includes_build_procedures_and_entry_points():
    release = load('release_layout_tested', 'build_tools/common/prepare_release.py')
    paths = {path.relative_to(ROOT).as_posix() for path in release.project_files()}
    assert {'.gitattributes', 'run_windows.bat', 'run_linux.sh', 'run_mac.command', 'scripts/build.py', 'scripts/prepare_build.py',
            'build_tools/windows/APEPDFStudio.spec', 'build_tools/windows/APEPDFStudio.iss',
            'build_tools/windows/build_windows_installer.ps1', 'build_tools/common/prepare_release.py',
            'docs/distribuzione/README.md'} <= paths


def test_ci_calls_existing_tools():
    import re
    workflow = (ROOT / '.github/workflows/security.yml').read_text(encoding='utf-8')
    paths = re.findall(r'(?:scripts|build_tools)/[\w/]+\.(?:py|ps1)', workflow)
    assert paths and all((ROOT / path).is_file() for path in paths)


def test_freeze_removes_install_origin_and_retains_mupdf_runtime_fields(tmp_path):
    source = tmp_path / '_build.py'
    source.write_text(
        "mupdf_location = '/home/fixture-builder/repo'\n"
        "pymupdf_git_diff = 'local changes'\n"
        "pymupdf_version = '1.28.2'\n"
        "pymupdf_version_tuple = (1, 28, 2)\n"
        "fake_no_gil = False\n",
        encoding='utf-8',
    )
    original = source.read_bytes()
    toc = [('pymupdf._build', str(source), 'PYMODULE')]
    data = [('ape_pdf_studio-1.3.0.dist-info/direct_url.json', 'local.json', 'DATA'),
            ('ape_pdf_studio-1.3.0.dist-info/METADATA', 'METADATA', 'DATA'),
            ('dependency.dist-info/licenses/LICENSE', 'LICENSE', 'DATA')]
    pure, files = privacy.public_tables(toc, data, tmp_path / 'public')
    values = {}
    exec(Path(pure[0][1]).read_text(encoding='utf-8'), values)
    assert values['mupdf_location'] == 'bundled MuPDF'
    assert values['pymupdf_git_diff'] is None
    assert values['pymupdf_version_tuple'] == (1, 28, 2)
    assert values['fake_no_gil'] is False
    assert files == data[1:]
    assert source.read_bytes() == original


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16le'])
def test_build_path_gate_reads_binary_boundaries_and_escaped_paths(tmp_path, encoding):
    markers = privacy.local_paths(r'C:\Users\Fixture Builder')
    binary = tmp_path / 'compiled.pyd'
    binary.write_bytes(b'\0' * (1024**2 - 4) + r'C:\\Users\\Fixture Builder\\mupdf'.encode(encoding))
    with pytest.raises(RuntimeError, match='compiled.pyd') as failure:
        privacy.check_paths([tmp_path], markers)
    assert 'Fixture Builder' not in str(failure.value)
    binary.write_bytes(b'build sourced from .dev-deps/mupdf')
    privacy.check_paths([tmp_path], markers)


def test_build_path_gate_reads_the_compressed_executable_archive(tmp_path):
    writers = pytest.importorskip('PyInstaller.archive.writers')
    module = 'pymupdf._build'
    source = tmp_path / '_build.py'
    private = "mupdf_location = '/home/fixture-builder/mupdf'"
    executable = tmp_path / 'APEPDFStudio.exe'
    pyz = tmp_path / 'PYZ.pyz'

    def executable_with(metadata):
        code = compile(metadata, 'pymupdf/_build.py', 'exec')
        writers.ZlibArchiveWriter(str(pyz), [(module, str(source), 'PYMODULE')], {module: code})
        writers.CArchiveWriter(str(executable), [('PYZ.pyz', str(pyz), False, 'z')], 'python.dll')

    markers = privacy.local_paths('/home/fixture-builder')
    executable_with(private)
    assert b'/home/fixture-builder' not in executable.read_bytes()
    with pytest.raises(RuntimeError, match=r'PYZ:pymupdf\._build'):
        privacy.check_paths([executable], markers)
    executable_with(privacy.public_build_metadata(private))
    privacy.check_paths([executable], markers)


def test_previously_built_mupdf_wheel_is_checked_before_reuse(tmp_path):
    wheel = tmp_path / 'dependency.whl'
    with zipfile.ZipFile(wheel, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('pymupdf/mupdfcpp64.dll', b'C:\\Users\\PreviousBuilder\\work\\mupdf.c\0')
    with pytest.raises(RuntimeError, match='mupdfcpp64.dll'):
        privacy.check_mupdf_wheel(wheel)


def test_portable_archive_omits_builder_account_metadata():
    entry = tarfile.TarInfo('APEPDFStudio')
    entry.uid, entry.gid, entry.uname, entry.gname = 1001, 1002, 'fixture-builder', 'fixture-group'
    public = privacy.public_tar_owner(entry)
    assert (public.uid, public.gid, public.uname, public.gname) == (0, 0, 'root', 'root')
