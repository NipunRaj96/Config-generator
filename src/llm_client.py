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
from typing import Any, Optional

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL,
    NAUKRI_TOKEN, NAUKRI_EMAIL, NAUKRI_APP_ID, NAUKRI_SYSTEM_ID, NAUKRI_API_URL, NAUKRI_MODEL,
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

    # Class-level token tracking
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_calls: int = 0

    @classmethod
    def reset_token_counts(cls):
        cls.total_prompt_tokens = 0
        cls.total_completion_tokens = 0
        cls.total_calls = 0

    def __init__(
        self,
        gemini_model: str = GEMINI_MODEL,
        groq_model: str   = GROQ_MODEL,
        naukri_model: str = NAUKRI_MODEL,
        temperature: float = 0.1,
    ) -> None:
        self._gemini_model   = gemini_model
        self._groq_model     = groq_model
        self._naukri_model   = naukri_model
        self._temperature    = temperature
        
        raw_keys = GEMINI_API_KEY or ""
        self._gemini_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not self._gemini_keys:
            self._gemini_keys = [""]
        self._gemini_clients = [None] * len(self._gemini_keys)
        self._current_key_idx = 0
        
        self._groq_client    = None   # lazy

    # ── Public API ──────────────────────────────────────────────────────────────

    def call(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        model: Optional[str] = None
    ) -> Optional[str]:
        """
        Send prompt to LLM. Returns raw text response, or None on failure.
        Only uses Naukri API Gateway. Gemini and Groq fallbacks are disabled.
        """
        temp = temperature if temperature is not None else self._temperature
        naukri_model = model if model is not None else self._naukri_model

        if NAUKRI_TOKEN and NAUKRI_TOKEN.strip():
            logger.info("LLMClient: Calling Naukri Gen-AI API Gateway")
            response = self._call_naukri(prompt, temp, naukri_model)
            if response is not None:
                return response
            logger.error("LLMClient: Naukri Gen-AI API Gateway request failed.")
        else:
            logger.error("LLMClient: NAUKRI_TOKEN is not configured.")

        return None

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
                # Track tokens
                try:
                    if getattr(response, "usage_metadata", None):
                        p_tok = response.usage_metadata.prompt_token_count or 0
                        c_tok = response.usage_metadata.candidates_token_count or 0
                        LLMClient.total_prompt_tokens += p_tok
                        LLMClient.total_completion_tokens += c_tok
                        LLMClient.total_calls += 1
                        logger.debug("LLMClient: Gemini tokens: prompt=%d, completion=%d", p_tok, c_tok)
                except Exception as usage_err:
                    logger.debug("LLMClient: failed to extract Gemini token usage: %s", usage_err)

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
                    # Track tokens
                    try:
                        if getattr(resp, "usage", None):
                            p_tok = resp.usage.prompt_tokens or 0
                            c_tok = resp.usage.completion_tokens or 0
                            LLMClient.total_prompt_tokens += p_tok
                            LLMClient.total_completion_tokens += c_tok
                            LLMClient.total_calls += 1
                            logger.debug("LLMClient: Groq tokens: prompt=%d, completion=%d", p_tok, c_tok)
                    except Exception as usage_err:
                        logger.debug("LLMClient: failed to extract Groq token usage: %s", usage_err)

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

    # ── Naukri Gen-AI API Gateway ────────────────────────────────────────────────
    
    def _call_naukri(self, prompt: str, temperature: float, model: str) -> Optional[str]:
        import requests  # local import — lazy
        
        auth_header = NAUKRI_TOKEN if NAUKRI_TOKEN.lower().startswith("bearer ") else f"Bearer {NAUKRI_TOKEN}"
        
        headers = {
            "AppId": NAUKRI_APP_ID,
            "Authorization": auth_header,
            "SystemId": NAUKRI_SYSTEM_ID,
            "email": NAUKRI_EMAIL,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Accept": "*/*"
        }
        
        payload = {
            "conversationId": None,
            "model": model,
            "message": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "temperature": temperature,
            "contextSize": 10,
            "templateId": 955,
            "saveContext": False,
            "systemMessage": "",
            "thinkingMode": True
        }
        
        max_retries = 3
        delay = 2.0
        
        for attempt in range(max_retries):
            try:
                logger.info("LLMClient: calling Naukri Gen-AI API with model %s (attempt %d/%d)", payload["model"], attempt + 1, max_retries)
                resp = requests.post(NAUKRI_API_URL, headers=headers, json=payload, timeout=60)
                
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        text = self._extract_naukri_text(data)
                        if text:
                            # Track tokens
                            try:
                                tok = 0
                                if "data" in data and isinstance(data["data"], dict) and "usageData" in data["data"]:
                                    usage_data = data["data"]["usageData"]
                                    if isinstance(usage_data, dict):
                                        tok = usage_data.get("tokenUsed") or 0
                                
                                if tok > 0:
                                    LLMClient.total_completion_tokens += tok
                                    LLMClient.total_calls += 1
                                    logger.debug("LLMClient: Naukri tokens: %d", tok)
                                else:
                                    # Fallback to standard OpenAI paths
                                    usage = None
                                    if "usage" in data:
                                        usage = data["usage"]
                                    elif "data" in data and isinstance(data["data"], dict) and "usage" in data["data"]:
                                        usage = data["data"]["usage"]
                                    
                                    if usage and isinstance(usage, dict):
                                        p_tok = usage.get("prompt_tokens") or usage.get("promptTokenCount") or 0
                                        c_tok = usage.get("completion_tokens") or usage.get("completionTokenCount") or 0
                                        LLMClient.total_prompt_tokens += p_tok
                                        LLMClient.total_completion_tokens += c_tok
                                        LLMClient.total_calls += 1
                                        logger.debug("LLMClient: Naukri tokens: prompt=%d, completion=%d", p_tok, c_tok)
                            except Exception as usage_err:
                                logger.debug("LLMClient: failed to extract Naukri token usage: %s", usage_err)

                            logger.info("LLMClient: Naukri Gen-AI responded successfully (%d chars)", len(text))
                            return text
                        else:
                            logger.warning("LLMClient: Naukri response parsed to empty text. Raw: %s", resp.text[:200])
                    except Exception as e:
                        logger.warning("LLMClient: Failed to parse Naukri JSON response: %s", e)
                elif resp.status_code == 429:
                    logger.warning("LLMClient: Naukri Gen-AI rate limited (429)")
                    retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                    sleep_time = 15.0
                    if retry_after:
                        try:
                            sleep_time = float(retry_after)
                        except ValueError:
                            pass
                    logger.info("LLMClient: Rate limit sleep for %.1fs", sleep_time)
                    time.sleep(sleep_time)
                else:
                    logger.warning("LLMClient: Naukri Gen-AI returned HTTP %d: %s", resp.status_code, resp.text[:200])
                    
            except Exception as e:
                logger.warning("LLMClient: Naukri Gen-AI connection error: %s", e)
                
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                
        return None

    def _extract_naukri_text(self, data: Any) -> Optional[str]:
        if not data:
            return None
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            # 0. Check specifically for Naukri Gen-AI API gateway custom nested shape:
            # {"data": {"message": {"text": {"response": "..."}}}}
            try:
                if "data" in data and isinstance(data["data"], dict):
                    sub = data["data"]
                    if "message" in sub and isinstance(sub["message"], dict):
                        msg_obj = sub["message"]
                        if "text" in msg_obj and isinstance(msg_obj["text"], dict):
                            text_obj = msg_obj["text"]
                            if "response" in text_obj and isinstance(text_obj["response"], str):
                                return text_obj["response"].strip()
            except Exception:
                pass

            # 1. Check for standard OpenAI format inside choices
            if "choices" in data:
                choices = data["choices"]
                if isinstance(choices, list) and len(choices) > 0:
                    first = choices[0]
                    if isinstance(first, dict):
                        if "message" in first and isinstance(first["message"], dict):
                            return first["message"].get("content", "").strip()
                        if "text" in first:
                            return first["text"].strip()
            # 2. Check for nested 'data' key
            if "data" in data:
                res = self._extract_naukri_text(data["data"])
                if res:
                    return res
            # 3. Check for 'message' key
            if "message" in data:
                msg = data["message"]
                if isinstance(msg, str):
                    return msg.strip()
                if isinstance(msg, dict):
                    return msg.get("content", "").strip()
            # 4. Other common keys
            for key in ["reply", "response", "result", "text", "content"]:
                if key in data and isinstance(data[key], str):
                    return data[key].strip()
        return None
