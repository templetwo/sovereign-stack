"""
Policies Module — Registry-Backed Standing Policies

v1.7.0 "Receipts & Seasons": policies live in an append-only registry
(~/.sovereign/policies/policies.jsonl), never derived from chronicle
intensity — a 0.9 sentinel is not a policy until a human enacts it.

Two surfaces:
- set_policy — the ONLY writer. Human-gated: ``set_by`` is required and
  names the approving human. Amend by passing an existing policy_id
  (version n+1 appended); retire via status="retired". NO chronicle echo
  (the registry is the single source of truth; discoverability comes from
  the boot one-liner).
- current_policies — reads ONLY policies.jsonl, folds latest record per
  policy_id, and ends with a self-describing footer (source-of-truth
  path, counts, how to enact).

Pure data → formatted text, witness.py-style. Import has zero side
effects: the registry file and its directory are created lazily on the
first write, never on read.

Integration notes (server.py owner):
- TOOLS list: ``+ POLICY_TOOLS`` (same concat pattern as METABOLISM_TOOLS).
- Dispatch: ``handle_policy_tool(name, arguments, registry)`` returns
  display text; wrap in TextContent (same contract as
  handle_compaction_memory_tool).
- my_toolkit registry: merge POLICY_TOOL_TIERS / POLICY_TOOL_INTENTS into
  TOOL_TIERS / TOOL_INTENTS; category for both tools is "policies".
- Boot one-liner: ``PolicyRegistry().boot_line()`` — returns None when
  the registry is empty (data-gated: zero records, zero boot change).
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import Tool

# ── Constants ──

DEFAULT_POLICIES_PATH = Path.home() / ".sovereign" / "policies" / "policies.jsonl"

VALID_STATUSES = ("active", "retired")

# The source_refs grammar (spec section 3): [{type, ref}], nothing else.
VALID_SOURCE_REF_TYPES = ("claim", "archive", "letter", "doc", "human")
_SOURCE_REF_KEYS = {"type", "ref"}

# Empty-registry honesty — verbatim from the v1.7.0 spec (section 2).
EMPTY_REGISTRY_LINE = "no policies registered yet; run season_review for candidates."

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 40


# ── Pure helpers ──


def _slugify(statement: str) -> str:
    """Policy-id slug from a statement: lowercase, hyphen-joined, <=40 chars."""
    slug = _SLUG_RE.sub("-", statement.lower()).strip("-")[:_SLUG_MAX_LEN].rstrip("-")
    if not slug:
        # Statement was all punctuation/whitespace-adjacent — fall back to a
        # content hash so the id is still deterministic and non-empty.
        slug = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:8]
    return slug


def _domain_matches(wanted: str, domain_field: str) -> bool:
    """
    Comma-tag ELEMENT match — the memory.py get_open_threads convention
    (memory.py:664): domain="openai-bridge" matches
    "openai-bridge,cross-system-inquiry" but is never a substring match
    inside another tag.
    """
    return wanted in [tag.strip() for tag in (domain_field or "").split(",")]


def _validate_source_refs(source_refs: list[dict] | None) -> list[dict]:
    """
    Validate source_refs against the spec grammar: [{type, ref}] with
    type ∈ claim|archive|letter|doc|human and ref a non-empty string.

    Fail-closed: any malformed entry rejects the whole call, and the
    error names the offending entry (the receipts philosophy — a ref
    pointing at nothing, or shaped like nothing, is unrecordable).
    """
    if source_refs is None:
        return []
    if not isinstance(source_refs, list):
        raise ValueError("source_refs must be a list of {type, ref} objects")
    validated: list[dict] = []
    for i, ref in enumerate(source_refs):
        label = f"source_refs[{i}]"
        if not isinstance(ref, dict):
            raise ValueError(
                f"{label} must be an object with 'type' and 'ref', got {type(ref).__name__}"
            )
        extra = set(ref) - _SOURCE_REF_KEYS
        if extra:
            raise ValueError(
                f"{label} has unknown key(s) {sorted(extra)} — the grammar is {{type, ref}}"
            )
        ref_type = ref.get("type")
        if ref_type not in VALID_SOURCE_REF_TYPES:
            raise ValueError(
                f"{label} has unknown type {ref_type!r} — "
                f"must be one of: {', '.join(VALID_SOURCE_REF_TYPES)}"
            )
        target = ref.get("ref")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"{label} (type={ref_type}) requires a non-empty string 'ref'")
        validated.append({"type": ref_type, "ref": target})
    return validated


def _format_policy(record: dict) -> list[str]:
    """Render one folded policy record as display lines."""
    marker = "" if record.get("status") == "active" else " (retired)"
    lines = [
        f"[{record.get('policy_id', '?')}] v{record.get('version', 1)}{marker}"
        f" — domain: {record.get('domain', '')}"
    ]
    lines.append(f"  {record.get('statement', '')}")
    meta = f"  set by {record.get('set_by', '?')} · {record.get('timestamp', '')[:10]}"
    refs = record.get("source_refs") or []
    if refs:
        meta += " · sources: " + ", ".join(f"{r.get('type')}:{r.get('ref')}" for r in refs)
    lines.append(meta)
    return lines


# ── Registry ──


class PolicyRegistry:
    """
    Append-only policy registry over policies.jsonl.

    The file is the ledger; current state is a fold (latest record per
    policy_id wins, in file order — append-only means last line is
    truth). Nothing is ever rewritten or deleted: amendments and
    retirements are new records.
    """

    def __init__(self, policies_path: str | Path = DEFAULT_POLICIES_PATH):
        self.policies_path = Path(policies_path)

    # ── Read path ──

    def load_records(self) -> list[dict]:
        """All ledger records in file (append) order. Corrupt lines skipped."""
        if not self.policies_path.exists():
            return []
        records: list[dict] = []
        with open(self.policies_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("policy_id"):
                    records.append(record)
        return records

    def fold(self) -> dict[str, dict]:
        """Current state: latest record per policy_id (file order wins)."""
        folded: dict[str, dict] = {}
        for record in self.load_records():
            folded[record["policy_id"]] = record
        return folded

    def boot_line(self) -> str | None:
        """
        The boot one-liner for where_did_i_leave_off / arrive.

        Returns None when the registry is empty — the data-gated rule:
        zero records, zero change to v1.6.2 boot output.
        """
        folded = self.fold()
        if not folded:
            return None
        active = sum(1 for r in folded.values() if r.get("status") == "active")
        return f"Standing policies: {active} active — current_policies()"

    def current_policies(self, domain: str | None = None, include_retired: bool = False) -> str:
        """
        Formatted view of standing policies. Reads ONLY policies.jsonl.

        Args:
            domain: Comma-tag ELEMENT filter (memory.py convention) —
                "style" matches a policy tagged "writing,style" but never
                as a substring inside another tag.
            include_retired: Show retired policies too (marked). When
                False, the footer states how many were held back and the
                call that reveals them — filters print what they hide.

        Returns:
            Display text ending with a self-describing footer: the
            source-of-truth path, active/retired counts, and how to enact
            (set_policy, human-gated).
        """
        folded_all = list(self.fold().values())
        if domain:
            scoped = [r for r in folded_all if _domain_matches(domain, r.get("domain", ""))]
        else:
            scoped = list(folded_all)

        active = sorted(
            (r for r in scoped if r.get("status") == "active"),
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )
        retired = sorted(
            (r for r in scoped if r.get("status") != "active"),
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )
        shown = active + (retired if include_retired else [])

        lines: list[str] = []
        if not folded_all:
            lines.append(f"📜 {EMPTY_REGISTRY_LINE}")
        elif domain and not scoped:
            lines.append(
                f'📜 No policies match domain "{domain}" — {len(folded_all)} registered; '
                "current_policies() shows all."
            )
        else:
            scope = f' (domain="{domain}")' if domain else ""
            lines.append(f"📜 Standing policies{scope} — {len(active)} active")
            for record in shown:
                lines.append("")
                lines.extend(_format_policy(record))

        held_back = 0 if include_retired else len(retired)
        if held_back:
            retired_clause = (
                f"{held_back} retired held back — "
                "current_policies(include_retired=true) shows them."
            )
        elif include_retired and retired:
            retired_clause = f"{len(retired)} retired shown."
        else:
            retired_clause = "0 retired."

        lines.append("")
        lines.append("---")
        lines.append(
            f"Source of truth: {self.policies_path} "
            "(append-only; latest record per policy_id wins)."
        )
        lines.append(f"{len(active)} active · {retired_clause}")
        lines.append(
            "To enact, amend, or retire: set_policy(statement, domain, set_by=<human>) — "
            "human-gated; set_by names the approving human."
        )
        return "\n".join(lines)

    # ── Write path ──

    def set_policy(
        self,
        statement: str,
        domain: str,
        set_by: str,
        source_refs: list[dict] | None = None,
        policy_id: str | None = None,
        status: str = "active",
        by: str = "",
    ) -> dict:
        """
        Register, amend, or retire a policy. The only writer.

        Args:
            statement: The policy text. Required for new policies; may be
                "" when amending/retiring an existing policy_id (the
                previous statement carries forward).
            domain: Comma-tag domain(s), e.g. "writing,style". Same
                carry-forward rule as statement.
            set_by: REQUIRED, non-empty — the human who approved this
                policy. This is the procedural human gate; there is no
                automated path into the registry.
            source_refs: Receipts: [{type, ref}] with type ∈
                claim|archive|letter|doc|human. Malformed entries reject
                the whole call (fail-closed, offender named).
            policy_id: Existing id to amend/retire (version n+1
                appended). None registers a new policy with a generated
                id pol_YYYYMMDD_<slug> (slug from statement,
                collision-suffixed).
            status: "active" (default) or "retired". Retiring requires
                policy_id; retiring an already-retired policy is an
                error. A later status="active" record un-retires.
            by: Optional recording-instance id (NOT a session_id — the
                attribution-mush rule). The human approver goes in
                set_by; the seat that typed the call goes here.

        Returns:
            The appended ledger record (includes the policy_id).

        Raises:
            ValueError: Missing set_by, bad status, malformed
                source_refs, unknown policy_id, retire-without-id, or
                double-retire.
        """
        if not isinstance(set_by, str) or not set_by.strip():
            raise ValueError(
                "set_by is required — policies are human-gated; name the human who approved this"
            )
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
        refs = _validate_source_refs(source_refs)
        statement = (statement or "").strip()
        domain = (domain or "").strip()
        policy_id = (policy_id or "").strip() or None

        folded = self.fold()
        if policy_id is not None:
            previous = folded.get(policy_id)
            if previous is None:
                raise ValueError(
                    f"unknown policy_id {policy_id!r} — omit policy_id to register a new policy"
                )
            if status == "retired" and previous.get("status") == "retired":
                raise ValueError(f"{policy_id} is already retired")
            # Carry-forward: empty statement/domain inherit from the
            # latest record, so a retire never forces re-typing the text.
            statement = statement or previous.get("statement", "")
            domain = domain or previous.get("domain", "")
            version = int(previous.get("version", 1)) + 1
        else:
            if status == "retired":
                raise ValueError(
                    "retiring requires policy_id — cannot retire a policy that was never registered"
                )
            if not statement:
                raise ValueError("statement is required when registering a new policy")
            if not domain:
                raise ValueError("domain is required when registering a new policy")
            policy_id = self._generate_policy_id(statement, existing=set(folded))
            version = 1

        record = {
            "policy_id": policy_id,
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "statement": statement,
            "domain": domain,
            "status": status,
            "set_by": set_by.strip(),
            "by": by,
            "source_refs": refs,
        }
        self._append(record)
        return record

    def _generate_policy_id(self, statement: str, existing: set[str]) -> str:
        """pol_YYYYMMDD_<slug>, suffixed -2, -3, ... on collision."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        base = f"pol_{stamp}_{_slugify(statement)}"
        policy_id = base
        suffix = 2
        while policy_id in existing:
            policy_id = f"{base}-{suffix}"
            suffix += 1
        return policy_id

    def _append(self, record: dict) -> None:
        """Append one ledger record. Directory created lazily, here only."""
        self.policies_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.policies_path, "a") as f:
            f.write(json.dumps(record) + "\n")


