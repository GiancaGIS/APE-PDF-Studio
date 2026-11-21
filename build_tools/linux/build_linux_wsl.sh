#!/usr/bin/env bash
# Build on the Linux filesystem and export only the completed packages.
set -euo pipefail
source_root="$(cd "${1:?Specify the Windows checkout via its WSL path}" && pwd -P)"
shift
mode="${1:-build}"
[[ "$mode" == build || "$mode" == prepare ]] || exit 2
[[ -n "${WSL_DISTRO_NAME:-}" && -f "$source_root/pyproject.toml" ]] || {
    echo 'Run this command in WSL with a valid checkout.' >&2
    exit 2
}
checkout="$(printf '%s' "$source_root" | sha256sum)"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/ape-pdf-studio/wsl-build/${checkout:0:24}"
mkdir -p "$cache"
case "$(stat -f -c %T "$cache")" in
    9p|drvfs|fuseblk|ntfs*) echo 'Use a WSL cache on the Linux filesystem.' >&2; exit 2 ;;
esac
exec 9>"$cache/build.lock"
flock -n 9 || { echo 'Another build is using this WSL workspace.' >&2; exit 2; }
stage="$(mktemp -d "$cache/source.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$source_root/build"
exec > >(tee "$source_root/build/linux-wsl-$mode.log") 2>&1
cd "$source_root"
rsync -rlt --relative \
    --exclude='__pycache__/' --exclude='*.egg-info/' --exclude='*.pyc' --exclude='*.pyd' --exclude='*.so' \
    --exclude='*.key' --exclude='*.pem' --exclude='*.pfx' --exclude='*.p12' \
    src scripts build_tools native tests examples docs images licenses .github \
    pyproject.toml requirements-*.txt README.md LICENSE NOTICE SOURCE_CODE.md THIRD_PARTY_NOTICES.md \
    AGENTS.md .gitignore .gitattributes run_windows.bat run_linux.sh run_mac.command "$stage/"
mkdir -p "$stage/.dev-deps/patched-mupdf"
for name in pymupdf-1.28.2.tar.gz mupdf-1.28.5-source.tar.gz; do
    archive="$source_root/.dev-deps/legal/sources/$name"
    if [[ -f "$archive" ]]; then cp -- "$archive" "$stage/.dev-deps/patched-mupdf/"; fi
done
if [[ ! -x "$cache/venv/bin/python" ]]; then
    "${APE_LINUX_PYTHON:-python3}" -m venv "$cache/venv"
fi
cd "$stage"
python="$cache/venv/bin/python"
export APE_PRIVATE_BUILD_ROOT="$source_root"
bash build_tools/common/prepare_unix.sh "$python"
if [[ "$mode" == prepare ]]; then exit 0; fi
bash build_tools/linux/build_linux_installer.sh --python "$python"
mkdir -p "$source_root/dist/linux"
cp -- "$stage"/dist/linux/* "$source_root/dist/linux/"
echo "Packages: $source_root/dist/linux"
