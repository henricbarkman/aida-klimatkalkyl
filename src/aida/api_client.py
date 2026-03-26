"""Shared API client configuration. Routes through OpenRouter."""

from __future__ import annotations

import os

import anthropic

OPENROUTER_BASE_URL = "https://openrouter.ai/api"


def get_client() -> anthropic.Anthropic:
    """Get Anthropic client routed through OpenRouter.

    Uses OPENROUTER_API_KEY (primary), falls back to direct Anthropic access.
    """
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        return anthropic.Anthropic(
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    raise RuntimeError(
        "No API key found. Set OPENROUTER_API_KEY or ANTHROPIC_API_KEY."
    )


# Default model for AIda agents — OpenRouter format
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

# Extended thinking budgets (tokens)
THINKING_LOW = 1024
THINKING_STANDARD = 5000

# Models that support extended thinking
_THINKING_MODELS = {"anthropic/claude-sonnet-4", "anthropic/claude-opus-4.6"}


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
