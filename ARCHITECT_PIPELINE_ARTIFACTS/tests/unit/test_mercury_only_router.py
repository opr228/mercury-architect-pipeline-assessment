"""Mercury-only routing gate."""

import os

import pytest

from modules.llm_router import LLMRouter, mercury_only_blocks_provider, mercury_only_enabled


def test_mercury_only_default_on():
    os.environ.pop("OPRAI_MERCURY_ONLY", None)
    assert mercury_only_enabled() is True


def test_mercury_only_can_disable():
    os.environ["OPRAI_MERCURY_ONLY"] = "0"
    try:
        assert mercury_only_enabled() is False
        assert mercury_only_blocks_provider("cursor_cli") is False
    finally:
        os.environ.pop("OPRAI_MERCURY_ONLY", None)


def test_mercury_only_blocks_cursor():
    os.environ["OPRAI_MERCURY_ONLY"] = "1"
    try:
        assert mercury_only_blocks_provider("cursor_cli") is True
        assert mercury_only_blocks_provider("inception") is False
    finally:
        os.environ.pop("OPRAI_MERCURY_ONLY", None)


def test_router_complete_blocks_cursor_cli():
    os.environ["OPRAI_MERCURY_ONLY"] = "1"
    os.environ["LLM_PROVIDER"] = "cursor_cli"
    try:
        router = LLMRouter()
        result = router.complete(messages=[{"role": "user", "content": "hi"}])
        assert result.success is False
        assert "mercury_only" in (result.error or "")
        assert result.provider == "cursor_cli"
    finally:
        os.environ.pop("OPRAI_MERCURY_ONLY", None)
        os.environ.pop("LLM_PROVIDER", None)
