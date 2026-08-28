"""
Covers ios_bot/utils/translation.py -- the retry/validation wrapper added
after deep_translator's GoogleTranslator was found returning Google's own
HTML error page as if it were a successful translation (confirmed live,
roughly 1 in 3 requests) instead of raising. These tests use a fake
GoogleTranslator so they don't depend on that flaky live endpoint.
"""
import pytest

from conftest import load_module_from_file


def _new_translation(monkeypatch, responses):
    """responses: list of strings, one per .translate() call, in order."""
    module = load_module_from_file("ios_bot/utils/translation.py")
    calls = {"count": 0}

    class FakeGoogleTranslator:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            value = responses[calls["count"]]
            calls["count"] += 1
            return value

    monkeypatch.setattr(module, "GoogleTranslator", FakeGoogleTranslator)
    return module, calls


def test_looks_like_failure_flags_html_error_page():
    module = load_module_from_file("ios_bot/utils/translation.py")
    assert module._looks_like_failure("<html><body>oops</body></html>") is True
    assert module._looks_like_failure("Error 500 (Server Error)!!1500.") is True
    assert module._looks_like_failure("") is True
    assert module._looks_like_failure(None) is True


def test_looks_like_failure_accepts_real_translation():
    module = load_module_from_file("ios_bot/utils/translation.py")
    assert module._looks_like_failure("Hola mundo, ¿cómo estás hoy?") is False


@pytest.mark.asyncio
async def test_succeeds_immediately_without_retry(monkeypatch):
    module, calls = _new_translation(monkeypatch, ["Hola mundo"])
    result = await module.translate_text("hello world", target="es", retry_delay_seconds=0)
    assert result == "Hola mundo"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_retries_past_an_error_page_response(monkeypatch):
    module, calls = _new_translation(
        monkeypatch,
        ["Error 500 (Server Error)!!1500.", "Hola mundo"],
    )
    result = await module.translate_text("hello world", target="es", retry_delay_seconds=0)
    assert result == "Hola mundo"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_raises_after_exhausting_all_attempts(monkeypatch):
    module, calls = _new_translation(
        monkeypatch,
        ["Error 500 (Server Error)!!1500."] * 3,
    )
    with pytest.raises(ValueError):
        await module.translate_text("hello world", target="es", attempts=3, retry_delay_seconds=0)
    assert calls["count"] == 3
