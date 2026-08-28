"""
Covers ios_bot/utils/translation.py -- the retry/fallback/chunking wrapper
added after deep_translator's GoogleTranslator was found returning Google's
own HTML error page as if it were a successful translation (confirmed live,
roughly 1 in 3 requests, and separately confirmed capable of a sustained bad
patch where every retry against Google fails identically). These tests use
fake translator backends so they don't depend on either live, flaky, rate-
limited free endpoint.
"""
import pytest

from conftest import load_module_from_file


def _fake_google(responses):
    calls = {"count": 0}

    class FakeGoogleTranslator:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            value = responses[calls["count"]]
            calls["count"] += 1
            return value

    return FakeGoogleTranslator, calls


def _fake_mymemory(response_or_responses):
    calls = {"count": 0, "texts": []}

    class FakeMyMemoryTranslator:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            calls["texts"].append(text)
            value = (
                response_or_responses[calls["count"]]
                if isinstance(response_or_responses, list)
                else response_or_responses
            )
            calls["count"] += 1
            return value

    return FakeMyMemoryTranslator, calls


def _new_translation(monkeypatch, *, google_responses=None, mymemory_response="ok"):
    module = load_module_from_file("ios_bot/utils/translation.py")
    google_calls = {"count": 0}
    if google_responses is not None:
        FakeGoogle, google_calls = _fake_google(google_responses)
        monkeypatch.setattr(module, "GoogleTranslator", FakeGoogle)
    FakeMyMemory, mymemory_calls = _fake_mymemory(mymemory_response)
    monkeypatch.setattr(module, "MyMemoryTranslator", FakeMyMemory)
    return module, google_calls, mymemory_calls


def test_looks_like_failure_flags_html_error_page():
    module = load_module_from_file("ios_bot/utils/translation.py")
    assert module._looks_like_failure("<html><body>oops</body></html>") is True
    assert module._looks_like_failure("Error 500 (Server Error)!!1500.") is True
    assert module._looks_like_failure("") is True
    assert module._looks_like_failure(None) is True


def test_looks_like_failure_accepts_real_translation():
    module = load_module_from_file("ios_bot/utils/translation.py")
    assert module._looks_like_failure("Hola mundo, ¿cómo estás hoy?") is False


def test_chunk_text_leaves_short_text_untouched():
    module = load_module_from_file("ios_bot/utils/translation.py")
    assert module._chunk_text("short text") == ["short text"]


def test_chunk_text_splits_long_text_under_the_limit_without_losing_content():
    module = load_module_from_file("ios_bot/utils/translation.py")
    long_text = ("Hola, como estas hoy? Espero que todo vaya bien. " * 30).strip()
    chunks = module._chunk_text(long_text, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).replace(" ", "") == long_text.replace(" ", "")


def test_chunk_text_splits_a_single_oversized_sentence_on_words():
    module = load_module_from_file("ios_bot/utils/translation.py")
    one_long_sentence = "palabra " * 200  # no periods -- forces the word-boundary fallback
    chunks = module._chunk_text(one_long_sentence.strip(), max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_succeeds_via_google_without_needing_fallback(monkeypatch):
    module, google_calls, mymemory_calls = _new_translation(monkeypatch, google_responses=["Hola mundo"])
    result = await module.translate_text("hello world", target="es", retry_delay_seconds=0)
    assert result == "Hola mundo"
    assert google_calls["count"] == 1
    assert mymemory_calls["count"] == 0


@pytest.mark.asyncio
async def test_google_recovers_after_one_error_page_retry(monkeypatch):
    module, google_calls, mymemory_calls = _new_translation(
        monkeypatch, google_responses=["Error 500 (Server Error)!!1500.", "Hola mundo"]
    )
    result = await module.translate_text("hello world", target="es", retry_delay_seconds=0)
    assert result == "Hola mundo"
    assert google_calls["count"] == 2
    assert mymemory_calls["count"] == 0


@pytest.mark.asyncio
async def test_falls_back_to_mymemory_after_google_exhausts_retries(monkeypatch):
    module, google_calls, mymemory_calls = _new_translation(
        monkeypatch,
        google_responses=["Error 500 (Server Error)!!1500."] * 3,
        mymemory_response="Hola mundo",
    )
    result = await module.translate_text("hello world", target="es", attempts=3, retry_delay_seconds=0)
    assert result == "Hola mundo"
    assert google_calls["count"] == 3
    assert mymemory_calls["count"] == 1


@pytest.mark.asyncio
async def test_fallback_chunks_long_text_across_multiple_mymemory_calls(monkeypatch):
    long_text = ("Hola, como estas hoy? Espero que todo vaya bien. " * 30).strip()
    module, google_calls, mymemory_calls = _new_translation(
        monkeypatch,
        google_responses=["Error 500 (Server Error)!!1500."] * 3,
        mymemory_response="translated chunk",
    )
    result = await module.translate_text(long_text, target="en", attempts=3, retry_delay_seconds=0)
    assert mymemory_calls["count"] > 1
    assert result == " ".join(["translated chunk"] * mymemory_calls["count"])
    assert all(len(text) <= 500 for text in mymemory_calls["texts"])


@pytest.mark.asyncio
async def test_raises_with_both_providers_failing(monkeypatch):
    module, google_calls, mymemory_calls = _new_translation(
        monkeypatch,
        google_responses=["Error 500 (Server Error)!!1500."] * 3,
        mymemory_response="Error 500 (Server Error)!!1500.",
    )
    with pytest.raises(ValueError):
        await module.translate_text("hello world", target="es", attempts=3, retry_delay_seconds=0)
