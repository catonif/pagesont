"""
User preferences and persistence.

Preferences can be regulated in a settings dialog and optionally persisted to a
JSON config file (`pagesont.json`) in the current working directory.  When the
program is run from that same directory, the config is loaded back automatically.
"""

import json
import os
from dataclasses import dataclass, asdict, fields

CONFIG_FILENAME = "pagesont-config.json"


@dataclass
class Preferences:
    font_size: int = 10
    apply_nfd: bool = True
    hide_duplicate_textedit: bool = True
    simplify_tolerance: float = 2.5
    separator: str = ""
    sequences: list = None


def config_path(cwd=None):
    """Path to the config file in the given (or current) working directory."""
    base = cwd or os.getcwd()
    return os.path.join(base, CONFIG_FILENAME)


def load_preferences(cwd=None):
    """Load preferences from the cwd config file, falling back to defaults."""
    prefs = Preferences()
    path = config_path(cwd)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid = {f.name for f in fields(Preferences)}
        for key, value in data.items():
            if key in valid:
                setattr(prefs, key, value)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        pass
    return prefs


def save_preferences(prefs, cwd=None):
    """
    Persist preferences to the cwd config file.

    Any existing keys the app doesn't manage (e.g. manually-added fields such
    as "separator") are preserved; only the known preference fields are
    overwritten with the current values.
    """
    path = config_path(cwd)
    existing = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if not isinstance(existing, dict):
            existing = {}
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        existing = {}
    data = {**existing, **asdict(prefs)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
