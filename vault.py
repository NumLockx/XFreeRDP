#!/usr/bin/env python3
"""
vault.py — Encrypted-at-rest storage for XFreeRDP GUI profiles.

Design:
- Key derivation: PBKDF2-HMAC-SHA256, 600k iterations, per-write random salt.
- Encryption: SHA-256 in counter mode as a keystream generator (XOR).
- Integrity: HMAC-SHA256 over header+ciphertext (encrypt-then-MAC), verified
  with a constant-time comparison *before* any decryption happens.
- Files are written atomically (tmp file + os.replace) with mode 0600.

This is a deliberately simple stdlib-only construction. It protects profiles
at rest against casual reading and tampering, but for maximum assurance you
could swap the internals for `cryptography`'s Fernet (AES-128-CBC + HMAC)
without changing the public API below.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

MAGIC = b"XFRPVLT1"
KDF_ITERATIONS = 600_000
_MAX_KDF_ITERATIONS = 5_000_000  # sanity cap when reading untrusted files
_SALT_LEN = 16
_NONCE_LEN = 16
_KEY_LEN = 32
_MAC_LEN = 32
_BLOCK = 32


class VaultError(Exception):
    """Base class for vault failures."""


class VaultFormatError(VaultError):
    """The vault file is malformed or not a vault at all."""


class VaultAuthError(VaultError):
    """Wrong passphrase (MAC verification failed)."""


def vault_exists(path: os.PathLike | str) -> bool:
    return Path(path).exists()


def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    """Derive (encryption_key, mac_key) from the passphrase."""
    material = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, KDF_ITERATIONS, dklen=2 * _KEY_LEN
    )
    return material[:_KEY_LEN], material[_KEY_LEN:]


def _keystream_xor(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    """XOR `data` with a keystream: SHA-256(key || nonce || counter)."""
    out = bytearray(len(data))
    counter = 0
    for offset in range(0, len(data), _BLOCK):
        ctr = counter.to_bytes(8, "big")
        ks = hashlib.sha256(enc_key + nonce + ctr).digest()
        chunk = data[offset : offset + _BLOCK]
        out[offset : offset + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, ks))
        counter += 1
    return bytes(out)


def write_vault(path: os.PathLike | str, passphrase: str, payload: object) -> None:
    """Encrypt `payload` (must be JSON-serialisable) and write it atomically."""
    path = Path(path)
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    enc_key, mac_key = _derive_keys(passphrase, salt)

    plaintext = json.dumps(payload, indent=2).encode("utf-8")
    ciphertext = _keystream_xor(enc_key, nonce, plaintext)

    header = (
        MAGIC
        + KDF_ITERATIONS.to_bytes(4, "big")
        + salt
        + nonce
    )
    mac = hmac.new(mac_key, header + ciphertext, hashlib.sha256).digest()

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(header + mac + ciphertext)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)  # atomic on POSIX


def read_vault(path: os.PathLike | str, passphrase: str) -> dict:
    """Verify and decrypt the vault, returning the stored payload.

    Raises:
        VaultFormatError: file is not a readable vault.
        VaultAuthError:   passphrase is wrong or the file was tampered with.
    """
    blob = Path(path).read_bytes()

    fixed_len = len(MAGIC) + 4 + _SALT_LEN + _NONCE_LEN
    if len(blob) < fixed_len + _MAC_LEN or not blob.startswith(MAGIC):
        raise VaultFormatError("not a recognised vault file")

    iters = int.from_bytes(blob[len(MAGIC) : len(MAGIC) + 4], "big")
    if not 0 < iters <= _MAX_KDF_ITERATIONS:
        raise VaultFormatError("implausible KDF iteration count")

    off = len(MAGIC) + 4
    salt = blob[off : off + _SALT_LEN]
    off += _SALT_LEN
    nonce = blob[off : off + _NONCE_LEN]
    off += _NONCE_LEN
    stored_mac = blob[off : off + _MAC_LEN]
    header = blob[:off]
    ciphertext = blob[off + _MAC_LEN :]

    enc_key, mac_key = _derive_keys(passphrase, salt)
    expected_mac = hmac.new(mac_key, header + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(stored_mac, expected_mac):
        raise VaultAuthError("wrong passphrase or corrupted vault")

    plaintext = _keystream_xor(enc_key, nonce, ciphertext)
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultFormatError(f"decrypted payload is not valid JSON: {exc}") from exc
