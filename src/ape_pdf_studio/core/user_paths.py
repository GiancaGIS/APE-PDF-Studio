"""Keep user-visible cloud/Vault paths; canonicalize only security boundaries."""
import os
from pathlib import Path


def user_path(value):
    # GetFinalPathNameByHandle (used by Path.resolve) can return a Vault volume
    # name that is not a usable filesystem path. Let Windows open the supplied
    # path through its cloud/encryption provider instead.
    return Path(os.path.abspath(Path(value).expanduser()))


def same_file(first, second):
    if user_path(first) == user_path(second):
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False
