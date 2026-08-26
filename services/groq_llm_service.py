"""
Groq-backed LLM adapter for strategic synthesis.

Recommended location:
    services/groq_llm_service.py

Purpose
-------
Expose the same small ``ask(...)`` contract used by the project's local
``LLMService`` while routing only the expensive strategic reasoning call to
GroqCloud.

The adapter is intentionally narrow:
- text in / text out;
- optional system prompt;
- JSON object mode when requested;
- no tools, browsing, code execution, or hidden chain-of-thought;
- no automatic provider fallback that could silently change epistemic behavior.

Environment
-----------
Required:
    GROQ_API_KEY

Optional:
    GROQ_STRATEGIC_MODEL=openai/gpt-oss-120b
    GROQ_STRATEGIC_TIMEOUT_SECONDS=60
    GROQ_STRATEGIC_REASONING_EFFORT=low
    GROQ_STRATEGIC_MAX_COMPLETION_TOKENS=800
    GROQ_STRATEGIC_RATE_LIMIT_RETRIES=2
    GROQ_STRATEGIC_RATE_LIMIT_MAX_WAIT_SECONDS=65

The Free plan for GPT-OSS 120B is rate-limited. Keep strategic prompts compact
and handle HTTP 429 as a normal capacity/rate-limit condition.
"""

from __future__ import annotations

import logging
import os
import re
import time
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


logger = logging.getLogger(__name__)


SERVICE_VERSION = "groq-llm-service-v1.1-rate-limit-resilient"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_COMPLETION_TOKENS = 800
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_RATE_LIMIT_RETRIES = 2
DEFAULT_RATE_LIMIT_MAX_WAIT_SECONDS = 65.0

_ALLOWED_REASONING_EFFORTS = {"low", "medium", "high"}


class GroqLLMServiceError(RuntimeError):
    """Base error for the Groq strategic LLM adapter."""


class GroqConfigurationError(GroqLLMServiceError):
    """Raised when required Groq configuration is missing or invalid."""


class GroqRateLimitError(GroqLLMServiceError):
    """Raised when Groq returns HTTP 429."""


