from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


logger = logging.getLogger("writing_brain.llm")


DEFAULT_BASE_URL = "https://www.packyapi.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_PPCHAT_BASE_URL = "https://code.ppchat.vip/v1"


def call_packy_chat(
    *,
    prompt: str,
    system_prompt: str,
    default_model: str,
    model_env_vars: list[str],
    temperature: float = 0.3,
    max_tokens: int = 1800,
    response_format: dict[str, Any] | None = None,
    api_key_env_vars: list[str] | None = None,
    base_url_env_vars: list[str] | None = None,
    provider: str = "packyapi",
    usage_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    return call_openai_compatible_chat(
        prompt=prompt,
        system_prompt=system_prompt,
        default_model=default_model,
        model_env_vars=model_env_vars,
        api_key_env_vars=api_key_env_vars or ["PACKYAPI_API_KEY", "WRITING_BRAIN_API_KEY"],
        base_url_env_vars=base_url_env_vars or ["PACKYAPI_BASE_URL", "WRITING_BRAIN_BASE_URL"],
        default_base_url=DEFAULT_BASE_URL,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        usage_context=usage_context,
    )


def call_gemini_native_chat(
    *,
    prompt: str,
    system_prompt: str,
    default_model: str,
    model_env_vars: list[str],
    temperature: float = 0.3,
    max_tokens: int = 1800,
    response_json_schema: dict[str, Any] | None = None,
    usage_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    api_key = _resolve_env(["GEMINI_API_KEY", "GOOGLE_API_KEY", "WRITING_BRAIN_GEMINI_API_KEY"])
    model = _resolve_model(default_model, model_env_vars)
    if not api_key:
        return {
            "mode": "prompt_only",
            "reply_text": "Gemini native 未调用。请配置 GEMINI_API_KEY 或 GOOGLE_API_KEY 后重试。",
            "provider": "google_genai",
            "model": model,
        }

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        return {
            "mode": "prompt_only",
            "reply_text": f"Gemini native 未调用。请安装 google-genai。错误：{exc!r}",
            "provider": "google_genai",
            "model": model,
        }

    client = None
    try:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        api_version = os.environ.get("WRITING_BRAIN_GEMINI_API_VERSION") or os.environ.get("GEMINI_API_VERSION")
        if api_version and api_version.strip():
            client_kwargs["http_options"] = types.HttpOptions(api_version=api_version.strip())
        client = genai.Client(**client_kwargs)
        config: dict[str, Any] = {
            "system_instruction": system_prompt,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if response_json_schema:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = response_json_schema
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        parsed_response = _serialize_genai_value(getattr(response, "parsed", None))
        reply_text = str(getattr(response, "text", "") or "").strip()
        if parsed_response is not None and not reply_text:
            reply_text = json.dumps(parsed_response, ensure_ascii=False)
        raw_usage = {}
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata:
            raw_usage = {
                "prompt_tokens": getattr(usage_metadata, "prompt_token_count", 0) or 0,
                "completion_tokens": getattr(usage_metadata, "candidates_token_count", 0) or 0,
                "total_tokens": getattr(usage_metadata, "total_token_count", 0) or 0,
            }
        result = {
            "mode": "model_output",
            "reply_text": reply_text,
            "provider": "google_genai",
            "model": model,
            "parsed_response": parsed_response,
            "usage": raw_usage,
        }
        _maybe_record_usage(result, usage_context=usage_context)
        return result
    except Exception as exc:
        logger.warning("Gemini native call failed: %s", exc)
        return {
            "mode": "prompt_only",
            "reply_text": f"Gemini native 调用失败，已退回 prompt_only。错误：{exc!r}",
            "error_detail": str(exc),
            "provider": "google_genai",
            "model": model,
        }
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                logger.debug("Failed to close Gemini client", exc_info=True)


def call_ppchat_chat(
    *,
    prompt: str,
    system_prompt: str,
    default_model: str,
    model_env_vars: list[str],
    temperature: float = 0.3,
    max_tokens: int = 1800,
    usage_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    return call_openai_compatible_chat(
        prompt=prompt,
        system_prompt=system_prompt,
        default_model=default_model,
        model_env_vars=model_env_vars,
        api_key_env_vars=["PPCHAT_API_KEY", "OPENAI_API_KEY", "WRITING_BRAIN_REVISE_API_KEY"],
        base_url_env_vars=["PPCHAT_BASE_URL", "OPENAI_BASE_URL", "WRITING_BRAIN_REVISE_BASE_URL"],
        default_base_url=DEFAULT_PPCHAT_BASE_URL,
        provider="ppchat",
        temperature=temperature,
        max_tokens=max_tokens,
        usage_context=usage_context,
    )


def call_anthropic_messages(
    *,
    prompt: str,
    system_prompt: str,
    default_model: str,
    model_env_vars: list[str],
    temperature: float = 0.3,
    max_tokens: int = 1800,
    usage_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    model = _resolve_model(default_model, model_env_vars)
    if not api_key:
        return {
            "mode": "prompt_only",
            "reply_text": "Claude 未调用。请配置 ANTHROPIC_API_KEY 后重试。",
            "provider": "anthropic",
            "model": model,
        }

    base_url = normalize_base_url(
        os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("CLAUDE_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL
    )
    if base_url.endswith("/v1"):
        endpoint = f"{base_url}/messages"
    else:
        endpoint = f"{base_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        body = _http_post_json_with_retry(
            endpoint,
            payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": "writing-brain/0.1",
            },
            timeout=180,
        )
    except Exception as exc:
        logger.warning("Anthropic call failed after retries: %s", exc)
        return {
            "mode": "prompt_only",
            "reply_text": f"Claude 调用失败，已退回 prompt_only。错误：{exc!r}",
            "error_detail": str(exc),
            "provider": "anthropic",
            "model": model,
        }
    try:
        data = json.loads(body)
        if "error" in data:
            return {
                "mode": "prompt_only",
                "reply_text": f"Claude 调用失败，已退回 prompt_only。错误：{json.dumps(data['error'], ensure_ascii=False)}",
                "provider": "anthropic",
                "model": model,
            }
        parts = data.get("content") or []
        reply = "\n".join(
            str(item.get("text") or "").strip()
            for item in parts
            if isinstance(item, dict) and item.get("type") == "text" and str(item.get("text") or "").strip()
        ).strip()
        raw_usage = dict(data.get("usage") or {})
    except Exception as exc:
        logger.warning("Anthropic response parse failed: %s", exc)
        return {
            "mode": "prompt_only",
            "reply_text": f"Claude 调用失败，已退回 prompt_only。错误：{exc!r}",
            "error_detail": str(exc),
            "provider": "anthropic",
            "model": model,
        }
    result = {
        "mode": "model_output",
        "reply_text": reply,
        "provider": "anthropic",
        "model": model,
        "usage": {
            "prompt_tokens": int(raw_usage.get("input_tokens") or 0),
            "completion_tokens": int(raw_usage.get("output_tokens") or 0),
            "total_tokens": int(raw_usage.get("input_tokens") or 0) + int(raw_usage.get("output_tokens") or 0),
        },
    }
    _maybe_record_usage(result, usage_context=usage_context)
    return result


def call_openai_compatible_chat(
    *,
    prompt: str,
    system_prompt: str,
    default_model: str,
    model_env_vars: list[str],
    api_key_env_vars: list[str],
    base_url_env_vars: list[str],
    default_base_url: str,
    provider: str,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    response_format: dict[str, Any] | None = None,
    usage_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    api_key = _resolve_env(api_key_env_vars)
    model = _resolve_model(default_model, model_env_vars)
    if not api_key:
        return {
            "mode": "prompt_only",
            "reply_text": f"{provider} 未调用。请配置对应 API key 后重试。",
            "provider": provider,
            "model": model,
        }
    base_url = normalize_base_url(_resolve_env(base_url_env_vars) or default_base_url)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    try:
        body = _http_post_json_with_retry(
            f"{base_url}/chat/completions",
            payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "writing-brain/0.1",
            },
            timeout=180,
        )
    except Exception as exc:
        logger.warning("%s call failed after retries: %s", provider, exc)
        return {
            "mode": "prompt_only",
            "reply_text": f"{provider} 调用失败，已退回 prompt_only。错误：{exc!r}",
            "error_detail": str(exc),
            "provider": provider,
            "model": model,
        }
    try:
        data = json.loads(body)
        if "error" in data:
            return {
                "mode": "prompt_only",
                "reply_text": f"{provider} 调用失败，已退回 prompt_only。错误：{json.dumps(data['error'], ensure_ascii=False)}",
                "provider": provider,
                "model": model,
            }
        reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        raw_usage = dict(data.get("usage") or {})
    except Exception as exc:
        logger.warning("%s response parse failed: %s", provider, exc)
        return {
            "mode": "prompt_only",
            "reply_text": f"{provider} 调用失败，已退回 prompt_only。错误：{exc!r}",
            "error_detail": str(exc),
            "provider": provider,
            "model": model,
        }
    result = {
        "mode": "model_output",
        "reply_text": str(reply).strip(),
        "provider": provider,
        "model": model,
        "usage": raw_usage,
    }
    _maybe_record_usage(result, usage_context=usage_context)
    return result


def normalize_base_url(raw: str) -> str:
    normalized = raw.rstrip("/")
    parsed = urlparse(normalized)
    path = (parsed.path or "").rstrip("/")
    if not path:
        return f"{normalized}/v1"
    return normalized


def _serialize_genai_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _serialize_genai_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_genai_value(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
            return _serialize_genai_value(dumped)
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            dumped = value.dict()
            return _serialize_genai_value(dumped)
        except Exception:
            pass
    return str(value)


def is_env_enabled(*names: str) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return False


def _resolve_model(default_model: str, env_vars: list[str]) -> str:
    for name in env_vars:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default_model


def _resolve_env(names: list[str]) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _maybe_record_usage(result: dict[str, Any], *, usage_context: dict[str, str] | None = None) -> None:
    ctx = usage_context
    if not ctx:
        try:
            from .usage import get_current_context
            ctx = get_current_context()
        except Exception:
            logger.debug("Failed to load usage context", exc_info=True)
    if not ctx:
        return
    data_dir = ctx.get("data_dir", "")
    if not data_dir:
        return
    usage = dict(result.get("usage") or {})
    if not usage:
        return
    try:
        from .usage import record
        caller = ctx.get("caller", "") or ctx.get("caller_prefix", "")
        record(
            data_dir,
            run_id=ctx.get("run_id", ""),
            caller=caller,
            provider=str(result.get("provider") or ""),
            model=str(result.get("model") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
    except Exception:
        logger.debug("Failed to record usage", exc_info=True)
def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> str:
    """POST JSON payload and return response body as string."""
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _http_post_json_with_retry(
    url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: int = 180, max_retries: int = 3,
) -> str:
    """POST with exponential backoff retry."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return _http_post_json(url, payload, headers, timeout=timeout)
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning("LLM HTTP attempt %d/%d failed: %s. Retrying in %ds...", attempt + 1, max_retries, exc, wait)
                time.sleep(wait)
    raise last_error  # type: ignore[misc]
