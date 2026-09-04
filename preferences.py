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
    """Persist preferences to the cwd config file."""
    path = config_path(cwd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(prefs), f, indent=2)
