#!/usr/bin/env python3
"""
storage.py — Settings persistence and the encrypted profile vault manager.

The vault passphrase is held in a mutable bytearray only while the vault is
unlocked, together with an expiry timestamp chosen by the user at unlock
time. When the TTL elapses (checked by the app) or the user locks manually,
the passphrase buffer is overwritten and decrypted profile data is dropped.
"""
from __future__ import annotations

import time
from typing import Any

from common import (
    LEGACY_PROFILES_FILE,
    SETTINGS_FILE,
    VAULT_FILE,
    atomic_write_json,
    backup_corrupt_file,
    load_json,
)
import vault
from vault import VaultAuthError, VaultFormatError


UNLOCKED = "ok"
WRONG_PASS = "wrong_pass"
CREATED = "created"
ERROR = "error"


class SettingsStore:
    """settings.json with atomic writes and corrupt-file preservation."""

    def __init__(self) -> None:
        data, corrupt = load_json(SETTINGS_FILE)
        self._corrupt_backup: str | None = None
        if corrupt:
            target = backup_corrupt_file(SETTINGS_FILE)
            self._corrupt_backup = str(target) if target else None
            data = None
        self.data: dict[str, Any] = data if data is not None else {}

    @property
    def corrupt_backup(self) -> str | None:
        return self._corrupt_backup

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        atomic_write_json(SETTINGS_FILE, self.data)


class ProfileVault:
    """Manages the encrypted profiles file and the unlock lifetime."""

    def __init__(self) -> None:
        self._profiles: dict[str, Any] | None = None
        self._expires: float = 0.0
        self._passphrase: bytearray | None = None

    # ── State ───────────────────────────────────────────────────────────
    @property
    def exists(self) -> bool:
        return VAULT_FILE.exists()

    @property
    def unlocked(self) -> bool:
        return (
            self._profiles is not None
            and self._passphrase is not None
            and time.monotonic() < self._expires
        )

    @property
    def profiles(self) -> dict[str, Any]:
        """Decrypted profiles. Callers must check `unlocked` first."""
        if not self.unlocked or self._profiles is None:
            return {}
        return self._profiles

    @property
    def seconds_left(self) -> int:
        if not self.unlocked:
            return 0
        return max(0, int(self._expires - time.monotonic()))

    # ── Unlock / lock ───────────────────────────────────────────────────
    def unlock(self, passphrase: str, ttl_seconds: int) -> str:
        """Verify the passphrase and open the vault for ttl_seconds.

        Returns UNLOCKED, CREATED (first run, vault was initialised),
        WRONG_PASS, or ERROR.
        """
        try:
            if self.exists:
                payload = vault.read_vault(VAULT_FILE, passphrase)
                if not isinstance(payload, dict):
                    return ERROR
                status = UNLOCKED
            else:
                payload = {}
                vault.write_vault(VAULT_FILE, passphrase, payload)
                status = CREATED
        except VaultAuthError:
            return WRONG_PASS
        except (VaultFormatError, OSError, ValueError):
            return ERROR

        self._hold_passphrase(passphrase)
        self._profiles = payload
        self._expires = time.monotonic() + max(60, ttl_seconds)
        return status

    def extend(self, ttl_seconds: int) -> None:
        if self.unlocked:
            self._expires = time.monotonic() + max(60, ttl_seconds)

    def lock(self) -> None:
        """Drop decrypted data and overwrite the passphrase buffer."""
        self._profiles = None
        self._expires = 0.0
        if self._passphrase is not None:
            for i in range(len(self._passphrase)):
                self._passphrase[i] = 0
            self._passphrase = None

    def _hold_passphrase(self, passphrase: str) -> None:
        # Needed in plaintext form only to re-encrypt on save.
        self._passphrase = bytearray(passphrase.encode("utf-8"))

    # ── Persistence ─────────────────────────────────────────────────────
    def save(self) -> bool:
        """Re-encrypt and write the vault. Requires an active unlock."""
        if not self.unlocked or self._passphrase is None or self._profiles is None:
            return False
        try:
            vault.write_vault(VAULT_FILE, self._passphrase.decode("utf-8"), self._profiles)
        except (OSError, ValueError):
            return False
        return True

    # ── Legacy plaintext migration ──────────────────────────────────────
    def load_legacy_profiles(self) -> dict[str, Any]:
        """Read the old plaintext profiles.json (if present)."""
        data, _corrupt = load_json(LEGACY_PROFILES_FILE)
        return data if data is not None else {}

    def retire_legacy_file(self) -> None:
        """Rename the old plaintext file so it can't silently linger."""
        target = LEGACY_PROFILES_FILE.with_suffix(".json.migrated")
        try:
            if LEGACY_PROFILES_FILE.exists():
                LEGACY_PROFILES_FILE.rename(target)
        except OSError:
            pass
