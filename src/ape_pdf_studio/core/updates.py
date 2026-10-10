"""Validate public GitHub release metadata; never download or execute an update."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import quote

from .. import __version__
from .localization import tr

REPOSITORY = "GiancaGIS/APE-PDF-Studio"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_MS = 12_000


class UpdateError(ValueError):
    """The check could not provide a trustworthy version comparison."""


def stable_version(value: str) -> tuple[int, int, int]:
    # Stable SemVer tags only. Build metadata has no effect on precedence.
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("Invalid version")
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", value)
    if not match:
        raise ValueError("Expected a stable major.minor.patch version")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    update_available: bool


def parse_release(status: int, body: bytes, current: str = __version__) -> Release | None:
    if status == 404:
        return None
    if status in (403, 429):
        raise UpdateError(tr("GitHub ha limitato le richieste. Riprova più tardi."))
    if status != 200:
        raise UpdateError(tr("Controllo aggiornamenti non riuscito (HTTP {0}).", status))
    try:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Oversized response")
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
            raise ValueError("Not a published stable release")
        tag = payload["tag_name"]
        latest, installed = stable_version(tag), stable_version(current)
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise UpdateError(tr("La risposta di GitHub non contiene una versione stabile valida.")) from exc
    # Do not trust html_url or asset URLs from the response. Keep navigation on
    # the fixed official repository, even if upstream metadata is unexpected.
    url = RELEASES_URL + "/tag/" + quote(tag, safe="")
    return Release(".".join(map(str, latest)), url, latest > installed)
