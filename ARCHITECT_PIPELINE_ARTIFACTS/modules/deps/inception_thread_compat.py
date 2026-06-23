"""Mercury 2 message-thread normalization (OpenClaw / Pipecat compat patterns).

Reference: vendor/mercury-agent, openclaw/openclaw#25956, pipecat inception/llm.py
"""

from __future__ import annotations

from typing import Any, Dict, List


def _message_content(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if content is None:
        return ""
    return str(content)


def _normalize_role(role: Any) -> str:
    raw = str(role or "user").strip().lower()
    if raw == "developer":
        return "system"
    return raw


def _assistant_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"role": "assistant", "content": _message_content(msg)}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
        if out["content"] is None:
            out["content"] = ""
    if msg.get("function_call"):
        out["function_call"] = msg["function_call"]
        if out["content"] is None:
            out["content"] = ""
    return out


def normalize_messages_for_mercury(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prepare OpenAI-style messages for Inception Mercury 2 API."""
    if not messages:
        return []

    normalized: List[Dict[str, Any]] = []
    pending_system: List[str] = []

    def flush_system() -> None:
        if not pending_system:
            return
        normalized.append({"role": "system", "content": "\n\n".join(pending_system)})
        pending_system.clear()

    for raw in messages:
        if not isinstance(raw, dict):
            continue
        role = _normalize_role(raw.get("role"))
        if role == "system":
            text = _message_content(raw).strip()
            if text:
                pending_system.append(text)
            continue

        flush_system()

        if role == "assistant":
            normalized.append(_assistant_message(raw))
            continue

        if role == "tool":
            normalized.append(
                {
                    "role": "tool",
                    "tool_call_id": str(raw.get("tool_call_id") or raw.get("id") or "call_unknown"),
                    "content": _message_content(raw),
                }
            )
            continue

        if role == "user":
            if normalized and normalized[-1].get("role") == "user":
                prev = normalized[-1]
                prev["content"] = f"{_message_content(prev)}\n\n{_message_content(raw)}".strip()
                continue
            normalized.append({"role": "user", "content": _message_content(raw)})
            continue

        normalized.append({"role": role, "content": _message_content(raw)})

    flush_system()
    return _inject_assistant_after_tool_block(normalized)


def _inject_assistant_after_tool_block(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If tool block is immediately followed by user, insert empty assistant (Mercury alternation)."""
    if len(messages) < 2:
        return messages

    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        out.append(msg)
        if msg.get("role") != "tool":
            i += 1
            continue
        j = i
        while j + 1 < len(messages) and messages[j + 1].get("role") == "tool":
            j += 1
            out.append(messages[j])
        if j + 1 < len(messages) and messages[j + 1].get("role") == "user":
            out.append({"role": "assistant", "content": ""})
        i = j + 1
    return out
