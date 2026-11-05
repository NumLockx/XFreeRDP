#!/usr/bin/env python3
"""
common.py — Shared paths, safe file I/O and input validation for XFreeRDP GUI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "xfreerdp-gui"
VAULT_FILE = CONFIG_DIR / "profiles.vault"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
LEGACY_PROFILES_FILE = CONFIG_DIR / "profiles.json"


def ensure_config_dir() -> None:
    """Create the config dir and restrict it to the current user."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON atomically (tmp + rename) with owner-only permissions."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)  # atomic on POSIX


def load_json(path: Path) -> tuple[dict | None, bool]:
    """Load a JSON file.

    Returns (data, corrupt). data is None if the file is missing or corrupt;
    corrupt is True only when the file exists but could not be parsed.
    """
    if not path.exists():
        return None, False
    try:
        with open(path) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, True
        return data, False
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, True


def backup_corrupt_file(path: Path) -> Path | None:
    """Rename a corrupt file to *.corrupt-<n> so it is preserved, not destroyed."""
    if not path.exists():
        return None
    for n in range(1, 1000):
        target = path.with_name(f"{path.name}.corrupt-{n}")
        if not target.exists():
            try:
                path.rename(target)
                os.chmod(target, 0o600)
                return target
            except OSError:
                return None
    return None


# ── Input validation ────────────────────────────────────────────────────────

def validate_port(text: str) -> int | None:
    """Return a valid TCP port number, or None if invalid."""
    text = text.strip()
    if not text:
        return None
    try:
        port = int(text)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def validate_int(text: str, lo: int, hi: int) -> int | None:
    """Return the parsed integer if it lies within [lo, hi], else None."""
    text = text.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if lo <= value <= hi else None


def mask_argv(argv: list[str]) -> str:
    """Render a command list for display, hiding any /p:<password> value."""
    masked = []
    for arg in argv:
        if arg.startswith("/p:"):
            masked.append("/p:●●●●●●●●")
        else:
            masked.append(arg)
    return " ".join(masked)


def has_legacy_profiles() -> bool:
    """True if an old plaintext profiles.json exists."""
    return LEGACY_PROFILES_FILE.exists()
