#!/usr/bin/env bash
# Bootstrap only an explicitly selected virtual environment.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${1:?Specify the virtualenv Python}"
cd "$root"
if "$python" -c 'import sys; assert sys.platform in ("linux", "darwin") and sys.version_info >= (3,11) and sys.prefix != sys.base_prefix'; then
    "$python" -m pip install --disable-pip-version-check 'pip>=25.3'
else
    echo 'A Linux/macOS Python 3.11+ virtualenv is required.' >&2
    exit 1
fi
if ! "$python" build_tools/common/release_privacy.py --mupdf-native >/dev/null 2>&1; then
    # The generated C++ bindings can embed the absolute source path in
    # __FILE__. Compile in a private directory outside the user's profile.
    native_work="$(mktemp -d /tmp/ape-mupdf.XXXXXXXX)"
    trap 'rm -rf -- "$native_work"' EXIT
    "$python" build_tools/common/prepare_patched_mupdf.py --build-root "$native_work"
    PIPCL_SHOW_ENV=0 PYMUPDF_SETUP_MUPDF_BUILD="$native_work/mupdf-1.28.5-source" \
        "$python" -m pip wheel --build-constraint requirements-native-build.txt --no-deps \
        --wheel-dir "$native_work/wheels" "$native_work/pymupdf-1.28.2"
    "$python" -m pip install --no-index --no-deps --force-reinstall \
        --find-links "$native_work/wheels" 'pymupdf==1.28.2.post1285'
    "$python" build_tools/common/release_privacy.py --mupdf-native
fi
"$python" -m pip install --disable-pip-version-check -e '.[installer,dev]'
if [[ "$(uname -s)" == Darwin ]]; then
    # PyInstaller converts the repository's PNG master to the bundle's ICNS.
    "$python" -m pip install --disable-pip-version-check Pillow
fi
"$python" -m pip check
