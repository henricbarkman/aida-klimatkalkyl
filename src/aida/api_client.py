"""Shared Anthropic API client configuration."""

from __future__ import annotations

import os

import anthropic


def get_client() -> anthropic.Anthropic:
    """Get configured Anthropic client.

    Supports ANTHROPIC_API_KEY, and CLAUDE_CODE_OAUTH_TOKEN (as auth_token).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_token:
        return anthropic.Anthropic(auth_token=oauth_token)

    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN."
    )


# Default model for AIda agents
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Extended thinking budgets (tokens)
THINKING_LOW = 1024
THINKING_STANDARD = 5000

# Models that support extended thinking
_THINKING_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}


def thinking_config(budget: int):
    """Return thinking parameter for API calls. NOT_GIVEN for unsupported models."""
    if DEFAULT_MODEL not in _THINKING_MODELS:
        return anthropic.NOT_GIVEN
    return {"type": "enabled", "budget_tokens": budget}


def extract_text(response) -> str:
    """Extract text content, works with both thinking and non-thinking responses."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
