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
DEFAULT_MODEL = "claude-sonnet-4-6"
