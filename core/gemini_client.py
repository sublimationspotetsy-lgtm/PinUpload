"""
core/gemini_client.py

Wraps the google-genai SDK for structured-output pin generation.
Handles retry/exponential-backoff on 429s and schema validation errors.

The caller receives a validated PinBatch or a GeminiError is raised —
no partial state is ever returned.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from google import genai
from google.genai import types
from pydantic import ValidationError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.pin_schema import PIN_BATCH_JSON_SCHEMA, SYSTEM_PROMPT, PinBatch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class GeminiError(Exception):
    """Raised when Gemini generation fails after all retries are exhausted."""

    def __init__(self, keyword: str, attempts: int, last_error: Exception) -> None:
        self.keyword = keyword
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Gemini generation failed for '{keyword}' after {attempts} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        )


# ---------------------------------------------------------------------------
# User/turn prompt template
# ---------------------------------------------------------------------------

def _build_user_prompt(keyword: str, slug: str, notes: str | None) -> str:
    notes_str = notes if notes else "none"
    return (
        f'TARGET_KEYWORD: "{keyword}"\n'
        f'BASE_SLUG: "{slug}"\n'
        f'NOTES: "{notes_str}"\n\n'
        "Generate the 10 pins now."
    )


# ---------------------------------------------------------------------------
# Retry callbacks
# ---------------------------------------------------------------------------

def _is_retryable(exc: BaseException) -> bool:
    """Return True for exceptions worth retrying."""
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return True
    # Catch Gemini 429s / quota errors — google-genai raises these as
    # google.api_core.exceptions.ResourceExhausted or similar; also catch
    # any generic Exception that has a status code of 429.
    exc_str = str(exc).lower()
    if "429" in exc_str or "quota" in exc_str or "rate" in exc_str:
        return True
    return False


# ---------------------------------------------------------------------------
# Core generation function
# ---------------------------------------------------------------------------

def generate_pins(
    keyword: str,
    slug: str,
    notes: str | None,
    model: str,
    temperature: float,
    api_key: str,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> PinBatch:
    """Call Gemini with structured output and return a validated PinBatch.

    Args:
        keyword: The target keyword string.
        slug: The keyword slug (used as BASE_SLUG in the prompt).
        notes: Optional context notes for Gemini.
        model: Gemini model ID string from config.
        temperature: Generation temperature from config.
        api_key: GEMINI_API_KEY value.
        on_retry: Optional callback(attempt_number, exception) called before
                  each retry — used to push messages to the Streamlit UI.

    Returns:
        A validated PinBatch with exactly 10 pins.

    Raises:
        GeminiError: After all retry attempts are exhausted.
    """
    client = genai.Client(api_key=api_key)
    user_prompt = _build_user_prompt(keyword, slug, notes)

    generation_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=PIN_BATCH_JSON_SCHEMA,
        temperature=temperature,
    )

    attempt = 0
    last_exc: Exception = RuntimeError("No attempt made")

    # Manual retry loop so we can call on_retry and track attempt count
    # clearly, rather than burying it inside a tenacity decorator where the
    # callback API is more complex to wire to Streamlit.
    max_attempts = 4
    wait_seconds = [4, 8, 16, 32]  # exponential backoff, capped at 32s

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=generation_config,
            )
            raw_text = response.text
            # Second validation layer — Pydantic re-validates even though
            # Gemini applied response_schema, catching subtle field errors.
            pin_batch = PinBatch.model_validate_json(raw_text)
            return pin_batch

        except (ValidationError, json.JSONDecodeError) as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d failed for '%s': schema/parse error: %s",
                attempt, max_attempts, keyword, exc,
            )
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc):
                # Non-retryable error — fail immediately.
                raise GeminiError(keyword, attempt, exc) from exc
            logger.warning(
                "Attempt %d/%d failed for '%s': %s",
                attempt, max_attempts, keyword, exc,
            )

        if attempt < max_attempts:
            wait = wait_seconds[attempt - 1]
            if on_retry:
                on_retry(attempt, last_exc)
            import time
            time.sleep(wait)

    raise GeminiError(keyword, max_attempts, last_exc)
