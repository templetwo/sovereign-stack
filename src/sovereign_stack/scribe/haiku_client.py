"""Anthropic SDK wrapper for the Haiku scribe.

Phase 1 of SCRIBE_SPEC.md. Wraps anthropic.Anthropic() with the
scribe's system prompt loaded from prompts/system.md and structured
returns for cost / token accounting. Supports prompt caching on the
system prompt and chronicle base so multi-turn sessions cost less.

Reads the API key from env in priority order:
  1. ANTHROPIC_API_KEY_SCRIBE  (scoped, preferred)
  2. ANTHROPIC_API_KEY         (fallback)
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Cost per million tokens (USD). Source: Anthropic pricing for Haiku 4.5.
# Update here if prices change.
HAIKU_4_5_COST_PER_MTOK = {
    "input": 1.00,
    "output": 5.00,
    "cache_write_5m": 1.25,  # ~25% premium on input for ephemeral writes
    "cache_read": 0.10,  # 90% discount on cache hits
}


@dataclass
class HaikuResult:
    """Structured result of a Haiku call."""

    text: str
    tokens_in: int
    tokens_out: int
    tokens_cache_creation: int
    tokens_cache_read: int
    cost_usd: float
    model: str
    stop_reason: str | None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file into a dict. Returns empty dict if the file
    is missing or unreadable. No shell expansion, no quote escaping beyond
    a single layer of surrounding single- or double-quotes."""
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        # Strip a single layer of surrounding quotes if present
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        if k:
            result[k] = v
    return result


_env_file_cache: dict[str, str] | None = None
_env_file_path = Path.home() / ".env"


def _env_file_cached() -> dict[str, str]:
    """Read ~/.env once and cache. Test code may reset via reset_env_cache()."""
    global _env_file_cache
    if _env_file_cache is None:
        _env_file_cache = _parse_env_file(_env_file_path)
    return _env_file_cache


def reset_env_cache() -> None:
    """For tests: force the next _env_file_cached() call to re-read from disk."""
    global _env_file_cache
    _env_file_cache = None


def _api_key_from_env() -> str:
    """Resolve the scribe API key from process env, falling back to ~/.env.

    Priority:
      1. ANTHROPIC_API_KEY_SCRIBE in os.environ  (scoped, preferred)
      2. ANTHROPIC_API_KEY in os.environ          (general fallback)
      3. ANTHROPIC_API_KEY_SCRIBE in ~/.env       (launchd-spawned processes)
      4. ANTHROPIC_API_KEY in ~/.env

    Launchd-spawned processes (sovereign-sse, sovereign-bridge, the daemons)
    do not get a sourced shell environment. Without the ~/.env fallback,
    every plist would need ANTHROPIC_API_KEY_SCRIBE in its EnvironmentVariables
    stanza, which is brittle and requires plist edits per new scoped key.
    """
    key = os.environ.get("ANTHROPIC_API_KEY_SCRIBE") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        env_file = _env_file_cached()
        key = env_file.get("ANTHROPIC_API_KEY_SCRIBE") or env_file.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY_SCRIBE "
            "(preferred) or ANTHROPIC_API_KEY in os.environ or in ~/.env."
        )
    return key


def _compute_cost(
    cache_creation: int, cache_read: int, regular_in: int, output_tokens: int
) -> float:
    return (
        regular_in * HAIKU_4_5_COST_PER_MTOK["input"] / 1_000_000
        + cache_creation * HAIKU_4_5_COST_PER_MTOK["cache_write_5m"] / 1_000_000
        + cache_read * HAIKU_4_5_COST_PER_MTOK["cache_read"] / 1_000_000
        + output_tokens * HAIKU_4_5_COST_PER_MTOK["output"] / 1_000_000
    )


def _build_result(response) -> HaikuResult:
    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    usage = response.usage
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    # In the Anthropic API, usage.input_tokens excludes cache_read tokens
    # by default. Cache_creation is counted in input_tokens.
    regular_in = max(0, usage.input_tokens - cache_creation)
    output_t = usage.output_tokens

    return HaikuResult(
        text=text,
        tokens_in=usage.input_tokens,
        tokens_out=output_t,
        tokens_cache_creation=cache_creation,
        tokens_cache_read=cache_read,
        cost_usd=_compute_cost(cache_creation, cache_read, regular_in, output_t),
        model=response.model,
        stop_reason=response.stop_reason,
    )