# ── MCP tool definitions ──

POLICY_TOOLS = [
    Tool(
        name="current_policies",
        description=(
            "Standing policies — registry-backed, never derived from chronicle intensity. "
            "Reads ONLY ~/.sovereign/policies/policies.jsonl (append-only; latest record per "
            "policy_id wins). Retired policies are held back by default and the footer says "
            "how many and how to see them. Empty registry answers honestly. Every response "
            "ends with a self-describing footer: source-of-truth path, counts, and how to "
            "enact (set_policy, human-gated)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Comma-tag ELEMENT filter — 'style' matches a policy tagged "
                        "'writing,style', never as a substring inside another tag."
                    ),
                },
                "include_retired": {
                    "type": "boolean",
                    "default": False,
                    "description": "Show retired policies too (marked '(retired)').",
                },
            },
        },
    ),
    Tool(
        name="set_policy",
        description=(
            "Register, amend, or retire a standing policy in the append-only registry "
            "(~/.sovereign/policies/policies.jsonl). HUMAN-GATED: set_by is required and "
            "names the approving human — there is no automated path into the registry. "
            "New policy: omit policy_id, get pol_YYYYMMDD_<slug>. Amend: pass the existing "
            "policy_id (version n+1 appended; empty statement/domain carry forward). "
            "Retire: existing policy_id + status='retired'. source_refs carry the receipts "
            "({type: claim|archive|letter|doc|human, ref}); malformed refs reject the whole "
            "call. NO chronicle echo — the registry is the single source of truth."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "statement": {
                    "type": "string",
                    "description": (
                        "The policy text. Required for new policies; may be '' when "
                        "amending/retiring (previous statement carries forward)."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Comma-tag domain(s), e.g. 'writing,style'. Required for new "
                        "policies; '' carries forward on amend/retire."
                    ),
                },
                "set_by": {
                    "type": "string",
                    "description": (
                        "REQUIRED — the human who approved this policy (e.g. 'anthony'). "
                        "The procedural human gate."
                    ),
                },
                "source_refs": {
                    "type": "array",
                    "description": (
                        "Receipts for the policy. Strict grammar: [{type, ref}] with type "
                        "∈ claim|archive|letter|doc|human. Any malformed entry rejects the "
                        "whole call, naming the offender."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["claim", "archive", "letter", "doc", "human"],
                                "description": "What kind of source the ref points at.",
                            },
                            "ref": {
                                "type": "string",
                                "description": (
                                    "The reference: claim id, archive id, letter path, "
                                    "doc path, or human name."
                                ),
                            },
                        },
                        "required": ["type", "ref"],
                    },
                },
                "policy_id": {
                    "type": "string",
                    "description": (
                        "Existing policy_id to amend or retire. Omit to register a new "
                        "policy. Unknown ids are rejected (fail-closed)."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "retired"],
                    "default": "active",
                    "description": (
                        "'active' registers/amends; 'retired' retires (requires "
                        "policy_id). A later 'active' record un-retires."
                    ),
                },
                "by": {
                    "type": "string",
                    "description": (
                        "Optional recording-instance id for audit (e.g. "
                        "'claude-fable-5-hq'). NOT a session_id. The approving human "
                        "goes in set_by."
                    ),
                },
            },
            "required": ["statement", "domain", "set_by"],
        },
    ),
]

