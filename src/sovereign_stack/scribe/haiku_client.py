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
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

# Model is env-overridable so the scribe can be promoted without a code edit.
# Default is Sonnet: the scribe is the librarian for the whole chronicle now,
# and it needs the reasoning to lead a fruitful search, not just greet.
DEFAULT_MODEL = os.environ.get("SCRIBE_MODEL", "claude-sonnet-4-6")

# Token ceilings are caps, not targets — the model stops when it is done, so a
# high ceiling never slows a short answer; it only removes the leash when a
# question deserves a full, cited answer. Both are env-overridable.
#   SCRIBE_MAX_TOKENS          — ask_scribe / conversational answers (the librarian)
#   SCRIBE_GREETING_MAX_TOKENS — the 2-3 sentence boot greeting (stays brief by prompt)
GREETING_MAX_TOKENS = int(os.environ.get("SCRIBE_GREETING_MAX_TOKENS", "1500"))
ANSWER_MAX_TOKENS = int(os.environ.get("SCRIBE_MAX_TOKENS", "32000"))

# Cost per million tokens (USD), per model family. The scribe reports spend in
# its stats footer, so the table must match the model that actually served the
# call — selected by response.model at accounting time. Update if prices change.
HAIKU_4_5_COST_PER_MTOK = {
    "input": 1.00,
    "output": 5.00,
    "cache_write_5m": 1.25,  # ~25% premium on input for ephemeral writes
    "cache_read": 0.10,  # 90% discount on cache hits
}
SONNET_4_X_COST_PER_MTOK = {
    "input": 3.00,
    "output": 15.00,
    "cache_write_5m": 3.75,  # ~25% premium on input for ephemeral writes
    "cache_read": 0.30,  # 90% discount on cache hits
}


def _cost_table_for(model: str) -> dict:
    """Pick the pricing table by model family. Any non-Haiku model prices as
    Sonnet, so a promoted scribe never silently under-reports its spend."""
    if "haiku" in (model or "").lower():
        return HAIKU_4_5_COST_PER_MTOK
    return SONNET_4_X_COST_PER_MTOK


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
    # List of tool call records from the tool-use loop (Lesson #582 instrument).
    # Each dict: {name, input, is_error, result_len}. Empty for greeting calls.
    tool_calls_made: list[dict] = field(default_factory=list)


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
    model: str, cache_creation: int, cache_read: int, regular_in: int, output_tokens: int
) -> float:
    cost = _cost_table_for(model)
    return (
        regular_in * cost["input"] / 1_000_000
        + cache_creation * cost["cache_write_5m"] / 1_000_000
        + cache_read * cost["cache_read"] / 1_000_000
        + output_tokens * cost["output"] / 1_000_000
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
        cost_usd=_compute_cost(response.model, cache_creation, cache_read, regular_in, output_t),
        model=response.model,
        stop_reason=response.stop_reason,
        tool_calls_made=[],
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

    def _build_system(self, chronicle_context: str, greeting_mode: bool = False) -> list[dict]:
        """Two cache-controlled blocks: system prompt (rarely changes),
        chronicle context (per-session). Each block independently cacheable.

        When greeting_mode=True, a short override block is prepended that
        tells the model to answer from context only, with no tools and no JSON.
        This prevents Sonnet from emitting tool-call XML (or Fork-D JSON) in
        the greeting even though it shares the same system.md as ask_scribe.
        """
        if greeting_mode:
            # Prepend a short override block that suppresses tool calls and JSON
            # format BEFORE the main system prompt, so the no-paths rule and
            # identity sections from system.md are still active.
            override_text = (
                "GREETING MODE OVERRIDE: You are writing a short 2-3 sentence boot greeting. "
                "Answer from your context only. "
                "You have NO tools in this call — do NOT emit any tool calls, "
                "tool-use XML, or JSON objects. "
                "Respond with plain prose only. "
                "Do NOT use the ask_scribe JSON response format in this mode. "
                "Ignore any 'USE YOUR TOOLS' instruction below — it does not apply "
                "to greeting mode."
            )
            blocks: list[dict] = [
                {
                    "type": "text",
                    "text": override_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        else:
            blocks = [
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
        max_tokens: int = GREETING_MAX_TOKENS,
    ) -> HaikuResult:
        """The 2-3 sentence greeting injected into the boot ritual.

        Uses greeting_mode=True to suppress the USE-YOUR-TOOLS directive and
        the ask_scribe JSON format, and passes NO tools to the API call.
        This prevents Sonnet from emitting tool-call XML as prose.
        """
        prompt = (
            "You have just been spawned for a newly arriving Claude instance. "
            "Their boot ritual contains:\n\n"
            f"{boot_context_summary}\n\n"
            "Write 2-3 sentences naming what is loud in the chronicle right now. "
            "Be brief. Cite paths if useful. End by attributing yourself as "
            "scribe-sonnet-4-6."
        )
        # Explicitly do NOT pass tools= — greeting never dispatches tools.
        # Stream: a high max_tokens (Sonnet, SCRIBE_MAX_TOKENS) can exceed the
        # SDK's 10-min non-streaming guard, so we stream and take the final
        # message. get_final_message() returns the same Message create() would.
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=self._build_system(chronicle_context, greeting_mode=True),
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
        return _build_result(response)

    def generate_response(
        self,
        conversation_history: Iterable[dict],
        user_message: str,
        chronicle_context: str = "",
        max_tokens: int = ANSWER_MAX_TOKENS,
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
            # Stream rather than create(): SCRIBE_MAX_TOKENS is high enough to
            # trip the SDK's non-streaming 10-min guard. get_final_message()
            # returns the same Message (content, usage, stop_reason) create did,
            # so the tool-use loop and cost accounting below are unchanged.
            with self._client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
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
            agg_cost += _compute_cost(
                response.model, cache_creation, cache_read, regular_in, output_t
            )

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
            tool_calls_made=tool_calls_made,
        )
