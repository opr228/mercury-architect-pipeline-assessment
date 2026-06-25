"""Unit tests for Inception agent routing and permissions."""

from modules.inception_agent_policy import (
    agent_max_tokens,
    build_agent_system_messages,
    chat_strict_generation_defaults,
    chat_reliability_retry_limit,
    clamp_mercury_temperature,
    is_chat_reliability_enabled,
    is_chat_strict_mode,
    max_agent_tool_turns,
    resolve_reasoning_effort,
    resolve_temperature,
    should_use_agent_loop,
    trim_agent_thread,
)
from modules.inception_agent_tools import AgentRuntime, can_read_path


def test_simple_chat_skips_agent_loop():
    assert should_use_agent_loop("Reply with one word: mercury", False, False) is False
    assert should_use_agent_loop("привет", False, False) is False


def test_repo_question_uses_agent_loop():
    assert should_use_agent_loop("Where is llm_router.py defined?", False, False) is True
    assert should_use_agent_loop("прочитай modules/inception_adapter.py", False, False) is True


def test_explore_always_agent():
    assert should_use_agent_loop("hello", True, False) is True
    assert should_use_agent_loop("hello", False, True) is True


def test_lean_read_allowlist_blocks_secrets():
    runtime = AgentRuntime(explore_mode=False, workspace="/home/opr")
    ok, _ = can_read_path("/home/opr/env.secrets", runtime)
    assert ok is False


def test_lean_read_allowlist_permits_modules():
    runtime = AgentRuntime(explore_mode=False, workspace="/home/opr")
    ok, reason = can_read_path("/home/opr/modules/llm_router.py", runtime)
    assert ok is True, reason


def test_mercury_temperature_defaults_and_clamp():
    assert resolve_temperature(agent_loop=False) == 0.65
    assert resolve_temperature(agent_loop=True) == 0.5
    assert clamp_mercury_temperature(0.0) == 0.0
    assert clamp_mercury_temperature(0.1) == 0.1
    assert clamp_mercury_temperature(0.75) == 0.75
    assert clamp_mercury_temperature(1.2) == 1.0


def test_trim_agent_thread_keeps_recent_tool_turns():
    thread = [
        {"role": "system", "content": "ctx"},
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "find llm_router"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "turn1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
        {"role": "tool", "tool_call_id": "2", "content": "turn2"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "3"}]},
        {"role": "tool", "tool_call_id": "3", "content": "turn3"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "4"}]},
        {"role": "tool", "tool_call_id": "4", "content": "turn4"},
    ]
    trimmed = trim_agent_thread(thread, max_tool_turns=2)
    tool_contents = [m["content"] for m in trimmed if m.get("role") == "tool"]
    assert "turn1" not in tool_contents
    assert "turn2" not in tool_contents
    assert "turn3" in tool_contents and "turn4" in tool_contents
    assert any("omitted" in m.get("content", "") for m in trimmed if m.get("role") == "system")
    assert max_agent_tool_turns() >= 1


def test_agent_system_messages_merged_to_two_blocks():
    blocks = build_agent_system_messages(AgentRuntime())
    assert len(blocks) == 2
    joined = "\n".join(blocks)
    assert "<persona>" in joined and "<grounding>" in joined
    assert "<workflow>" in joined and "<tool_routing>" in joined
    assert "<tools>" not in joined


def test_critical_rules_last_and_anti_fabrication():
    blocks = build_agent_system_messages(AgentRuntime())
    workflow = blocks[1]
    assert "<critical_rules>" in workflow
    assert "Never fabricate" in workflow
    assert "not measured" in workflow
    assert workflow.index("<critical_rules>") > workflow.index("<self_check>")
    assert workflow.rstrip().endswith("</critical_rules>")


def test_tool_routing_has_audit_negative_fewshot():
    blocks = build_agent_system_messages(AgentRuntime())
    joined = "\n".join(blocks)
    assert "Do NOT do this" in joined
    assert "invented" in joined.lower()


def test_tool_routing_has_roadmap_fewshot():
    blocks = build_agent_system_messages(AgentRuntime())
    joined = "\n".join(blocks)
    assert "mercury-roadmap" in joined
    assert "inception_agent_tools" in joined


def test_audit_reasoning_effort_high():
    assert resolve_reasoning_effort(
        agent_loop=True,
        explore_mode=False,
        allow_writes=False,
        tool_steps=1,
        synthesis_step=False,
        audit_task=True,
    ) == "high"


def test_roadmap_reasoning_effort_high():
    assert resolve_reasoning_effort(
        agent_loop=True,
        explore_mode=False,
        allow_writes=False,
        tool_steps=1,
        synthesis_step=False,
        roadmap_task=True,
    ) == "high"


def test_roadmap_synthesis_max_tokens():
    assert agent_max_tokens(synthesis_step=True, roadmap_task=True) >= 4096


def test_synthesis_reasoning_and_max_tokens():
    assert resolve_reasoning_effort(
        agent_loop=True,
        explore_mode=False,
        allow_writes=False,
        tool_steps=2,
        synthesis_step=True,
    ) == "instant"
    assert resolve_reasoning_effort(
        agent_loop=True,
        explore_mode=False,
        allow_writes=False,
        tool_steps=2,
        synthesis_step=False,
    ) == "medium"
    assert agent_max_tokens(synthesis_step=True) == 2048
    assert agent_max_tokens(synthesis_step=False) == 8192


def test_chat_strict_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("OPRAI_CHAT_STRICT_ENABLED", "0")
    assert (
        is_chat_strict_mode(
            user_text="что делает модуль",
            allow_writes=False,
            explore_mode=False,
        )
        is False
    )


def test_chat_strict_defaults_use_env_max_tokens(monkeypatch):
    monkeypatch.setenv("OPRAI_CHAT_STRICT_MAX_TOKENS", "640")
    defaults = chat_strict_generation_defaults()
    assert defaults["temperature"] == 0.0
    assert defaults["max_tokens"] == 640


def test_chat_reliability_flags(monkeypatch):
    monkeypatch.setenv("OPRAI_CHAT_RELIABILITY_ENABLED", "1")
    monkeypatch.setenv("OPRAI_CHAT_RELIABILITY_RETRY_LIMIT", "1")
    assert is_chat_reliability_enabled() is True
    assert chat_reliability_retry_limit() == 1
