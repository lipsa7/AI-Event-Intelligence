
"""
Local-first LLM service for the Event Intelligence pipeline.

Recommended location:
    services/llm_service.py

Role
----
ModelRouterService
        ↓
already-selected model
        ↓
LLMService
        ↓
Ollama / compatible local inference endpoint
        ↓
Extraction / Constraint / StrategicInference / Grounding / RedTeam

This service is intentionally thin.

It does NOT:
- choose which model should be used;
- decide Fast vs Deep research mode;
- modify Blueprint policy;
- perform retries that change the task semantics;
- perform web search;
- treat model output as evidence;
- authorize release;
- hide model failures behind fabricated fallback text.

Default transport
-----------------
Ollama HTTP API:

    http://127.0.0.1:11434/api/chat

The default configuration permits loopback/local hostnames only. A remote or
LAN-hosted inference server must be explicitly enabled with:

    allow_remote_base_url=True

This prevents the free/local-first project from accidentally routing requests
to a hosted API simply because an environment variable changed.

No paid API key or cloud SDK is required.

Model routing
-------------
The caller / ModelRouterService selects the model. LLMService simply executes
the requested local model.

Typical usage:

    llm = LLMService(model_name="qwen3:8b")

    text = llm.ask(
        prompt,
        system_prompt="Return JSON only."
    )

The public `ask(...)` method is the compatibility contract used by the current
agents/services.

Reliability
-----------
- explicit connect/read timeout through urllib;
- bounded response size;
- deterministic retry policy for transient transport/server failures only;
- no retry on malformed application output;
- no automatic model pull/download;
- no silent fallback to another model;
- model availability can be checked explicitly;
- complete response metadata available through get_last_response_metadata().

Structured output
-----------------
`ask_json(...)` can request Ollama JSON mode and validates that a JSON object was
returned. It does not repair or reinterpret malformed content. Components that
want a bounded repair pass should own that behavior themselves.

Privacy / reasoning
-------------------
No private chain-of-thought is requested or stored by this service.
Raw prompts/responses are not persisted. Only safe operational metadata is kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "llm-service-v2.0"
TRANSPORT_VERSION = "ollama-chat-api-v1"

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"

DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.75

TRANSIENT_HTTP_STATUS = {
    408,
    425,
    429,
    500,
    502,
    503,
    504,
}


class LLMServiceError(RuntimeError):
    """Base LLM service error."""


class LLMConfigurationError(LLMServiceError):
    """Invalid local-inference configuration."""


class LLMTransportError(LLMServiceError):
    """Network/HTTP transport failure."""


class LLMTimeoutError(LLMTransportError):
    """Inference request exceeded timeout."""


class LLMResponseError(LLMServiceError):
    """Inference endpoint returned malformed/unusable output."""


class LLMModelUnavailableError(LLMServiceError):
    """Requested model is not installed/available."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    return str(value)


def _positive_float(
    value: Any,
    *,
    name: str,
) -> float:
    if isinstance(value, bool):
        raise LLMConfigurationError(
            f"{name} must be numeric, not bool."
        )

    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(
            f"{name} must be numeric."
        ) from exc

    if numeric <= 0:
        raise LLMConfigurationError(
            f"{name} must be > 0."
        )

    return numeric


def _nonnegative_int(
    value: Any,
    *,
    name: str,
) -> int:
    if isinstance(value, bool):
        raise LLMConfigurationError(
            f"{name} must be an integer, not bool."
        )

    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(
            f"{name} must be an integer."
        ) from exc

    if numeric < 0:
        raise LLMConfigurationError(
            f"{name} must be >= 0."
        )

    return numeric


def _positive_int(
    value: Any,
    *,
    name: str,
) -> int:
    numeric = _nonnegative_int(
        value,
        name=name,
    )

    if numeric <= 0:
        raise LLMConfigurationError(
            f"{name} must be > 0."
        )

    return numeric


