"""
Covers the encryption used for stored game-server RCON/SSH passwords
(ios_bot/db/servers.py). A regression here is exactly the kind of thing that
either silently leaves credentials unencrypted, or worse, makes them
permanently undecryptable (locking the bot out of every game server) --
high-stakes enough to be worth pinning down with tests, not just the manual
live-DB round-trip check done when this was first built.
"""
from cryptography.fernet import Fernet

from conftest import load_module_from_file


def _new_crypto(monkeypatch, key: str | None):
    if key is None:
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    return load_module_from_file("ios_bot/utils/credential_crypto.py")


def test_encrypt_then_decrypt_roundtrips(monkeypatch):
    crypto = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    ciphertext = crypto.encrypt_secret("hunter2")
    assert ciphertext != "hunter2"
    assert crypto.decrypt_secret(ciphertext) == "hunter2"


def test_encrypt_empty_string_returns_none(monkeypatch):
    crypto = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    assert crypto.encrypt_secret("") is None
    assert crypto.encrypt_secret(None) is None


def test_decrypt_empty_returns_none(monkeypatch):
    crypto = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    assert crypto.decrypt_secret("") is None
    assert crypto.decrypt_secret(None) is None


def test_decrypt_non_token_value_passes_through_unchanged(monkeypatch):
    """Legacy plaintext values written before encryption was wired up (or
    any other stored value that isn't a real Fernet token) must not crash
    decrypt_secret -- RCON should fail with a real "wrong password" from
    the actual connection attempt, not an opaque exception here."""
    crypto = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    assert crypto.decrypt_secret("plaintext-legacy-value") == "plaintext-legacy-value"


def test_missing_key_fails_open_to_plaintext(monkeypatch):
    """No CREDENTIAL_ENCRYPTION_KEY configured is a deploy misconfiguration
    the operator needs to notice (it warns loudly), but must not make server
    credentials unusable -- it should fail open, not closed."""
    crypto = _new_crypto(monkeypatch, None)
    assert crypto.encrypt_secret("hunter2") == "hunter2"
    assert crypto.decrypt_secret("hunter2") == "hunter2"


def test_wrong_key_does_not_raise_and_returns_ciphertext_unchanged(monkeypatch):
    crypto = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    ciphertext = crypto.encrypt_secret("hunter2")

    crypto_wrong_key = _new_crypto(monkeypatch, Fernet.generate_key().decode())
    # Can't decrypt with the wrong key -- should not raise, and since it's
    # not decryptable it's returned as-is rather than silently becoming "".
    assert crypto_wrong_key.decrypt_secret(ciphertext) == ciphertext
