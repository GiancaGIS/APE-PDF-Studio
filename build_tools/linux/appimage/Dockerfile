FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential autoconf automake libtool pkg-config meson ninja-build xz-utils \
    && rm -rf /var/lib/apt/lists/*
