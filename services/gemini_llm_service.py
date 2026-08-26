"""
Gemini-backed LLM adapter for AI Event Intelligence.

Place at:
    services/gemini_llm_service.py

Environment:
    GEMINI_API_KEY                         required
    GEMINI_STRATEGIC_MODEL                 default: gemini-3.1-pro-preview
    GEMINI_GROUNDING_MODEL                 default: gemini-3.5-flash-lite
    GEMINI_STRATEGIC_THINKING_LEVEL        default: high
    GEMINI_GROUNDING_THINKING_LEVEL        default: minimal
    GEMINI_RATE_LIMIT_RETRIES              default: 2
    GEMINI_RETRY_BASE_SECONDS              default: 2
    GEMINI_HONOR_NUM_PREDICT               default: false

Important Gemini 3.x compatibility:
- Gemini 3.x uses thinking_level, not thinking_budget.
- Gemini 3.x thinking cannot be fully disabled; Flash-Lite uses "minimal".
- Sampling parameters such as temperature are intentionally omitted for 3.x.
- response_mime_type="application/json" is used for GenerateContent JSON mode.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


logger = logging.getLogger(__name__)


SERVICE_VERSION = "gemini-llm-service-v1.2-gemini3-thinking-fix"

DEFAULT_STRATEGIC_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GROUNDING_MODEL = "gemini-3.5-flash-lite"

DEFAULT_STRATEGIC_THINKING_LEVEL = "high"
DEFAULT_GROUNDING_THINKING_LEVEL = "minimal"

DEFAULT_RATE_LIMIT_RETRIES = 2
DEFAULT_RETRY_BASE_SECONDS = 2.0

_ALLOWED_THINKING_LEVELS = {
    "minimal",
    "low",
    "medium",
    "high",
}


class GeminiLLMServiceError(RuntimeError):
    pass


class GeminiConfigurationError(GeminiLLMServiceError):
    pass


class GeminiRateLimitError(GeminiLLMServiceError):
    pass


class GeminiInferenceError(GeminiLLMServiceError):
    pass


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _env_bool(
    name: str,
    default: bool = False,
) -> bool:
    raw = _clean(
        os.getenv(
            name,
            "",
        )
    )

    if not raw:
        return default

    return raw.casefold() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _bounded_int(
    value: Any,
    default: int,
    low: int,
    high: int,
) -> int:
    try:
        parsed = int(value)

    except (
        TypeError,
        ValueError,
    ):
        parsed = int(default)

    return max(
        low,
        min(
            high,
            parsed,
        ),
    )


def _bounded_float(
    value: Any,
    default: float,
    low: float,
    high: float,
) -> float:
    try:
        parsed = float(value)

    except (
        TypeError,
        ValueError,
    ):
        parsed = float(default)

    return max(
        low,
        min(
            high,
            parsed,
        ),
    )


def _is_gemini_3(
    model_name: str,
) -> bool:
    name = _clean(
        model_name
    ).casefold()

    return name.startswith(
        "gemini-3"
    )


def _thinking_level(
    value: Any,
    default: str,
) -> str:
    level = (
        _clean(
            value
        ).casefold()
        or default
    )

    if level not in _ALLOWED_THINKING_LEVELS:
        return default

    return level


def _status_code(
    exc: BaseException,
) -> Optional[int]:
    for attr in (
        "code",
        "status_code",
    ):
        value = getattr(
            exc,
            attr,
            None,
        )

        try:
            if value is not None:
                return int(
                    value
                )

        except (
            TypeError,
            ValueError,
        ):
            pass

    response = getattr(
        exc,
        "response",
        None,
    )

    if response is not None:
        for attr in (
            "status_code",
            "status",
        ):
            value = getattr(
                response,
                attr,
                None,
            )

            try:
                if value is not None:
                    return int(
                        value
                    )

            except (
                TypeError,
                ValueError,
            ):
                pass

    return None


def _retry_after_seconds(
    exc: BaseException,
) -> float:
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

    if headers is not None:
        try:
            raw = (
                headers.get(
                    "retry-after"
                )
                or headers.get(
                    "Retry-After"
                )
            )

            if raw:
                return max(
                    0.0,
                    float(
                        raw
                    ),
                )

        except Exception:
            pass

    text = str(
        exc
        or ""
    )

    patterns = (
        r"retry\s+after\s+([0-9]+(?:\.[0-9]+)?)\s*s",
        r"retry\s+after\s+([0-9]+(?:\.[0-9]+)?)\s*seconds?",
        r"try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
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
                        match.group(
                            1
                        )
                    ),
                )

            except (
                TypeError,
                ValueError,
            ):
                pass

    return 0.0


def _usage_metadata(
    response: Any,
) -> Dict[str, Any]:
    usage = getattr(
        response,
        "usage_metadata",
        None,
    )

    if usage is None:
        return {}

    output: Dict[
        str,
        Any,
    ] = {}

    for attr in (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "thoughts_token_count",
        "cached_content_token_count",
    ):
        value = getattr(
            usage,
            attr,
            None,
        )

        if value is None:
            continue

        try:
            output[
                attr
            ] = int(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            output[
                attr
            ] = value

    return output


class GeminiLLMService:
    """
    Compatibility adapter around the official google-genai SDK.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model_name: str = "",
        default_json: bool = False,
        thinking_level: str = "",
        timeout_seconds: float = 90.0,
        rate_limit_retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        **_: Any,
    ) -> None:
        resolved_key = (
            _clean(
                api_key
            )
            or _clean(
                os.getenv(
                    "GEMINI_API_KEY"
                )
            )
        )

        if not resolved_key:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not set."
            )

        resolved_model = (
            _clean(
                model_name
            )
            or _clean(
                os.getenv(
                    "GEMINI_STRATEGIC_MODEL"
                )
            )
            or DEFAULT_STRATEGIC_MODEL
        )

        try:
            from google import genai
            from google.genai import types

        except Exception as exc:
            raise GeminiConfigurationError(
                "The official Gemini SDK is not installed. "
                "Run: pip install -U google-genai"
            ) from exc

        self.model_name = (
            resolved_model
        )

        self.provider_name = (
            "gemini"
        )

        self.default_json = bool(
            default_json
        )

        self.timeout_seconds = (
            _bounded_float(
                timeout_seconds,
                90.0,
                5.0,
                300.0,
            )
        )

        self.thinking_level = (
            _thinking_level(
                thinking_level,
                DEFAULT_STRATEGIC_THINKING_LEVEL,
            )
        )

        self.rate_limit_retries = (
            _bounded_int(
                (
                    rate_limit_retries
                    if rate_limit_retries
                    is not None
                    else os.getenv(
                        "GEMINI_RATE_LIMIT_RETRIES",
                        str(
                            DEFAULT_RATE_LIMIT_RETRIES
                        ),
                    )
                ),
                DEFAULT_RATE_LIMIT_RETRIES,
                0,
                5,
            )
        )

        self.retry_base_seconds = (
            _bounded_float(
                (
                    retry_base_seconds
                    if retry_base_seconds
                    is not None
                    else os.getenv(
                        "GEMINI_RETRY_BASE_SECONDS",
                        str(
                            DEFAULT_RETRY_BASE_SECONDS
                        ),
                    )
                ),
                DEFAULT_RETRY_BASE_SECONDS,
                0.5,
                30.0,
            )
        )

        self.honor_num_predict = (
            _env_bool(
                "GEMINI_HONOR_NUM_PREDICT",
                False,
            )
        )

        self.last_request_metadata: Dict[
            str,
            Any,
        ] = {}

        self._types = (
            types
        )

        self._client = (
            genai.Client(
                api_key=resolved_key
            )
        )

    @classmethod
    def strategic(
        cls,
    ) -> "GeminiLLMService":
        return cls(
            model_name=(
                _clean(
                    os.getenv(
                        "GEMINI_STRATEGIC_MODEL"
                    )
                )
                or DEFAULT_STRATEGIC_MODEL
            ),
            default_json=False,
            thinking_level=(
                _thinking_level(
                    os.getenv(
                        "GEMINI_STRATEGIC_THINKING_LEVEL"
                    ),
                    DEFAULT_STRATEGIC_THINKING_LEVEL,
                )
            ),
            timeout_seconds=(
                _bounded_float(
                    os.getenv(
                        "GEMINI_STRATEGIC_TIMEOUT_SECONDS",
                        "120",
                    ),
                    120.0,
                    20.0,
                    300.0,
                )
            ),
        )

    @classmethod
    def grounding(
        cls,
    ) -> "GeminiLLMService":
        return cls(
            model_name=(
                _clean(
                    os.getenv(
                        "GEMINI_GROUNDING_MODEL"
                    )
                )
                or DEFAULT_GROUNDING_MODEL
            ),
            default_json=True,
            thinking_level=(
                _thinking_level(
                    os.getenv(
                        "GEMINI_GROUNDING_THINKING_LEVEL"
                    ),
                    DEFAULT_GROUNDING_THINKING_LEVEL,
                )
            ),
            timeout_seconds=(
                _bounded_float(
                    os.getenv(
                        "GEMINI_GROUNDING_TIMEOUT_SECONDS",
                        "60",
                    ),
                    60.0,
                    10.0,
                    180.0,
                )
            ),
        )

    def get_last_request_metadata(
        self,
    ) -> Dict[str, Any]:
        return deepcopy(
            self.last_request_metadata
        )

    # Compatibility with code that expects the local service method name.
    def get_last_response_metadata(
        self,
    ) -> Dict[str, Any]:
        return self.get_last_request_metadata()

    def ask(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        model_name: Optional[str] = None,
        options: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
        temperature: Optional[
            float
        ] = None,
        seed: Optional[
            int
        ] = None,
        num_ctx: Optional[
            int
        ] = None,
        num_predict: Optional[
            int
        ] = None,
        response_format: Any = None,
        keep_alive: Any = None,
        metadata: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
        **_: Any,
    ) -> str:
        #
        # These are Ollama compatibility parameters.
        #
        del options
        del num_ctx
        del keep_alive

        user_text = (
            _clean(
                prompt
            )
        )

        if not user_text:
            raise GeminiInferenceError(
                "prompt cannot be empty"
            )

        chosen_model = (
            _clean(
                model_name
            )
            or self.model_name
        )

        is_v3 = (
            _is_gemini_3(
                chosen_model
            )
        )

        wants_json = bool(
            self.default_json
        )

        if response_format in {
            "json",
            "json_object",
        }:
            wants_json = True

        elif isinstance(
            response_format,
            Mapping,
        ):
            wants_json = (
                _clean(
                    response_format.get(
                        "type"
                    )
                )
                == "json_object"
            )

        config_kwargs: Dict[
            str,
            Any,
        ] = {}

        if _clean(
            system_prompt
        ):
            config_kwargs[
                "system_instruction"
            ] = _clean(
                system_prompt
            )

        if wants_json:
            config_kwargs[
                "response_mime_type"
            ] = "application/json"

        #
        # CRITICAL GEMINI 3.x FIX
        #
        # Do NOT send thinking_budget=0.
        #
        # Gemini 3.x uses thinking_level:
        #
        #   minimal
        #   low
        #   medium
        #   high
        #
        # Grounding uses "minimal".
        # Strategic Pro uses "high".
        #
        if is_v3:
            config_kwargs[
                "thinking_config"
            ] = (
                self
                ._types
                .ThinkingConfig(
                    thinking_level=(
                        self.thinking_level
                    )
                )
            )

            #
            # Google recommends leaving Gemini 3.x
            # sampling parameters at model defaults.
            #
            # Therefore callers can still pass temperature
            # for interface compatibility, but it is deliberately
            # NOT sent to Gemini 3.x.
            #

        else:
            if temperature is not None:
                config_kwargs[
                    "temperature"
                ] = _bounded_float(
                    temperature,
                    0.15,
                    0.0,
                    2.0,
                )

            if seed is not None:
                config_kwargs[
                    "seed"
                ] = int(
                    seed
                )

        #
        # Old Ollama callers often provide num_predict.
        #
        # Do not convert it to Gemini max_output_tokens unless
        # explicitly requested through GEMINI_HONOR_NUM_PREDICT.
        #
        if (
            self.honor_num_predict
            and num_predict
            is not None
        ):
            config_kwargs[
                "max_output_tokens"
            ] = _bounded_int(
                num_predict,
                1024,
                64,
                65536,
            )

        #
        # This application is not giving Gemini callable Python tools.
        # Disable SDK automatic function calling explicitly.
        #
        config_kwargs[
            "automatic_function_calling"
        ] = (
            self
            ._types
            .AutomaticFunctionCallingConfig(
                disable=True
            )
        )

        config = (
            self
            ._types
            .GenerateContentConfig(
                **config_kwargs
            )
        )

        started = (
            time.perf_counter()
        )

        retry_events = []

        self.last_request_metadata = {
            "provider": (
                "gemini"
            ),
            "service_version": (
                SERVICE_VERSION
            ),
            "model_name": (
                chosen_model
            ),
            "json_mode": (
                wants_json
            ),
            "thinking_level": (
                self.thinking_level
                if is_v3
                else ""
            ),
            "request_metadata": (
                dict(
                    metadata
                    or {}
                )
            ),
            "status": (
                "started"
            ),
        }

        response = None

        for attempt in range(
            self.rate_limit_retries
            + 1
        ):
            try:
                response = (
                    self
                    ._client
                    .models
                    .generate_content(
                        model=(
                            chosen_model
                        ),
                        contents=(
                            user_text
                        ),
                        config=(
                            config
                        ),
                    )
                )

                break

            except Exception as exc:
                code = (
                    _status_code(
                        exc
                    )
                )

                text = str(
                    exc
                )

                is_rate_limit = (
                    code == 429
                    or "RESOURCE_EXHAUSTED"
                    in text
                    or "429"
                    in text
                )

                if not is_rate_limit:
                    elapsed_ms = (
                        (
                            time.perf_counter()
                            - started
                        )
                        * 1000.0
                    )

                    self.last_request_metadata.update(
                        {
                            "status": (
                                "failed"
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
                                code
                            ),
                            "retry_events": (
                                retry_events
                            ),
                        }
                    )

                    raise GeminiInferenceError(
                        "Gemini inference failed: "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ) from exc

                provider_wait = (
                    _retry_after_seconds(
                        exc
                    )
                )

                fallback_wait = (
                    self.retry_base_seconds
                    * (
                        2
                        ** attempt
                    )
                )

                wait_seconds = (
                    max(
                        provider_wait,
                        fallback_wait,
                    )
                    + random.uniform(
                        0.05,
                        0.35,
                    )
                )

                retry_events.append(
                    {
                        "attempt": (
                            attempt
                            + 1
                        ),
                        "status_code": (
                            code
                        ),
                        "provider_retry_after_seconds": round(
                            provider_wait,
                            3,
                        ),
                        "wait_seconds": round(
                            wait_seconds,
                            3,
                        ),
                    }
                )

                if (
                    attempt
                    >= self.rate_limit_retries
                ):
                    elapsed_ms = (
                        (
                            time.perf_counter()
                            - started
                        )
                        * 1000.0
                    )

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
                                code
                            ),
                            "retry_events": (
                                retry_events
                            ),
                        }
                    )

                    raise GeminiRateLimitError(
                        "Gemini rate limit reached "
                        "after bounded retries."
                    ) from exc

                logger.warning(
                    (
                        "Gemini rate limit hit; retrying. "
                        "attempt=%s/%s "
                        "wait_seconds=%.2f "
                        "model=%s"
                    ),
                    attempt + 1,
                    self.rate_limit_retries + 1,
                    wait_seconds,
                    chosen_model,
                )

                time.sleep(
                    wait_seconds
                )

        if response is None:
            raise GeminiInferenceError(
                "Gemini request ended "
                "without a response."
            )

        try:
            content = (
                _clean(
                    response.text
                )
            )

        except Exception as exc:
            raise GeminiInferenceError(
                "Gemini returned a response "
                "without readable text."
            ) from exc

        if not content:
            elapsed_ms = (
                (
                    time.perf_counter()
                    - started
                )
                * 1000.0
            )

            self.last_request_metadata.update(
                {
                    "status": (
                        "failed_empty"
                    ),
                    "latency_ms": round(
                        elapsed_ms,
                        2,
                    ),
                    "retry_events": (
                        retry_events
                    ),
                }
            )

            raise GeminiInferenceError(
                "Gemini returned empty output."
            )

        if wants_json:
            try:
                parsed = (
                    json.loads(
                        content
                    )
                )

            except json.JSONDecodeError as exc:
                raise GeminiInferenceError(
                    "Gemini JSON mode returned "
                    "malformed JSON."
                ) from exc

            if not isinstance(
                parsed,
                (
                    dict,
                    list,
                ),
            ):
                raise GeminiInferenceError(
                    "Gemini JSON mode returned "
                    "a non-object/non-array value."
                )

        elapsed_ms = (
            (
                time.perf_counter()
                - started
            )
            * 1000.0
        )

        self.last_request_metadata.update(
            {
                "status": (
                    "ok"
                ),
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
                    _usage_metadata(
                        response
                    )
                ),
                "retry_events": (
                    retry_events
                ),
            }
        )

        logger.info(
            "gemini_llm_run=%s",
            self.last_request_metadata,
        )

        return content


__all__ = [
    "SERVICE_VERSION",
    "GeminiLLMServiceError",
    "GeminiConfigurationError",
    "GeminiRateLimitError",
    "GeminiInferenceError",
    "GeminiLLMService",
]