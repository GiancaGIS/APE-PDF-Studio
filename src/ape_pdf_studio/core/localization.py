"""Explicit application messages, separate from PDF/user text and independent of Qt."""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import os

DEFAULT_LANGUAGE = "it"
LANGUAGES = {"it": "Italiano", "es": "Español", "en": "English", "fr": "Français"}


def normalize_language(value):
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def current_language():
    return normalize_language(os.environ.get("APE_UI_LANGUAGE", os.environ.get("APEX_UI_LANGUAGE", DEFAULT_LANGUAGE)))


@lru_cache(maxsize=3)
def catalog(language):
    return json.loads(files("ape_pdf_studio").joinpath("locales", language + ".json").read_text(encoding="utf-8"))


def tr(message, *values):
    language = current_language()
    translated = message if language == DEFAULT_LANGUAGE else catalog(language).get(message, message)
    return translated.format(*values) if values else translated
