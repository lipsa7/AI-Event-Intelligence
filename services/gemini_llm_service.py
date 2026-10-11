"""
Gemini-backed LLM adapter for AI Event Intelligence.

Place at:
    services/gemini_llm_service.py

Environment:
    GEMINI_API_KEY                         required (environment or project .env)
    GEMINI_STRATEGIC_MODEL                 default: gemini-3.1-pro-preview
    GEMINI_GROUNDING_MODEL                 default: gemini-3.5-flash-lite
    GEMINI_STRATEGIC_THINKING_LEVEL        default: high
    GEMINI_GROUNDING_THINKING_LEVEL        default: minimal
    GEMINI_RATE_LIMIT_RETRIES              default: 2
    GEMINI_TRANSIENT_RETRIES               default: 2 (dropped connection, HTTP 500/502/503/504)
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
import shlex
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


# v6.20.12: under the configured "event_intelligence" logger so rate-limit
# retries and Google's own error text reach data/logs/system.log.
logger = logging.getLogger("event_intelligence.services.gemini_llm_service")


SERVICE_VERSION = "gemini-llm-service-v1.2.3-credits-fail-fast"

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


class GeminiCreditsDepletedError(GeminiRateLimitError):
    """v6.30.1: Google 402 - prepaid credits used up. Not retried."""


class GeminiInferenceError(GeminiLLMServiceError):
    pass


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


_GEMINI_KEY_NAMES = (
    "GEMINI_API_KEY",
    "GOOGLE_GEMINI_API_KEY",
    "GEMINI_KEY",
    "GOOGLE_API_KEY",
)


def _resolve_gemini_api_key() -> str:
    """Resolve credentials when a client is constructed, before any agent runs.

    Nonempty process settings take precedence over the project's .env. Only
    Gemini key entries are read; unrelated settings are not changed. This
    deliberately does not import an agent or depend on its import-time loader.
    The project path is anchored to this installed services module, not cwd.
    Values are single-line literals; quotes, export and comments are supported.
    No credential value is logged or included in a configuration exception.
    """
    # Gemini-specific names first (environment, then the project .env).
    # GOOGLE_API_KEY comes last: another Google service may have set it
    # system-wide, and it must not override the project's own Gemini key.
    specific = tuple(name for name in _GEMINI_KEY_NAMES if name != "GOOGLE_API_KEY")
    for name in specific:
        value = _clean(os.getenv(name))
        if value:
            os.environ["GEMINI_API_KEY"] = value
            return value

    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    except (OSError, UnicodeError):
        raise GeminiConfigurationError(
            "Could not read the project-root .env file. Check its permissions "
            "and UTF-8 encoding, or set GEMINI_API_KEY in the environment."
        ) from None

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.casefold().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        raw_name, raw_value = line.split("=", 1)
        # Preserve the existing "Gemini API Key = ..." project format.
        name = re.sub(r"[^A-Za-z0-9]+", "_", raw_name).strip("_").upper()
        if name not in _GEMINI_KEY_NAMES:
            continue
        try:
            tokens = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            raise GeminiConfigurationError(
                "A Gemini key entry in the project-root .env has unmatched "
                "quotes. Use GEMINI_API_KEY=\"your-key\" on one line."
            ) from None
        if len(tokens) > 1:
            raise GeminiConfigurationError(
                "A Gemini key entry in the project-root .env must contain "
                "one value. Put comments after #."
            )
        if tokens and _clean(tokens[0]):
            values[name] = _clean(tokens[0])

    for name in specific:
        if name in values:
            os.environ["GEMINI_API_KEY"] = values[name]
            return values[name]
    google_key = _clean(os.getenv("GOOGLE_API_KEY")) or values.get("GOOGLE_API_KEY", "")
    if google_key:
        os.environ["GEMINI_API_KEY"] = google_key
        return google_key
    return ""


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


_TRANSIENT_ERROR_MARKERS = (
    "readerror",
    "connecterror",
    "remoteprotocolerror",
    "connectionreseterror",
    "connectionabortederror",
    "brokenpipeerror",
    "forcibly closed",
    "connection reset",
    "connection aborted",
    "connection was closed",
    "server disconnected",
    "10054",
    "10053",
    "unavailable",
    "internal error",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
)


def _is_transient_transport_error(
    exc: BaseException,
    code: Optional[int],
) -> bool:
    """A dropped connection or a temporary server error (HTTP 500/502/503/504).

    The request itself was fine; the same call may succeed if sent again.
    Not included: rate limits (handled separately), timeouts (they already
    used their time budget), and key/permission/argument errors.
    """
    if code in {500, 502, 503, 504}:
        return True
    if code is not None and 400 <= code < 500:
        return False
    fragments: list[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        fragments.extend([type(current).__name__, str(current)])
        current = current.__cause__ or current.__context__
    text = " ".join(fragments).casefold()
    if any(marker in text for marker in ("api key", "permission", "invalid argument", "billing")):
        return False
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


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
        # v6.20.12: Google's own 429 wording ("Please retry in 41.2s.",
        # "'retryDelay': '41s'"). Missed before, so a per-minute limit was
        # retried after ~2 s and 4 s and then given up.
        r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
        r"retryDelay['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)s",
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


# v6.21: per-run Gemini usage (calls and tokens per model), so each run's
# cost can be read from the log. The orchestrator logs and resets it at the
# end of every run.
import threading as _threading

_USAGE_LOCK = _threading.Lock()
_USAGE: Dict[str, Dict[str, int]] = {}


def record_usage(model: str, usage: Mapping[str, Any] | None, *, failed: bool = False) -> None:
    with _USAGE_LOCK:
        row = _USAGE.setdefault(_clean(model) or "unknown", {
            "calls": 0, "failed_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "thinking_tokens": 0})
        if failed:
            row["failed_calls"] += 1
            return
        row["calls"] += 1
        usage = usage or {}
        for key, field in (("prompt_tokens", "prompt_token_count"), ("output_tokens", "candidates_token_count"),
                           ("thinking_tokens", "thoughts_token_count")):
            try:
                row[key] += int(usage.get(field) or 0)
            except (TypeError, ValueError):
                pass


def usage_snapshot(reset: bool = False) -> Dict[str, Dict[str, int]]:
    with _USAGE_LOCK:
        snapshot = {model: dict(row) for model, row in _USAGE.items()}
        if reset:
            _USAGE.clear()
        return snapshot


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
        resolved_key = _clean(api_key) or _resolve_gemini_api_key()

        if not resolved_key:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not set. Add it to the project-root .env "
                "or the environment before starting the app."
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

        self.transient_retries = _bounded_int(
            os.getenv("GEMINI_TRANSIENT_RETRIES", "2"),
            2,
            0,
            5,
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
        attempt = 0
        transient_attempts = 0

        while True:
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

                if code == 402 or "credits are depleted" in text.casefold():
                    # v6.30.1: out of prepaid credit is not a rate limit - waiting does not
                    # help, so fail at once instead of retrying every model three times.
                    self.last_request_metadata.update({
                        "status": "credits_depleted", "error_type": type(exc).__name__,
                        "status_code": code, "retry_events": retry_events,
                        "latency_ms": round((time.perf_counter() - started) * 1000.0, 2)})
                    record_usage(chosen_model, None, failed=True)
                    logger.error("Gemini prepaid credits are depleted; not retrying. model=%s", chosen_model)
                    raise GeminiCreditsDepletedError(
                        "Gemini prepaid credits are depleted (top up in AI Studio). Google said: "
                        + " ".join(text.split())[:300]
                    ) from exc

                if (
                    not is_rate_limit
                    and transient_attempts < self.transient_retries
                    and _is_transient_transport_error(exc, code)
                ):
                    # Dropped connection or temporary server error: send the
                    # same request again (generate_content has no side effects).
                    transient_attempts += 1
                    wait_seconds = (
                        self.retry_base_seconds * (2 ** (transient_attempts - 1))
                        + random.uniform(0.05, 0.35)
                    )
                    retry_events.append(
                        {
                            "attempt": attempt + transient_attempts,
                            "kind": "transient_transport",
                            "error_type": type(exc).__name__,
                            "status_code": code,
                            "wait_seconds": round(wait_seconds, 3),
                        }
                    )
                    logger.warning(
                        "Gemini connection dropped or server error; retrying. "
                        "retry=%s/%s wait_seconds=%.2f model=%s error=%s",
                        transient_attempts,
                        self.transient_retries,
                        wait_seconds,
                        chosen_model,
                        type(exc).__name__,
                    )
                    time.sleep(wait_seconds)
                    continue

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

                provider_wait = min(
                    _retry_after_seconds(
                        exc
                    ),
                    65.0,  # v6.20.12: honour Google's per-minute delay, never hang longer
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

                    record_usage(chosen_model, None, failed=True)
                    raise GeminiRateLimitError(
                        "Gemini rate limit reached "
                        "after bounded retries. Google said: "
                        + " ".join(str(exc).split())[:400]
                    ) from exc

                logger.warning(
                    (
                        "Gemini rate limit hit; retrying. "
                        "attempt=%s/%s "
                        "wait_seconds=%.2f "
                        "model=%s google=%s"
                    ),
                    attempt + 1,
                    self.rate_limit_retries + 1,
                    wait_seconds,
                    chosen_model,
                    " ".join(str(exc).split())[:300],
                )

                time.sleep(
                    wait_seconds
                )
                attempt += 1

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
        record_usage(chosen_model, self.last_request_metadata.get("usage"))

        return content


__all__ = [
    "SERVICE_VERSION",
    "GeminiLLMServiceError",
    "GeminiConfigurationError",
    "GeminiRateLimitError",
    "GeminiInferenceError",
    "GeminiLLMService",
]