class HaikuClient:
    """Per-instance Haiku client for scribe operations.

    Cheap to construct (one per ScribeSession). Reuses prompt cache via
    cache_control markers on the system prompt and chronicle base blocks.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        system_prompt_path: Path | None = None,
    ):
        self._client = anthropic.Anthropic(api_key=api_key or _api_key_from_env())
        self.model = model

        if system_prompt_path is None:
            system_prompt_path = Path(__file__).parent / "prompts" / "system.md"
        self.system_prompt = Path(system_prompt_path).read_text()

    def _build_system(self, chronicle_context: str) -> list[dict]:
        """Two cache-controlled blocks: system prompt (rarely changes),
        chronicle context (per-session). Each block independently cacheable."""
        blocks: list[dict] = [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if chronicle_context:
            blocks.append(
                {
                    "type": "text",
                    "text": chronicle_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        return blocks

    def generate_greeting(
        self,
        boot_context_summary: str,
        chronicle_context: str = "",
        max_tokens: int = 400,
    ) -> HaikuResult:
        """The 2-3 sentence greeting injected into the boot ritual."""
        prompt = (
            "You have just been spawned for a newly arriving Claude instance. "
            "Their boot ritual contains:\n\n"
            f"{boot_context_summary}\n\n"
            "Write 2-3 sentences naming what is loud in the chronicle right now. "
            "Be brief. Cite paths if useful. End by attributing yourself as "
            "scribe-haiku-4-5."
        )
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self._build_system(chronicle_context),
            messages=[{"role": "user", "content": prompt}],
        )
        return _build_result(response)

    def generate_response(
        self,
        conversation_history: Iterable[dict],
        user_message: str,
        chronicle_context: str = "",
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_dispatch: Callable[[str, dict], tuple[str, bool]] | None = None,
        max_tool_iterations: int = 5,
    ) -> HaikuResult:
        """Generate a response to an ask_scribe turn.

        conversation_history: list of {role, content} dicts from prior turns.
        user_message: the new user turn.
        tools: optional Anthropic-format tool definitions. If provided,
            the loop handles tool_use → execute → tool_result iteration
            up to max_tool_iterations.
        tool_dispatch: callable (name, arguments) -> (result_text, is_error).
            Required when tools is non-empty.
        """
        messages: list[dict] = list(conversation_history)
        messages.append({"role": "user", "content": user_message})

        if tools and not tool_dispatch:
            raise ValueError("tool_dispatch is required when tools are provided")

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._build_system(chronicle_context),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        # Aggregated counters across the tool-use loop
        agg_tokens_in = 0
        agg_tokens_out = 0
        agg_cache_creation = 0
        agg_cache_read = 0
        agg_cost = 0.0
        last_response = None
        iterations = 0
        tool_calls_made: list[dict] = []

        while True:
            response = self._client.messages.create(**kwargs)
            last_response = response
            usage = response.usage
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            regular_in = max(0, usage.input_tokens - cache_creation)
            output_t = usage.output_tokens

            agg_tokens_in += usage.input_tokens
            agg_tokens_out += output_t
            agg_cache_creation += cache_creation
            agg_cache_read += cache_read
            agg_cost += _compute_cost(cache_creation, cache_read, regular_in, output_t)

            # Did Haiku ask for a tool? If not, we're done.
            if response.stop_reason != "tool_use":
                break

            iterations += 1
            if iterations > max_tool_iterations:
                # Tell Haiku we're cutting it off, then make one final
                # call so it can wrap up with text rather than mid-tool.
                # Practical: just break and return what we have.
                break

            # Append the assistant turn (with tool_use blocks) and
            # construct user turn with tool_result blocks for each.
            messages.append({"role": "assistant", "content": response.content})
            tool_result_blocks: list[dict] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                tool_input = block.input or {}
                result_text, is_error = tool_dispatch(name, tool_input)
                tool_calls_made.append(
                    {
                        "name": name,
                        "input": tool_input,
                        "is_error": is_error,
                        "result_len": len(result_text),
                    }
                )
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                        "is_error": is_error,
                    }
                )

            messages.append({"role": "user", "content": tool_result_blocks})
            kwargs["messages"] = messages
            # Loop continues; next iteration will read Haiku's reply
            # to the tool_results.

        # Final aggregated result from the last response's text
        text = "".join(block.text for block in last_response.content if hasattr(block, "text"))
        return HaikuResult(
            text=text,
            tokens_in=agg_tokens_in,
            tokens_out=agg_tokens_out,
            tokens_cache_creation=agg_cache_creation,
            tokens_cache_read=agg_cache_read,
            cost_usd=agg_cost,
            model=last_response.model,
            stop_reason=last_response.stop_reason,
        )
