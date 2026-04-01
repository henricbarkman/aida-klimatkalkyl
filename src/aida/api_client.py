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
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

# Extended thinking budgets (tokens)
# NONE: Simple parsing, no reasoning needed (chat)
# LOW: Understand input, extract structure (intake)
# STANDARD: Reason about data, compare, search (pricing, alternatives)
# DEEP: Complex analysis, synthesis, conclusions (baseline, report)
THINKING_NONE = 0
THINKING_LOW = 1024
THINKING_STANDARD = 5000
THINKING_DEEP = 10000

# Models that support extended thinking
_THINKING_MODELS = {"anthropic/claude-sonnet-4-6", "anthropic/claude-sonnet-4", "anthropic/claude-opus-4.6"}


def thinking_config(budget: int):
    """Return thinking parameter for API calls. NOT_GIVEN for unsupported models or NONE budget."""
    if budget == 0 or DEFAULT_MODEL not in _THINKING_MODELS:
        return anthropic.NOT_GIVEN
    return {"type": "enabled", "budget_tokens": budget}


def extract_text(response) -> str:
    """Extract text content, works with both thinking and non-thinking responses."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
