"""Forward packaging to the native build_tools procedure."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(arguments):
    if sys.platform in ('linux', 'darwin'):
        scripts = {'linux': 'linux/build_linux_installer.sh', 'darwin': 'macos/build_macos_app.sh'}
        interpreter = [] if '--python' in arguments or any(arg.startswith('--python=') for arg in arguments) else ['--python', sys.executable]
        return ['bash', str(ROOT / 'build_tools' / scripts[sys.platform]), *interpreter, *arguments]
    if sys.platform != 'win32':
        raise RuntimeError('Standalone packaging supports Windows, Linux and macOS. See docs/distribution/README.md.')
    interpreter = [] if any(value.lower().split(':', 1)[0] in ('-python', '-pythonexe') for value in arguments) else ['-PythonExe', sys.executable]
    return ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT / 'build_tools/windows/build_windows_installer.ps1'),
            *interpreter,
            *['-Help' if value in ('-h', '--help') else value for value in arguments]]


def main():
    try:
        return subprocess.run(command(sys.argv[1:]), cwd=ROOT).returncode
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
