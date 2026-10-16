"""Propagate only the Windows security zone, never source/referrer URLs."""
from __future__ import annotations

import os
from pathlib import Path
import re


def read_zone(path):
    if os.name != "nt":
        return None
    try:
        with Path(str(path)+":Zone.Identifier").open("rb") as stream:
            data = stream.read(4097)
    except FileNotFoundError:
        return None
    if len(data) > 4096:
        return 3  # Do not silently strip an unrecognised provenance marker.
    match = re.search(rb"(?im)^ZoneId\s*=\s*([0-4])\s*$", data)
    return int(match[1]) if match else 3


def propagate_zone(destination, *sources, zone=None):
    """Call on the temporary output before its atomic replacement.

    An ADS write failure propagates, leaving the existing destination intact.
    Filesystems without ADS can still save files without an incoming zone.
    """
    zones = [zone for path in sources if path is not None for zone in [read_zone(path)] if zone is not None]
    if zone is not None:
        if type(zone)is not int or not 0<=zone<=4:raise ValueError('Invalid source zone')
        zones.append(zone)
    if os.name == "nt" and zones:
        Path(str(destination)+":Zone.Identifier").write_text(
            f"[ZoneTransfer]\r\nZoneId={max(zones)}\r\n", encoding="ascii")

