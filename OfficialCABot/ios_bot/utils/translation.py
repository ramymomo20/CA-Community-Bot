"""
Wraps deep_translator with the validation, retry, and fallback it doesn't do
itself.

Google's free/unofficial translate endpoint doesn't just fail intermittently
-- it can also enter a sustained bad patch (confirmed live: 4/4 and then 6/6
consecutive failures within the same short test session, unrelated to text
length) where retrying the same endpoint accomplishes nothing. When it's in
that state, MyMemoryTranslator (a genuinely separate free translation
service, not another Google scrape) has worked reliably in testing, so it's
used as a fallback rather than a second attempt at the same broken endpoint.

MyMemory has a hard 500-character-per-request limit (raises NotValidLength
above that, unlike Google's endpoint, which silently hands back its error
page) and requires an explicit source language instead of auto-detect, so
longer text gets chunked on sentence/word boundaries and translated piece by
piece.
"""
from __future__ import annotations

import asyncio
import re

from deep_translator import GoogleTranslator, MyMemoryTranslator

_FAILURE_MARKERS = ("<html", "error 500", "server error", "1500.")

# MyMemoryTranslator wants xx-XX region-qualified codes and doesn't support
# source="auto" -- these two commands only ever translate between Spanish
# and English, so the fallback source is just "whichever one isn't the
# target."
_MYMEMORY_LANG_CODES = {"es": "es-ES", "en": "en-US"}
_MYMEMORY_CHUNK_LIMIT = 500


def _looks_like_failure(text: str | None) -> bool:
    if not text:
        return True
    lowered = text.strip().lower()
    if lowered.startswith("<"):
        return True
    return any(marker in lowered for marker in _FAILURE_MARKERS)


def _chunk_text(text: str, max_chars: int = _MYMEMORY_CHUNK_LIMIT) -> list[str]:
    """Split on sentence boundaries first, falling back to word boundaries
    for any single sentence that's still over the limit on its own, then
    greedily repack the resulting pieces up to the limit."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    atoms: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(sentence) <= max_chars:
            atoms.append(sentence)
        else:
            atoms.extend(sentence.split(" "))

    packed: list[str] = []
    current = ""
    for atom in atoms:
        candidate = f"{current} {atom}".strip()
        if len(candidate) > max_chars:
            if current:
                packed.append(current)
            current = atom
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


async def _translate_via_google(text: str, target: str, *, attempts: int, retry_delay_seconds: float) -> str:
    last_result = ""
    for attempt in range(attempts):
        result = await asyncio.to_thread(
            lambda: GoogleTranslator(source="auto", target=target).translate(text)
        )
        if not _looks_like_failure(result):
            return result
        last_result = result
        if attempt < attempts - 1:
            await asyncio.sleep(retry_delay_seconds)

    raise ValueError(
        f"Google Translate kept returning an error response after {attempts} attempts: "
        f"{last_result[:200]!r}"
    )


async def _translate_via_mymemory(text: str, target: str) -> str:
    mymemory_target = _MYMEMORY_LANG_CODES.get(target)
    mymemory_source = _MYMEMORY_LANG_CODES.get("en" if target == "es" else "es")
    if not mymemory_target or not mymemory_source:
        raise ValueError(f"No MyMemory fallback language mapping for target={target!r}")

    translated_chunks = []
    for chunk in _chunk_text(text):
        result = await asyncio.to_thread(
            lambda c=chunk: MyMemoryTranslator(source=mymemory_source, target=mymemory_target).translate(c)
        )
        if _looks_like_failure(result):
            raise ValueError(f"MyMemory returned an unexpected response: {result[:200]!r}")
        translated_chunks.append(result)
    return " ".join(translated_chunks)


async def translate_text(text: str, target: str, *, attempts: int = 3, retry_delay_seconds: float = 1.0) -> str:
    """Translate `text` to `target` ("es" or "en"). Tries Google first
    (with retries, since its failures are usually transient); if Google is
    in a sustained bad patch, falls back to MyMemory. Raises ValueError only
    if both fail."""
    try:
        return await _translate_via_google(text, target, attempts=attempts, retry_delay_seconds=retry_delay_seconds)
    except Exception as google_error:
        try:
            return await _translate_via_mymemory(text, target)
        except Exception as mymemory_error:
            raise ValueError(
                f"Both translation providers failed. Google: {google_error} | MyMemory: {mymemory_error}"
            ) from mymemory_error
