"""Provider-agnostic LLM router for orchestrators and web API."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from modules.cursor_cli_adapter import CursorCliAdapter, CursorCliResult
from modules.codebase_context import chat_max_tokens
from modules.gemini_web_adapter import GeminiWebAdapterNonApi, GeminiWebResult
from modules.inception_adapter import InceptionResult, call_agent as inception_call_agent
from modules.inception_adapter import call_chat as inception_call_chat
from modules.inception_adapter import iter_call_stream as inception_iter_call_stream
from modules.inception_agent_policy import should_use_agent_loop
from modules.inception_agent_policy import (
    chat_strict_generation_defaults,
    is_chat_strict_mode,
)

logger = logging.getLogger(__name__)


def mercury_only_enabled() -> bool:
    """When true, only Inception Mercury is allowed (no Cursor CLI / other providers)."""
    raw = os.getenv("OPRAI_MERCURY_ONLY", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def mercury_only_blocks_provider(provider: str) -> bool:
    return mercury_only_enabled() and provider.strip().lower() != "inception"


@dataclass
class LLMRouterResult:
    """Standardized response envelope for LLM calls."""

    success: bool
    content: str
    provider: str
    model: str
    error: Optional[str] = None
    error_class: Optional[str] = None
    ok: Optional[bool] = None
    latency_ms: Optional[int] = None


class LLMRouter:
    """Routes requests to configured LLM provider with fallback."""

    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "inception").strip().lower()
        self.default_model = os.getenv("LLM_MODEL", "mercury-2")
        self.fallback_provider = os.getenv("LLM_FALLBACK_PROVIDER", "disabled").strip().lower()
        self.cursor_adapter = CursorCliAdapter()
        self.gemini_adapter = GeminiWebAdapterNonApi()

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.1,
        explore_mode: bool = False,
        allow_writes: bool = False,
    ) -> LLMRouterResult:
        """Complete chat request via configured provider with fallback."""
        target_model = model or self.default_model
        if target_model.strip().lower() == "auto" and self.provider == "inception":
            target_model = os.getenv("LLM_MODEL", "mercury-2")
        token_budget = max_tokens if max_tokens is not None else chat_max_tokens()
        if not allow_writes:
            allow_writes = os.getenv("OPRAI_ALLOW_WRITES", "").strip().lower() in ("1", "true", "yes")

        if mercury_only_blocks_provider(self.provider):
            return self._finalize_result(
                LLMRouterResult(
                success=False,
                content="",
                provider=self.provider,
                model=target_model,
                error=f"mercury_only: provider {self.provider!r} disabled (use LLM_PROVIDER=inception)",
                error_class="policy_blocked",
                )
            )

        if self.provider == "inception":
            user_text = ""
            for item in reversed(messages):
                if item.get("role") == "user":
                    user_text = str(item.get("content", ""))
                    break
            from modules.inception_agent_policy import resolve_temperature
            chat_temperature = resolve_temperature(agent_loop=False)
            chat_token_budget = min(token_budget, 2048)

            if is_chat_strict_mode(
                user_text=user_text,
                allow_writes=allow_writes,
                explore_mode=explore_mode,
            ):
                defaults = chat_strict_generation_defaults()
                chat_token_budget = min(chat_token_budget, int(defaults["max_tokens"]))
                chat_temperature = float(defaults["temperature"])

            if should_use_agent_loop(user_text, explore_mode=explore_mode, allow_writes=allow_writes):
                inception_result = inception_call_agent(
                    messages=messages,
                    model=target_model,
                    max_tokens=None,
                    temperature=resolve_temperature(agent_loop=True),
                    explore_mode=explore_mode,
                    allow_writes=allow_writes,
                )
            else:
                inception_result = inception_call_chat(
                    messages=messages,
                    model=target_model,
                    max_tokens=chat_token_budget,
                    temperature=chat_temperature,
                    reasoning_effort=os.getenv("INCEPTION_CHAT_REASONING_EFFORT", "instant"),
                    explore_mode=explore_mode,
                )
            return self._from_inception_result(inception_result, target_model)

        if self.provider == "cursor_cli":
            cursor_result = self.cursor_adapter.call(
                messages=messages,
                model=target_model,
                max_tokens=token_budget,
                temperature=temperature,
                mode=os.getenv("CURSOR_AGENT_MODE", "ask"),
                allow_writes=allow_writes,
                explore_mode=explore_mode,
            )
            if cursor_result.success:
                return self._from_cursor_result(cursor_result, target_model)

            if self.fallback_provider == "gemini_web_subscription":
                logger.warning("Cursor CLI adapter failed, trying Gemini fallback")
                gemini_result = self.gemini_adapter.call(
                    messages=messages,
                    model=target_model,
                    max_tokens=token_budget,
                    temperature=temperature,
                )
                if gemini_result.success:
                    result = self._from_gemini_result(gemini_result, target_model)
                    result.error = cursor_result.error
                    return result

            return self._finalize_result(
                LLMRouterResult(
                success=False,
                content="",
                provider="cursor_cli",
                model=target_model,
                error=cursor_result.error or "Cursor CLI adapter failed",
                error_class="provider_failure",
                latency_ms=cursor_result.latency_ms,
                )
            )

        if self.provider == "gemini_web_subscription":
            gemini_result = self.gemini_adapter.call(
                messages=messages,
                model=target_model,
                max_tokens=token_budget,
                temperature=temperature,
            )
            if gemini_result.success:
                return self._from_gemini_result(gemini_result, target_model)

            if self.fallback_provider == "xai":
                logger.warning("Gemini adapter failed, trying xAI compatibility fallback")
                fallback = self._call_xai(messages, target_model, token_budget, temperature)
                if fallback.success:
                    fallback.error = gemini_result.error
                return fallback

            return self._finalize_result(
                LLMRouterResult(
                success=False,
                content="",
                provider="gemini_web_subscription",
                model=target_model,
                error=gemini_result.error or "Gemini adapter failed and fallback is disabled",
                error_class="provider_failure",
                )
            )

        if self.provider == "xai":
            return self._call_xai(messages, target_model, token_budget, temperature)

        return self._finalize_result(
            LLMRouterResult(
            success=False,
            content="",
            provider=self.provider,
            model=target_model,
            error=f"Unsupported provider: {self.provider}",
            error_class="unsupported_provider",
            )
        )

    def _from_cursor_result(self, result: CursorCliResult, model: str) -> LLMRouterResult:
        return self._finalize_result(LLMRouterResult(
            success=result.success,
            content=result.content,
            provider="cursor_cli",
            model=model,
            error=result.error,
            error_class=None if result.success else "provider_failure",
            latency_ms=result.latency_ms,
        ))

    def _from_gemini_result(self, result: GeminiWebResult, model: str) -> LLMRouterResult:
        return self._finalize_result(LLMRouterResult(
            success=result.success,
            content=result.content,
            provider="gemini_web_subscription",
            model=model,
            error=result.error,
            error_class=None if result.success else "provider_failure",
            latency_ms=result.latency_ms,
        ))

    def _from_inception_result(self, result: InceptionResult, model: str) -> LLMRouterResult:
        return self._finalize_result(LLMRouterResult(
            success=result.success,
            content=result.content,
            provider="inception",
            model=result.model or model,
            error=result.error,
            error_class=None if result.success else "provider_failure",
            latency_ms=result.latency_ms,
        ))

    def _call_xai(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMRouterResult:
        """Fallback path for existing Grok/xAI configuration."""
        api_key = os.getenv("GROK_API_KEY", "")
        if not api_key:
            return self._finalize_result(LLMRouterResult(
                success=False,
                content="",
                provider="xai",
                model=model,
                error="GROK_API_KEY is not configured",
                error_class="provider_not_configured",
            ))

        api_url = os.getenv("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
        payload = {
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        try:
            response = requests.post(
                api_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json=payload,
                timeout=60,
            )
            if response.status_code != 200:
                return self._finalize_result(LLMRouterResult(
                    success=False,
                    content="",
                    provider="xai",
                    model=model,
                    error=f"xAI request failed ({response.status_code}): {response.text[:500]}",
                    error_class="provider_failure",
                ))
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return self._finalize_result(LLMRouterResult(
                success=True,
                content=content,
                provider="xai",
                model=model,
            ))
        except Exception as exc:
            return self._finalize_result(LLMRouterResult(
                success=False,
                content="",
                provider="xai",
                model=model,
                error=str(exc),
                error_class="provider_failure",
            ))

    def _finalize_result(self, result: LLMRouterResult) -> LLMRouterResult:
        if result.ok is None:
            result.ok = bool(result.success and not result.error)
        return result

    def health(self) -> Dict[str, Any]:
        """Expose runtime health and metrics for reliability endpoints."""
        payload: Dict[str, Any] = {
            "provider": self.provider,
            "fallback_provider": self.fallback_provider,
            "default_model": self.default_model,
            "cursor_adapter": self.cursor_adapter.health(),
            "gemini_adapter": self.gemini_adapter.health(),
        }
        if self.provider == "cursor_cli":
            payload["primary_health"] = self.cursor_adapter.check_health()
        elif self.provider == "gemini_web_subscription":
            payload["primary_health"] = self.gemini_adapter.check_health()
        elif self.provider == "inception":
            from modules.inception_adapter import check_health as inception_health
            from modules.inception_agent_tools import tool_schemas

            payload["primary_health"] = inception_health()
            payload["inception_agent"] = {
                "tools": [t["function"]["name"] for t in tool_schemas()],
                "max_steps": int(os.getenv("INCEPTION_AGENT_MAX_STEPS", "12")),
            }
        return payload

    def iter_call_stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        explore_mode: bool = False,
        allow_writes: bool = False,
    ):
        """Yield SSE events for configured provider stream."""
        if not allow_writes:
            allow_writes = os.getenv("OPRAI_ALLOW_WRITES", "").strip().lower() in ("1", "true", "yes")
        target_model = model or self.default_model
        if target_model.strip().lower() == "auto" and self.provider == "inception":
            target_model = os.getenv("LLM_MODEL", "mercury-2")
        token_budget = max_tokens if max_tokens is not None else chat_max_tokens()

        if mercury_only_blocks_provider(self.provider):
            yield ("error", {
                "error": f"mercury_only: provider {self.provider!r} disabled",
                "provider": self.provider,
            })
            return

        if self.provider == "inception":
            yield from inception_iter_call_stream(
                messages=messages,
                model=target_model,
                max_tokens=token_budget,
                explore_mode=explore_mode,
                allow_writes=allow_writes,
            )
            return

        yield from self.cursor_adapter.iter_call_stream(
            messages=messages,
            model=target_model,
            max_tokens=token_budget,
            temperature=0.1,
            mode=os.getenv("CURSOR_AGENT_MODE", "ask"),
            allow_writes=allow_writes,
            explore_mode=explore_mode,
        )


_router_singleton: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = LLMRouter()
    return _router_singleton
