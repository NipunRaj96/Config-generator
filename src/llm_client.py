"""
llm_client.py
─────────────
Shared Gemini → Groq fallback LLM caller.

DRY principle: previously each step that needed LLM (LLMReasoner, LOCRGXGenerator,
XPathSRPGenerator) would have duplicated this Gemini/Groq chain. This module
extracts it into one place.

Design:
  - Lazy init: clients created only on first .call() — zero cost if not reached
  - Stateless between requests: safe to share across pipeline steps
  - Groq triggered on: 429, RESOURCE_EXHAUSTED, 503, UNAVAILABLE, rate limit keywords
  - Returns None if both providers fail (caller decides what to do)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL,
)

logger = logging.getLogger(__name__)

# Error keywords that trigger Groq fallback
_RATE_LIMIT_SIGNALS = frozenset({
    "429", "503", "resource_exhausted", "rate limit", "quota", "unavailable",
    "overloaded", "too many requests",
})

_RETRY_DELAY_S = 2   # seconds to wait before Groq fallback


class LLMClient:
    """
    Gemini-primary, Groq-fallback LLM caller.

    Thread-safe after init (providers are stateless per-request).
    Lazy: Gemini/Groq clients are initialised only on first call().
    """

    def __init__(
        self,
        gemini_model: str = GEMINI_MODEL,
        groq_model: str   = GROQ_MODEL,
        temperature: float = 0.1,
    ) -> None:
        self._gemini_model   = gemini_model
        self._groq_model     = groq_model
        self._temperature    = temperature
        
        raw_keys = GEMINI_API_KEY or ""
        self._gemini_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not self._gemini_keys:
            self._gemini_keys = [""]
        self._gemini_clients = [None] * len(self._gemini_keys)
        self._current_key_idx = 0
        
        self._groq_client    = None   # lazy

    # ── Public API ──────────────────────────────────────────────────────────────

    def call(self, prompt: str, temperature: Optional[float] = None) -> Optional[str]:
        """
        Send prompt to LLM. Returns raw text response, or None on total failure.

        Tries Gemini first. Falls back to Groq on rate-limit / server errors.
        """
        temp = temperature if temperature is not None else self._temperature

        response = self._call_gemini(prompt, temp)
        if response is not None:
            return response

        logger.info("LLMClient: Gemini failed — trying Groq fallback")
        time.sleep(_RETRY_DELAY_S)
        return self._call_groq(prompt, temp)

    # ── Gemini ──────────────────────────────────────────────────────────────────

    def _extract_retry_delay(self, exc: Exception) -> Optional[float]:
        try:
            import re
            exc_str = str(exc)
            # Match "Please retry in 35.96s" or similar
            match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", exc_str, re.IGNORECASE)
            if match:
                return float(match.group(1))
            # Match "retryDelay': '35s'" or similar from JSON details
            match = re.search(r"retryDelay':\s*'(\d+)(?:\.\d+)?s'", exc_str, re.IGNORECASE)
            if match:
                return float(match.group(1))
            # Match retry_after / retry-after
            match = re.search(r"retry[-_]after\D*(\d+)", exc_str, re.IGNORECASE)
            if match:
                return float(match.group(1))
            # Match minutes and seconds like "4m31.296s"
            match = re.search(r"(?:(\d+)m)?(\d+(?:\.\d+)?)s", exc_str, re.IGNORECASE)
            if match:
                minutes = float(match.group(1)) if match.group(1) else 0.0
                seconds = float(match.group(2))
                return minutes * 60.0 + seconds
        except Exception:
            pass
        return None

    def _call_gemini(self, prompt: str, temperature: float) -> Optional[str]:
        num_keys = len(self._gemini_keys)
        max_attempts = max(3, num_keys * 2)
        base_delay = 2.0
        
        # Keep track of keys we've tried in this invocation to avoid looping infinitely
        keys_tried = set()
        
        for attempt in range(max_attempts):
            if not self._gemini_keys:
                logger.error("LLMClient: No Gemini API keys configured.")
                return None
                
            idx = self._current_key_idx % num_keys
            
            try:
                client = self._get_gemini_client()
                response = client.models.generate_content(
                    model=self._gemini_model,
                    contents=prompt,
                    config={"temperature": temperature},
                )
                text = response.text.strip()
                # Detect soft rate-limit responses
                if any(s in text.lower() for s in _RATE_LIMIT_SIGNALS):
                    logger.warning("LLMClient: Gemini returned rate-limit-like text (attempt %d/%d)", attempt + 1, max_attempts)
                    
                    # Try rotating key if we have others
                    if num_keys > 1 and len(keys_tried) < num_keys:
                        keys_tried.add(idx)
                        self._current_key_idx = (self._current_key_idx + 1) % num_keys
                        logger.info("LLMClient: Rotating to Gemini key index %d due to soft rate limit", self._current_key_idx)
                        continue
                        
                    # If all keys tried or only one key, parse retry delay
                    retry_delay = self._extract_retry_delay(Exception(text))
                    if retry_delay:
                        retry_delay = min(retry_delay, 60.0)
                        logger.info("LLMClient: Gemini requested retry after %.1fs", retry_delay)
                        time.sleep(retry_delay)
                        continue
                        
                    if any(s in text.lower() for s in ("quota", "daily")) and "retry" not in text.lower():
                        logger.warning("LLMClient: Gemini soft quota/daily limit hit — failing immediately to trigger Groq fallback")
                        return None
                        
                    if attempt < max_attempts - 1:
                        time.sleep(base_delay)
                        base_delay *= 2.0
                        continue
                    return None
                logger.debug("LLMClient: Gemini responded (%d chars)", len(text))
                return text
            except Exception as exc:
                exc_str = str(exc)
                exc_lower = exc_str.lower()
                is_rate_limit = any(s in exc_lower for s in _RATE_LIMIT_SIGNALS) or "503" in exc_lower or "unavailable" in exc_lower
                
                if is_rate_limit:
                    logger.warning("LLMClient: Gemini rate-limited/unavailable (%s) (attempt %d/%d)", exc, attempt + 1, max_attempts)
                    
                    # Try rotating key if we have others
                    if num_keys > 1 and len(keys_tried) < num_keys:
                        keys_tried.add(idx)
                        self._current_key_idx = (self._current_key_idx + 1) % num_keys
                        logger.info("LLMClient: Rotating to Gemini key index %d due to rate limit/error", self._current_key_idx)
                        continue
                else:
                    logger.warning("LLMClient: Gemini error (%s) (attempt %d/%d)", exc, attempt + 1, max_attempts)
                
                # Check for requested retry delay first to handle transient rate limits
                retry_delay = self._extract_retry_delay(exc)
                if retry_delay:
                    retry_delay = min(retry_delay, 60.0)
                    logger.info("LLMClient: Gemini requested retry after %.1fs (transient limit)", retry_delay)
                    time.sleep(retry_delay)
                    continue
                
                # Check if it is a permanent quota limit
                if any(s in exc_lower for s in ("quota", "daily")) and "retry in" not in exc_lower:
                    logger.warning("LLMClient: Gemini permanent quota/daily limit hit — failing immediately to trigger Groq fallback")
                    return None
                
                if attempt < max_attempts - 1:
                    sleep_time = min(base_delay, 30.0)
                    logger.info("LLMClient: Sleeping %.1fs before retry", sleep_time)
                    time.sleep(sleep_time)
                    base_delay *= 2.0
                    continue
                return None

    def _get_gemini_client(self):
        if not self._gemini_keys:
            raise RuntimeError("GEMINI_API_KEY not set")
        idx = self._current_key_idx % len(self._gemini_keys)
        if self._gemini_clients[idx] is None:
            from google import genai  # local import — lazy
            self._gemini_clients[idx] = genai.Client(api_key=self._gemini_keys[idx])
            logger.info("LLMClient: Gemini client initialized for key index %d (ending in %s)", idx, self._gemini_keys[idx][-6:])
        return self._gemini_clients[idx]

    # ── Groq ────────────────────────────────────────────────────────────────────

    def _call_groq(self, prompt: str, temperature: float) -> Optional[str]:
        models_to_try = [self._groq_model]
        # Append fallback models if they are not already the primary model
        for m in ["qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.1-8b-instant"]:
            if m != self._groq_model:
                models_to_try.append(m)

        for model in models_to_try:
            logger.info("LLMClient: calling Groq model %s", model)
            max_retries = 3
            delay = 2.0
            for attempt in range(max_retries):
                try:
                    client = self._get_groq_client()
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=2048,
                    )
                    text = resp.choices[0].message.content.strip()
                    if any(s in text.lower() for s in _RATE_LIMIT_SIGNALS):
                        logger.warning("LLMClient: Groq returned rate-limit-like text (attempt %d/%d)", attempt + 1, max_retries)
                        if any(s in text.lower() for s in ("tpd", "tokens per day", "rpd", "requests per day", "quota")):
                            logger.warning("LLMClient: Groq model %s daily limit exceeded — failing over to next model", model)
                            break  # try next model
                        retry_delay = self._extract_retry_delay(Exception(text))
                        if retry_delay:
                            logger.info("LLMClient: Groq requested retry after %.1fs", retry_delay)
                            time.sleep(retry_delay)
                            continue
                        if attempt < max_retries - 1:
                            time.sleep(delay)
                            delay *= 2
                            continue
                        break  # try next model
                    logger.debug("LLMClient: Groq responded (%d chars)", len(text))
                    return text
                except Exception as exc:
                    exc_lower = str(exc).lower()
                    is_rate_limit = any(s in exc_lower for s in _RATE_LIMIT_SIGNALS)
                    if is_rate_limit:
                        logger.warning("LLMClient: Groq rate-limited (%s) (attempt %d/%d)", exc, attempt + 1, max_retries)
                    else:
                        logger.warning("LLMClient: Groq error (%s) (attempt %d/%d)", exc, attempt + 1, max_retries)
                    
                    # Check if it's a daily limit error (TPD / RPD / quota)
                    if any(s in exc_lower for s in ("tpd", "tokens per day", "rpd", "requests per day", "quota")):
                        logger.warning("LLMClient: Groq model %s daily limit exceeded — failing over to next model", model)
                        break  # try next model
                    
                    # Check for requested retry delay
                    retry_delay = self._extract_retry_delay(exc)
                    if retry_delay:
                        # Cap at 45 seconds to avoid long hangs
                        retry_delay = min(retry_delay, 45.0)
                        logger.info("LLMClient: Groq requested retry after %.1fs", retry_delay)
                        time.sleep(retry_delay)
                        continue
                    
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    break  # try next model
        return None

    def _get_groq_client(self):
        if self._groq_client is None:
            if not GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY not set")
            from openai import OpenAI  # local import — lazy
            self._groq_client = OpenAI(
                api_key=GROQ_API_KEY,
                base_url=GROQ_BASE_URL,
            )
            logger.info("LLMClient: Groq client initialised")
        return self._groq_client
