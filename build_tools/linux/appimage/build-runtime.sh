#!/bin/sh
# Compile the AppImage launcher using the source archives mounted at /inputs.
set -eu
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=0
umask 022
jobs="${1:?Specify the build job count}"
mkdir /tmp/appimage-build
cd /tmp/appimage-build
export CFLAGS='-O2 -g0 -fstack-protector-strong -ffile-prefix-map=/tmp/appimage-build=.'

runtime_commit=dd6cebedcbddde9c82f89b011e8e1d40b6e43868
runtime="type2-runtime-$runtime_commit"
for source in "$runtime.tar.gz" fuse-3.15.0.tar.xz squashfuse-0.5.2.tar.gz zstd-1.5.7.tar.gz; do
    tar -xf "/inputs/$source" --no-same-owner
done

prefix="$PWD/prefix"
mkdir -p "$prefix/include" "$prefix/lib"
make -C zstd-1.5.7/lib -j "$jobs" libzstd.a
cp zstd-1.5.7/lib/zstd.h zstd-1.5.7/lib/zstd_errors.h "$prefix/include/"
cp zstd-1.5.7/lib/libzstd.a "$prefix/lib/"

# Use the runtime project's FUSE mount patch with its matching libfuse release.
patch -d fuse-3.15.0 -p1 < "$runtime/patches/libfuse/mount.c.diff"
meson setup fuse-build fuse-3.15.0 --prefix "$prefix" --libdir lib --buildtype release \
    --default-library static -Dutils=false -Dexamples=false -Dtests=false -Ddisable-libc-symbol-version=true
meson compile -C fuse-build -j "$jobs"
meson install -C fuse-build --no-rebuild

# The APE payload uses Zstandard exclusively. Other codecs and allocators
# are omitted from the runtime so they do not become release components.
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
export CPPFLAGS="-I$prefix/include" LDFLAGS="-L$prefix/lib"
(
    cd squashfuse-0.5.2
    ./autogen.sh
    ./configure --prefix="$prefix" --disable-shared --enable-static --with-zstd="$prefix" \
        --without-zlib --without-xz --without-lzo --without-lz4 || { tail -n 120 config.log; exit 1; }
    make -j "$jobs" libsquashfuse.la libsquashfuse_ll.la
)
mkdir "$prefix/include/squashfuse"
cp squashfuse-0.5.2/*.h "$prefix/include/squashfuse/"

runtime_source="$runtime/src/runtime"
cc $CFLAGS -std=gnu99 -D_FILE_OFFSET_BITS=64 "-DGIT_COMMIT=\"$runtime_commit\"" \
    -Wl,--gc-sections -Wl,-z,relro,-z,now -ffunction-sections -fdata-sections \
    -T "$runtime_source/data_sections.ld" -I"$prefix/include/fuse3" -I"$prefix/include" \
    "$runtime_source/runtime.c" -Wl,--start-group \
    squashfuse-0.5.2/.libs/libsquashfuse.a squashfuse-0.5.2/.libs/libsquashfuse_ll.a \
    "$prefix/lib/libfuse3.a" "$prefix/lib/libzstd.a" -Wl,--end-group -pthread -ldl -o /out/runtime
strip --strip-unneeded /out/runtime
# Set the Type 2 marker using the upstream runtime's build convention.
printf 'AI\002' | dd of=/out/runtime bs=1 seek=8 conv=notrunc status=none
needed=$(readelf -d /out/runtime | sed -n 's/.*(NEEDED).*\[\([^]]*\)\].*/\1/p')
test "$needed" = libc.so.6
printf '%s\n' "$needed" > /out/dynamic-libraries.txt
dpkg-query -W -f='${Package}\t${Version}\n' > /out/build-packages.tsv