class LLMService:
    """
    Thin Ollama/local-inference client.

    Parameters
    ----------
    model_name:
        Already-routed model name. If omitted, OLLAMA_MODEL is used, then the
        local default.

    base_url:
        Ollama endpoint base. Defaults to OLLAMA_BASE_URL, then loopback.

    allow_remote_base_url:
        False by default. Keeps accidental cloud/remote usage from entering the
        free/local-first path.

    timeout_seconds:
        Whole HTTP request timeout.

    max_retries:
        Transient transport/server retries. Does not retry application-level
        malformed model output.

    default_options:
        Ollama generation options such as temperature, seed, num_ctx, num_predict.
        These are transport/model options only; policy should be compiled outside.
    """

    def __init__(
        self,
        *,
        model_name: str = "",
        base_url: str = "",
        allow_remote_base_url: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        default_options: Optional[Mapping[str, Any]] = None,
        keep_alive: Any = "5m",
        user_agent: str = "event-intelligence-local-llm/2.0",
    ) -> None:
        self.model_name = (
            _clean(model_name)
            or _clean(os.getenv("OLLAMA_MODEL"))
            or DEFAULT_MODEL
        )

        if not self.model_name:
            raise LLMConfigurationError(
                "model_name cannot be empty."
            )

        self.base_url = (
            _clean(base_url)
            or _clean(os.getenv("OLLAMA_BASE_URL"))
            or DEFAULT_BASE_URL
        ).rstrip("/")

        self.allow_remote_base_url = bool(
            allow_remote_base_url
        )

        self.timeout_seconds = _positive_float(
            timeout_seconds,
            name="timeout_seconds",
        )
        self.max_response_bytes = _positive_int(
            max_response_bytes,
            name="max_response_bytes",
        )
        self.max_retries = _nonnegative_int(
            max_retries,
            name="max_retries",
        )
        self.retry_backoff_seconds = _positive_float(
            retry_backoff_seconds,
            name="retry_backoff_seconds",
        )

        self.default_options = dict(
            default_options
            or {}
        )

        self.keep_alive = keep_alive
        self.user_agent = (
            _clean(user_agent)
            or "event-intelligence-local-llm/2.0"
        )

        self._validate_base_url(
            self.base_url,
            allow_remote=self.allow_remote_base_url,
        )

        self._last_response_metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        model_name: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        num_ctx: Optional[int] = None,
        num_predict: Optional[int] = None,
        response_format: Optional[Any] = None,
        keep_alive: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """
        Execute one non-streaming local chat inference request.

        Returns assistant text only.
        """
        prompt = str(
            prompt
            if prompt is not None
            else ""
        )

        if not prompt.strip():
            raise LLMConfigurationError(
                "prompt cannot be empty."
            )

        system_prompt = (
            str(system_prompt)
            if system_prompt is not None
            else ""
        )

        selected_model = (
            _clean(model_name)
            if model_name is not None
            else self.model_name
        )

        if not selected_model:
            raise LLMConfigurationError(
                "selected model cannot be empty."
            )

        generation_options = self._generation_options(
            options=options,
            temperature=temperature,
            seed=seed,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )

        messages = []

        if system_prompt.strip():
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
        }

        if generation_options:
            payload[
                "options"
            ] = generation_options

        effective_keep_alive = (
            self.keep_alive
            if keep_alive is None
            else keep_alive
        )

        if effective_keep_alive is not None:
            payload[
                "keep_alive"
            ] = effective_keep_alive

        if response_format is not None:
            payload[
                "format"
            ] = _json_safe(
                response_format
            )

        started = time.perf_counter()

        response = self._request_json(
            path="/api/chat",
            payload=payload,
            method="POST",
        )

        elapsed = (
            time.perf_counter()
            - started
        )

        text = self._extract_chat_text(
            response
        )

        self._last_response_metadata = self._response_metadata(
            response=response,
            selected_model=selected_model,
            elapsed_seconds=elapsed,
            prompt=prompt,
            system_prompt=system_prompt,
            generation_options=generation_options,
            request_metadata=metadata
            or {},
        )

        return text

    def ask_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        model_name: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        num_ctx: Optional[int] = None,
        num_predict: Optional[int] = None,
        schema: Optional[Mapping[str, Any]] = None,
        keep_alive: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Request JSON output and return one parsed JSON object.

        No repair pass is performed here.
        """
        response_format: Any = (
            dict(schema)
            if isinstance(schema, Mapping)
            else "json"
        )

        text = self.ask(
            prompt,
            system_prompt=system_prompt,
            model_name=model_name,
            options=options,
            temperature=temperature,
            seed=seed,
            num_ctx=num_ctx,
            num_predict=num_predict,
            response_format=response_format,
            keep_alive=keep_alive,
            metadata=metadata,
        )

        try:
            parsed = json.loads(
                text
            )
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                "Model did not return valid JSON."
            ) from exc

        if not isinstance(
            parsed,
            dict,
        ):
            raise LLMResponseError(
                "ask_json expected a JSON object."
            )

        return parsed

    def get_last_response_metadata(
        self,
    ) -> Dict[str, Any]:
        """
        Return safe operational metadata.

        Raw prompt/system/output text is never stored here.
        """
        return deepcopy(
            self._last_response_metadata
        )

    def list_models(
        self,
    ) -> list[Dict[str, Any]]:
        """
        List locally available Ollama models.
        """
        payload = self._request_json(
            path="/api/tags",
            payload=None,
            method="GET",
        )

        models = payload.get(
            "models"
        )

        if not isinstance(
            models,
            list,
        ):
            raise LLMResponseError(
                "Ollama /api/tags did not return models[]."
            )

        result = []

        for item in models:
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            result.append(
                {
                    "name": _clean(
                        item.get(
                            "name"
                        )
                        or item.get(
                            "model"
                        )
                    ),
                    "model": _clean(
                        item.get(
                            "model"
                        )
                        or item.get(
                            "name"
                        )
                    ),
                    "size": item.get(
                        "size"
                    ),
                    "digest": _clean(
                        item.get(
                            "digest"
                        )
                    ),
                    "modified_at": _clean(
                        item.get(
                            "modified_at"
                        )
                    ),
                    "details": _json_safe(
                        item.get(
                            "details"
                        )
                        or {}
                    ),
                }
            )

        return result

    def is_model_available(
        self,
        model_name: Optional[str] = None,
    ) -> bool:
        selected_model = (
            _clean(model_name)
            if model_name is not None
            else self.model_name
        )

        available = {
            item[
                "name"
            ]
            for item in self.list_models()
            if item[
                "name"
            ]
        }

        if selected_model in available:
            return True

        # Ollama names may be returned with an implicit tag.
        if ":" not in selected_model:
            if (
                selected_model
                + ":latest"
            ) in available:
                return True

        return False

    def require_model_available(
        self,
        model_name: Optional[str] = None,
    ) -> None:
        selected_model = (
            _clean(model_name)
            if model_name is not None
            else self.model_name
        )

        if not self.is_model_available(
            selected_model
        ):
            raise LLMModelUnavailableError(
                f"Local model {selected_model!r} is not available in Ollama. "
                "This service does not auto-pull/download models."
            )

    def health_check(
        self,
    ) -> Dict[str, Any]:
        """
        Lightweight local-endpoint health check.

        Ollama's model-list endpoint is used because it is broadly available.
        """
        started = time.perf_counter()

        try:
            models = self.list_models()
            ok = True
            error_type = ""
        except Exception as exc:
            models = []
            ok = False
            error_type = type(
                exc
            ).__name__

        return {
            "ok": ok,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "model_available": (
                self.is_model_available(
                    self.model_name
                )
                if ok
                else False
            ),
            "available_model_count": len(
                models
            ),
            "error_type": error_type,
            "latency_ms": round(
                (
                    time.perf_counter()
                    - started
                )
                * 1000.0,
                2,
            ),
            "paid_api_used": False,
            "remote_base_url_allowed": self.allow_remote_base_url,
        }

    # ------------------------------------------------------------------
    # Request construction
    # ------------------------------------------------------------------

    def _generation_options(
        self,
        *,
        options: Optional[Mapping[str, Any]],
        temperature: Optional[float],
        seed: Optional[int],
        num_ctx: Optional[int],
        num_predict: Optional[int],
    ) -> Dict[str, Any]:
        merged = dict(
            self.default_options
        )

        if options is not None:
            if not isinstance(
                options,
                Mapping,
            ):
                raise LLMConfigurationError(
                    "options must be an object."
                )

            merged.update(
                dict(
                    options
                )
            )

        if temperature is not None:
            if isinstance(
                temperature,
                bool,
            ):
                raise LLMConfigurationError(
                    "temperature must be numeric, not bool."
                )

            try:
                numeric = float(
                    temperature
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise LLMConfigurationError(
                    "temperature must be numeric."
                ) from exc

            if numeric < 0:
                raise LLMConfigurationError(
                    "temperature must be >= 0."
                )

            merged[
                "temperature"
            ] = numeric

        if seed is not None:
            if isinstance(
                seed,
                bool,
            ):
                raise LLMConfigurationError(
                    "seed must be an integer, not bool."
                )

            try:
                merged[
                    "seed"
                ] = int(
                    seed
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise LLMConfigurationError(
                    "seed must be an integer."
                ) from exc

        if num_ctx is not None:
            merged[
                "num_ctx"
            ] = _positive_int(
                num_ctx,
                name="num_ctx",
            )

        if num_predict is not None:
            merged[
                "num_predict"
            ] = _positive_int(
                num_predict,
                name="num_predict",
            )

        return _json_safe(
            merged
        )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request_json(
        self,
        *,
        path: str,
        payload: Optional[Mapping[str, Any]],
        method: str,
    ) -> Dict[str, Any]:
        url = (
            self.base_url
            + "/"
            + path.lstrip("/")
        )

        body = (
            json.dumps(
                _json_safe(
                    payload
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if payload is not None
            else None
        )

        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

        if body is not None:
            headers[
                "Content-Type"
            ] = "application/json"

        last_error: Optional[
            Exception
        ] = None

        for attempt in range(
            self.max_retries
            + 1
        ):
            request = urllib.request.Request(
                url=url,
                data=body,
                headers=headers,
                method=method.upper(),
            )

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    raw = response.read(
                        self.max_response_bytes
                        + 1
                    )

                    if len(
                        raw
                    ) > self.max_response_bytes:
                        raise LLMResponseError(
                            "Inference response exceeded max_response_bytes."
                        )

                    charset = (
                        response.headers.get_content_charset()
                        or "utf-8"
                    )

                    text = raw.decode(
                        charset,
                        errors="strict",
                    )

                    try:
                        parsed = json.loads(
                            text
                        )
                    except json.JSONDecodeError as exc:
                        raise LLMResponseError(
                            "Inference endpoint returned invalid JSON."
                        ) from exc

                    if not isinstance(
                        parsed,
                        dict,
                    ):
                        raise LLMResponseError(
                            "Inference endpoint must return a JSON object."
                        )

                    return parsed

            except urllib.error.HTTPError as exc:
                body_text = self._read_http_error_body(
                    exc
                )

                error = self._http_error(
                    exc.code,
                    body_text,
                )

                last_error = error

                if (
                    exc.code
                    in TRANSIENT_HTTP_STATUS
                    and attempt
                    < self.max_retries
                ):
                    self._retry_sleep(
                        attempt
                    )
                    continue

                raise error from exc

            except (
                TimeoutError,
                socket.timeout,
            ) as exc:
                last_error = LLMTimeoutError(
                    f"Local inference timed out after "
                    f"{self.timeout_seconds:.1f}s."
                )

                if attempt < self.max_retries:
                    self._retry_sleep(
                        attempt
                    )
                    continue

                raise last_error from exc

            except urllib.error.URLError as exc:
                reason = getattr(
                    exc,
                    "reason",
                    exc,
                )

                if isinstance(
                    reason,
                    (
                        TimeoutError,
                        socket.timeout,
                    ),
                ):
                    error = LLMTimeoutError(
                        f"Local inference timed out after "
                        f"{self.timeout_seconds:.1f}s."
                    )
                else:
                    error = LLMTransportError(
                        "Could not reach local inference endpoint "
                        f"{self.base_url!r}: {reason}"
                    )

                last_error = error

                if attempt < self.max_retries:
                    self._retry_sleep(
                        attempt
                    )
                    continue

                raise error from exc

        if last_error is not None:
            raise last_error

        raise LLMTransportError(
            "Inference request failed without a captured error."
        )

    def _retry_sleep(
        self,
        attempt: int,
    ) -> None:
        delay = self.retry_backoff_seconds * (
            2 ** attempt
        )

        time.sleep(
            delay
        )

    @staticmethod
    def _read_http_error_body(
        error: urllib.error.HTTPError,
    ) -> str:
        try:
            raw = error.read(
                64 * 1024
            )
        except Exception:
            return ""

        try:
            return raw.decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            return ""

    @staticmethod
    def _http_error(
        status_code: int,
        body_text: str,
    ) -> LLMServiceError:
        message = ""

        if body_text:
            try:
                parsed = json.loads(
                    body_text
                )

                if isinstance(
                    parsed,
                    Mapping,
                ):
                    message = _clean(
                        parsed.get(
                            "error"
                        )
                        or parsed.get(
                            "message"
                        )
                    )
            except Exception:
                message = _clean(
                    body_text
                )[
                    :500
                ]

        lowered = message.casefold()

        if (
            status_code
            in {
                404,
                400,
            }
            and "model"
            in lowered
            and (
                "not found"
                in lowered
                or "not available"
                in lowered
            )
        ):
            return LLMModelUnavailableError(
                message
                or "Requested local model is unavailable."
            )

        return LLMTransportError(
            f"Inference endpoint returned HTTP {status_code}"
            + (
                f": {message}"
                if message
                else ""
            )
        )

    # ------------------------------------------------------------------
    # Response parsing / metadata
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_chat_text(
        response: Mapping[str, Any],
    ) -> str:
        error = _clean(
            response.get(
                "error"
            )
        )

        if error:
            raise LLMResponseError(
                error
            )

        message = response.get(
            "message"
        )

        if not isinstance(
            message,
            Mapping,
        ):
            raise LLMResponseError(
                "Ollama chat response is missing message object."
            )

        content = message.get(
            "content"
        )

        if content is None:
            raise LLMResponseError(
                "Ollama chat response is missing message.content."
            )

        if not isinstance(
            content,
            str,
        ):
            content = str(
                content
            )

        return content

    def _response_metadata(
        self,
        *,
        response: Mapping[str, Any],
        selected_model: str,
        elapsed_seconds: float,
        prompt: str,
        system_prompt: str,
        generation_options: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "service_version": SERVICE_VERSION,
            "transport_version": TRANSPORT_VERSION,
            "provider": "ollama",
            "model": _clean(
                response.get(
                    "model"
                )
                or selected_model
            ),
            "base_url": self.base_url,
            "remote_base_url_allowed": self.allow_remote_base_url,
            "created_at": _clean(
                response.get(
                    "created_at"
                )
            ),
            "done": bool(
                response.get(
                    "done",
                    True,
                )
            ),
            "done_reason": _clean(
                response.get(
                    "done_reason"
                )
            ),
            "total_duration_ns": response.get(
                "total_duration"
            ),
            "load_duration_ns": response.get(
                "load_duration"
            ),
            "prompt_eval_count": response.get(
                "prompt_eval_count"
            ),
            "prompt_eval_duration_ns": response.get(
                "prompt_eval_duration"
            ),
            "eval_count": response.get(
                "eval_count"
            ),
            "eval_duration_ns": response.get(
                "eval_duration"
            ),
            "request_elapsed_ms": round(
                elapsed_seconds
                * 1000.0,
                2,
            ),
            "prompt_sha256": _sha256_text(
                prompt
            ),
            "system_prompt_sha256": (
                _sha256_text(
                    system_prompt
                )
                if system_prompt
                else ""
            ),
            "generation_options": _json_safe(
                generation_options
            ),
            "request_metadata": _json_safe(
                request_metadata
            ),
            "paid_api_used": False,
            "model_route_chosen_here": False,
            "research_depth_modified_here": False,
            "policy_modified_here": False,
            "output_is_grounding_evidence": False,
            "authorizes_release": False,
            "raw_prompt_stored": False,
            "raw_response_stored": False,
            "private_chain_of_thought_stored": False,
        }

    # ------------------------------------------------------------------
    # Base URL safety
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_base_url(
        base_url: str,
        *,
        allow_remote: bool,
    ) -> None:
        parsed = urllib.parse.urlparse(
            base_url
        )

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise LLMConfigurationError(
                "base_url must use http or https."
            )

        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise LLMConfigurationError(
                "base_url must be a clean HTTP(S) endpoint without "
                "userinfo/query/fragment."
            )

        if allow_remote:
            return

        hostname = parsed.hostname.casefold()

        local_names = {
            "localhost",
            "127.0.0.1",
            "::1",
        }

        if hostname not in local_names:
            raise LLMConfigurationError(
                "Remote inference base_url is disabled by default. "
                "Use allow_remote_base_url=True only for an explicitly approved "
                "self-hosted endpoint."
            )


# Compatibility alias.
LocalLLMService = LLMService
