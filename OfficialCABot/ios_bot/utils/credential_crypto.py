"""
Symmetric encryption for secrets stored at rest in the operational DB --
currently just game server RCON/SSH passwords (ios_bot/db/servers.py).

Uses Fernet (AES-128-CBC + HMAC-SHA256, authenticated) from `cryptography`.
The key lives in CREDENTIAL_ENCRYPTION_KEY (.env), generated once via:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Losing this key means every stored server password becomes unrecoverable --
back it up the same way you'd treat SUPABASE_DB_URL.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None
_warned_missing_key = False


def _get_fernet() -> Fernet | None:
    global _fernet, _warned_missing_key
    if _fernet is not None:
        return _fernet

    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        if not _warned_missing_key:
            print(
                "⚠️ CREDENTIAL_ENCRYPTION_KEY is not set -- server credentials cannot be "
                "encrypted or decrypted. Generate one with: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\" and add it to .env."
            )
            _warned_missing_key = True
        return None

    try:
        _fernet = Fernet(key.encode("utf-8"))
    except Exception as e:
        print(f"⚠️ CREDENTIAL_ENCRYPTION_KEY is set but invalid ({e}) -- server credentials cannot be encrypted or decrypted.")
        return None
    return _fernet


def encrypt_secret(plaintext: str | None) -> str | None:
    """Encrypt a value for storage. Returns None for empty input (nothing to
    encrypt), and returns the PLAINTEXT unchanged (with a loud warning) if no
    encryption key is configured -- fails open rather than silently losing
    the value, since a missing key at this point is a deploy misconfiguration
    the operator needs to notice and fix, not a reason to lose server access.
    """
    if not plaintext:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(stored_value: str | None) -> str | None:
    """Decrypt a value read back from storage. Returns None for empty input.
    If the value doesn't look like a Fernet token (e.g. it's still legacy
    plaintext from before this was wired up) or decryption otherwise fails,
    returns the stored value AS-IS rather than raising -- RCON/SSH auth
    should fail with a clear "wrong password" from the actual connection
    attempt, not an opaque crash here.
    """
    if not stored_value:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return stored_value
    try:
        return fernet.decrypt(stored_value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return stored_value