# my_toolkit registry entries (integrator: merge into server.py's
# TOOL_TIERS / TOOL_INTENTS; category "policies" in TOOL_CATEGORIES).
POLICY_TOOL_TIERS: dict[str, str] = {
    "current_policies": "essential",
    "set_policy": "advanced",  # human-gated by design; current_policies footers the breadcrumb
}
POLICY_TOOL_INTENTS: dict[str, str] = {
    "current_policies": "orient",
    "set_policy": "govern",
}


# ── MCP dispatcher ──


def handle_policy_tool(name: str, arguments: dict, registry: PolicyRegistry) -> str:
    """
    Dispatch a policy tool call. Returns display text — the server wraps
    it in TextContent (same contract as handle_compaction_memory_tool).

    set_policy validation failures come back as rejection text, not
    exceptions, so the MCP surface never throws at the caller.
    """
    arguments = arguments or {}

    if name == "current_policies":
        return registry.current_policies(
            domain=(arguments.get("domain") or "").strip() or None,
            include_retired=bool(arguments.get("include_retired", False)),
        )

    if name == "set_policy":
        try:
            record = registry.set_policy(
                statement=arguments.get("statement", ""),
                domain=arguments.get("domain", ""),
                set_by=arguments.get("set_by", ""),
                source_refs=arguments.get("source_refs"),
                policy_id=arguments.get("policy_id"),
                status=arguments.get("status", "active"),
                by=arguments.get("by", ""),
            )
        except ValueError as exc:
            return f"⚠️ set_policy rejected: {exc}"
        if record["status"] == "retired":
            verb = "retired"
        elif record["version"] == 1:
            verb = "registered"
        else:
            verb = "amended"
        return (
            f"📜 Policy {verb}: {record['policy_id']} v{record['version']}\n"
            f"  {record['statement']}\n"
            f"  domain: {record['domain']} · status: {record['status']}"
            f" · set by {record['set_by']}\n"
            f"  → {registry.policies_path}"
        )

    return f"Unknown policy tool: {name}"