class GroqInferenceError(GroqLLMServiceError):
    """Raised when a Groq inference request fails."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bounded_float(
    value: Any,
    default: float,
    low: float,
    high: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)

    return max(low, min(high, parsed))


def _bounded_int(
    value: Any,
    default: int,
    low: int,
    high: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)

    return max(low, min(high, parsed))


def _safe_headers(
    exc: BaseException,
) -> Dict[str, str]:
    response = getattr(
        exc,
        "response",
        None,
    )

    headers = getattr(
        response,
        "headers",
        None,
    )

    if headers is None:
        return {}

    wanted = {
        "retry-after",
        "x-ratelimit-limit-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    }

    output: Dict[str, str] = {}

    try:
        for key, value in headers.items():
            key_text = str(key).lower()

            if key_text in wanted:
                output[key_text] = str(value)

    except Exception:
        return {}

    return output


def _retry_after_seconds(
    exc: BaseException,
    headers: Mapping[str, str],
) -> float:
    """
    Extract Groq's requested retry delay from response headers or error text.
    """

    raw = _clean(
        headers.get(
            "retry-after"
        )
    )

    if raw:
        try:
            return max(
                0.0,
                float(raw),
            )
        except (TypeError, ValueError):
            pass

    text = str(
        exc or ""
    )

    patterns = (
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry after(?: approximately)?\s+([0-9]+(?:\.[0-9]+)?)\s*seconds?",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.I,
        )

        if match:
            try:
                return max(
                    0.0,
                    float(
                        match.group(1)
                    ),
                )
            except (TypeError, ValueError):
                continue

    return 0.0


class GroqLLMService:
    """
    Small compatibility adapter around ``groq.Groq``.

    StrategicInferenceAgent introspects the ``ask`` signature. The adapter
    therefore accepts the same optional knobs that the local LLM service uses,
    even when a knob is local-only, for example ``keep_alive`` or ``num_ctx``.

    Local-only knobs are accepted and ignored rather than causing a routing
    failure.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model_name: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 0,
        reasoning_effort: str = "",
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:

        resolved_key = (
            _clean(api_key)
            or _clean(
                os.getenv(
                    "GROQ_API_KEY"
                )
            )
        )

        if not resolved_key:
            raise GroqConfigurationError(
                "GROQ_API_KEY is not set. Create a Groq API key and set it in "
                "the environment before using Groq strategic inference."
            )

        resolved_model = (
            _clean(
                model_name
            )
            or _clean(
                os.getenv(
                    "GROQ_STRATEGIC_MODEL"
                )
            )
            or DEFAULT_MODEL
        )

        resolved_timeout = _bounded_float(
            timeout_seconds,
            DEFAULT_TIMEOUT_SECONDS,
            10.0,
            180.0,
        )

        resolved_max_tokens = _bounded_int(
            os.getenv(
                "GROQ_STRATEGIC_MAX_COMPLETION_TOKENS",
                str(
                    max_completion_tokens
                ),
            ),
            max_completion_tokens,
            128,
            4096,
        )

        resolved_effort = (
            _clean(
                reasoning_effort
            )
            or _clean(
                os.getenv(
                    "GROQ_STRATEGIC_REASONING_EFFORT"
                )
            )
            or DEFAULT_REASONING_EFFORT
        ).casefold()

        if (
            resolved_effort
            not in _ALLOWED_REASONING_EFFORTS
        ):
            resolved_effort = (
                DEFAULT_REASONING_EFFORT
            )

        try:
            from groq import Groq

        except Exception as exc:
            raise GroqConfigurationError(
                "The 'groq' package is not installed. "
                "Run: pip install groq"
            ) from exc

        self.model_name = (
            resolved_model
        )

        self.timeout_seconds = (
            resolved_timeout
        )

        self.max_retries = max(
            0,
            int(
                max_retries
            ),
        )

        self.reasoning_effort = (
            resolved_effort
        )

        self.max_completion_tokens = (
            resolved_max_tokens
        )

        self.provider_name = "groq"

        self.rate_limit_retries = _bounded_int(
            os.getenv(
                "GROQ_STRATEGIC_RATE_LIMIT_RETRIES",
                str(
                    DEFAULT_RATE_LIMIT_RETRIES
                ),
            ),
            DEFAULT_RATE_LIMIT_RETRIES,
            0,
            4,
        )

        self.rate_limit_max_wait_seconds = _bounded_float(
            os.getenv(
                "GROQ_STRATEGIC_RATE_LIMIT_MAX_WAIT_SECONDS",
                str(
                    DEFAULT_RATE_LIMIT_MAX_WAIT_SECONDS
                ),
            ),
            DEFAULT_RATE_LIMIT_MAX_WAIT_SECONDS,
            1.0,
            120.0,
        )

        self.last_request_metadata: Dict[
            str,
            Any,
        ] = {}

        # Disable SDK retries here. We handle 429 ourselves because
        # Groq tells us exactly how long to wait for the TPM window.
        self._client = Groq(
            api_key=resolved_key,
            timeout=resolved_timeout,
            max_retries=self.max_retries,
        )

    def get_last_request_metadata(
        self,
    ) -> Dict[str, Any]:

        return deepcopy(
            self.last_request_metadata
        )

    def ask(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        model_name: str = "",
        temperature: float = 0.15,
        seed: Optional[int] = None,
        num_ctx: Optional[int] = None,
        num_predict: Optional[int] = None,
        response_format: Any = None,
        keep_alive: Any = None,
        metadata: Optional[
            Mapping[str, Any]
        ] = None,
        reasoning_effort: str = "",
        **_: Any,
    ) -> str:

        # Ollama-specific compatibility parameters.
        del num_ctx
        del keep_alive

        user_text = _clean(
            prompt
        )

        if not user_text:
            raise GroqInferenceError(
                "prompt cannot be empty"
            )

        chosen_model = (
            _clean(
                model_name
            )
            or self.model_name
        )

        chosen_effort = (
            _clean(
                reasoning_effort
            ).casefold()
            or self.reasoning_effort
        )

        if (
            chosen_effort
            not in _ALLOWED_REASONING_EFFORTS
        ):
            chosen_effort = (
                self.reasoning_effort
            )

        requested_max_tokens = (
            self.max_completion_tokens
            if num_predict is None
            else _bounded_int(
                num_predict,
                self.max_completion_tokens,
                64,
                self.max_completion_tokens,
            )
        )

        messages = []

        system_text = _clean(
            system_prompt
        )

        if system_text:
            messages.append(
                {
                    "role": "system",
                    "content": system_text,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": user_text,
            }
        )

        request_kwargs: Dict[
            str,
            Any,
        ] = {
            "model": chosen_model,
            "messages": messages,
            "temperature": _bounded_float(
                temperature,
                0.15,
                0.0,
                1.0,
            ),
            "max_completion_tokens": (
                requested_max_tokens
            ),
            "reasoning_effort": (
                chosen_effort
            ),
            "reasoning_format": "hidden",
            "stream": False,
        }

        if seed is not None:
            request_kwargs[
                "seed"
            ] = int(
                seed
            )

        if (
            response_format
            in {
                "json",
                "json_object",
            }
            or (
                isinstance(
                    response_format,
                    Mapping,
                )
                and response_format.get(
                    "type"
                )
                == "json_object"
            )
        ):
            request_kwargs[
                "response_format"
            ] = {
                "type": "json_object"
            }

        started = (
            time.perf_counter()
        )

        self.last_request_metadata = {
            "provider": "groq",
            "service_version": (
                SERVICE_VERSION
            ),
            "model_name": (
                chosen_model
            ),
            "reasoning_effort": (
                chosen_effort
            ),
            "max_completion_tokens": (
                requested_max_tokens
            ),
            "timeout_seconds": (
                self.timeout_seconds
            ),
            "request_metadata": dict(
                metadata
                or {}
            ),
            "status": "started",
        }

        completion = None
        rate_limit_events = []

        for attempt in range(
            self.rate_limit_retries
            + 1
        ):

            try:
                completion = (
                    self._client
                    .chat
                    .completions
                    .create(
                        **request_kwargs
                    )
                )

                break

            except Exception as exc:
                headers = _safe_headers(
                    exc
                )

                status_code = getattr(
                    exc,
                    "status_code",
                    None,
                )

                is_rate_limit = (
                    status_code
                    == 429
                    or type(
                        exc
                    ).__name__
                    == "RateLimitError"
                )

                if not is_rate_limit:
                    elapsed_ms = (
                        time.perf_counter()
                        - started
                    ) * 1000.0

                    self.last_request_metadata.update(
                        {
                            "status": "failed",
                            "latency_ms": round(
                                elapsed_ms,
                                2,
                            ),
                            "error_type": (
                                type(
                                    exc
                                ).__name__
                            ),
                            "status_code": (
                                status_code
                            ),
                            "rate_limit_headers": (
                                headers
                            ),
                            "rate_limit_events": (
                                rate_limit_events
                            ),
                        }
                    )

                    raise GroqInferenceError(
                        "Groq inference failed: "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ) from exc

                retry_after = (
                    _retry_after_seconds(
                        exc,
                        headers,
                    )
                )

                # Give the rolling TPM window a small safety margin.
                wait_seconds = min(
                    self.rate_limit_max_wait_seconds,
                    max(
                        1.0,
                        retry_after
                        + 0.75,
                    ),
                )

                rate_limit_events.append(
                    {
                        "attempt": (
                            attempt
                            + 1
                        ),
                        "retry_after_seconds": round(
                            retry_after,
                            3,
                        ),
                        "planned_wait_seconds": round(
                            wait_seconds,
                            3,
                        ),
                        "headers": (
                            headers
                        ),
                    }
                )

                if (
                    attempt
                    >= self.rate_limit_retries
                ):
                    elapsed_ms = (
                        time.perf_counter()
                        - started
                    ) * 1000.0

                    self.last_request_metadata.update(
                        {
                            "status": (
                                "rate_limited"
                            ),
                            "latency_ms": round(
                                elapsed_ms,
                                2,
                            ),
                            "error_type": (
                                type(
                                    exc
                                ).__name__
                            ),
                            "status_code": (
                                status_code
                            ),
                            "rate_limit_headers": (
                                headers
                            ),
                            "rate_limit_events": (
                                rate_limit_events
                            ),
                        }
                    )

                    message = (
                        "Groq rate limit reached "
                        "after bounded retries."
                    )

                    if retry_after:
                        message += (
                            " Provider requested "
                            "another retry in approximately "
                            f"{retry_after:.2f} seconds."
                        )

                    raise GroqRateLimitError(
                        message
                    ) from exc

                logger.warning(
                    "Groq rate limit hit; retrying strategic inference. "
                    "attempt=%s/%s wait_seconds=%.2f model=%s",
                    attempt + 1,
                    self.rate_limit_retries + 1,
                    wait_seconds,
                    chosen_model,
                )

                time.sleep(
                    wait_seconds
                )

        if completion is None:
            raise GroqInferenceError(
                "Groq inference ended without a completion "
                "or a surfaced error."
            )

        elapsed_ms = (
            time.perf_counter()
            - started
        ) * 1000.0

        choices = getattr(
            completion,
            "choices",
            None,
        ) or []

        if not choices:
            self.last_request_metadata.update(
                {
                    "status": (
                        "failed_empty"
                    ),
                    "latency_ms": round(
                        elapsed_ms,
                        2,
                    ),
                }
            )

            raise GroqInferenceError(
                "Groq returned no completion choices."
            )

        message = getattr(
            choices[0],
            "message",
            None,
        )

        content = _clean(
            getattr(
                message,
                "content",
                "",
            )
        )

        if not content:
            self.last_request_metadata.update(
                {
                    "status": (
                        "failed_empty"
                    ),
                    "latency_ms": round(
                        elapsed_ms,
                        2,
                    ),
                }
            )

            raise GroqInferenceError(
                "Groq returned an empty completion."
            )

        usage = getattr(
            completion,
            "usage",
            None,
        )

        usage_metadata: Dict[
            str,
            Any,
        ] = {}

        if usage is not None:
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                value = getattr(
                    usage,
                    field,
                    None,
                )

                if value is not None:
                    usage_metadata[
                        field
                    ] = int(
                        value
                    )

        self.last_request_metadata.update(
            {
                "status": "ok",
                "latency_ms": round(
                    elapsed_ms,
                    2,
                ),
                "response_characters": (
                    len(
                        content
                    )
                ),
                "usage": (
                    usage_metadata
                ),
                "rate_limit_events": (
                    rate_limit_events
                ),
            }
        )

        logger.info(
            "groq_llm_run=%s",
            self.last_request_metadata,
        )

        return content