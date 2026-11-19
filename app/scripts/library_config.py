"""Shared library location configuration for Harmony clients."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Harmony configuration directory
HARMONY_DIR = Path.home() / ".harmony"

# Database paths
DATABASE_PATH = HARMONY_DIR / "harmony.sqlite3"
LEGACY_DATABASE_PATH = HARMONY_DIR / ".play" / "harmony.sqlite3"

# Music library directory
DEFAULT_FOLDER_PATH = HARMONY_DIR / ".play"

# Library index
LOCAL_LIBRARY_PATH = DEFAULT_FOLDER_PATH / "library.json"
DEFAULT_LIBRARY_PATHS = (LOCAL_LIBRARY_PATH,)


def get_library_path():
    """Return the configured library path, preferring existing locations."""

    configured_path = os.environ.get("HARMONY_LIBRARY_PATH")

    if configured_path:
        path = Path(configured_path).expanduser()
        if path.is_file():
            return path

    if LOCAL_LIBRARY_PATH.is_file():
        return LOCAL_LIBRARY_PATH

    return LOCAL_LIBRARY_PATH


def get_library_folder_path():
    """Return the configured library folder path."""

    configured_path = os.environ.get("HARMONY_LIBRARY_PATH")

    if configured_path:
        path = Path(configured_path).expanduser()

        # If the environment variable points to a JSON file
        if path.suffix == ".json":
            path = path.parent

        return path

    return DEFAULT_FOLDER_PATH


LIBRARY_PATH = get_library_path()
LIBRARY_FOLDER = get_library_folder_path()


def ensure_library_parent(path=None):
    """Create the parent directory for a library path and return the path."""

    library_path = Path(path) if path is not None else get_library_path()
    library_path.parent.mkdir(parents=True, exist_ok=True)
    return library_path


def ensure_library_folder():
    """Create the music library directory and return its path."""

    library_folder = get_library_folder_path()
    library_folder.mkdir(parents=True, exist_ok=True)
    return library_folder
