"""
Wraps deep_translator's GoogleTranslator with the validation and retry it
doesn't do itself. Google's free/unofficial translate endpoint intermittently
returns its own HTML error page instead of a translation -- deep_translator
doesn't treat that as a failure, so callers get the raw error page text back
as if it were a real translation. Confirmed intermittent (roughly 1 in 3
requests in testing), not a persistent outage, so a couple of quick retries
recovers most of the time.
"""
from __future__ import annotations

import asyncio

from deep_translator import GoogleTranslator

_FAILURE_MARKERS = ("<html", "error 500", "server error", "1500.")


def _looks_like_failure(text: str | None) -> bool:
    if not text:
        return True
    lowered = text.strip().lower()
    if lowered.startswith("<"):
        return True
    return any(marker in lowered for marker in _FAILURE_MARKERS)


async def translate_text(text: str, target: str, *, attempts: int = 3, retry_delay_seconds: float = 1.0) -> str:
    """Translate `text` to `target`, retrying past Google's intermittent
    error-page responses. Raises ValueError if every attempt fails."""
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
