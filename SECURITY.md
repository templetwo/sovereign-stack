# Security Policy

## Reporting a Vulnerability

Email **templetwo@proton.me** with a description of the issue, steps to reproduce,
affected version(s), and any relevant log output or proof-of-concept code.

Please do not file a public GitHub issue before a fix has been coordinated.
Public disclosure before a patch is available puts all users at risk. Encrypted
email is welcome; request a public key at the same address.

We follow coordinated disclosure. Reporters are credited in the changelog unless
they prefer anonymity.

## Supported Versions

| Version | Supported       |
|---------|-----------------|
| 1.3.x   | Yes (current)   |
| 1.2.x   | No              |
| 1.1.x   | No              |
| < 1.1   | No              |

Only the latest 1.3.x release receives security patches. Users on older versions
should upgrade before reporting issues.

## Scope

**In scope** — vulnerabilities in:

- The `sovereign_stack` Python package (`src/sovereign_stack/`)
- CLI entry points (`sovereign`, `sovereign-sse`, and related tools)
- Daemon code (`nape_daemon.py`, modules under `src/sovereign_stack/daemons/`)
- The dispatcher and tool registration layer
- The MCP server transport (stdio and SSE modes)
- Guardian scan, quarantine, and audit logic
- Chronicle read/write paths and governance circuits

**Out of scope** — issues in:

- Third-party MCP servers used alongside this package
  (`@modelcontextprotocol/server-filesystem`, `@modelcontextprotocol/server-memory`)
  — report those to their respective maintainers
- User-managed infrastructure: Cloudflare Tunnel configuration, launchd plist
  deployment, reverse proxies, and similar operational concerns
- The user's bearer token storage or local secrets management practices
- Vulnerabilities in Claude Desktop, Claude Code, or any Anthropic product

If you are unsure whether something is in scope, report it anyway and we will triage it.

## Response Timeline

| Stage                                          | Target                        |
|------------------------------------------------|-------------------------------|
| Initial acknowledgment                         | Within 72 hours of receipt    |
| Triage and severity assessment                 | Within 5 business days        |
| Status update or coordinated fix               | Within 14 days (confirmed)    |
| CVE assignment                                 | For CVSS >= 7.0 severity      |

These are targets, not guarantees. Complex issues may require more time.
We will communicate delays promptly. If you receive no acknowledgment within
72 hours, follow up at the same address.

## Known Boundaries

The following behaviors are by design and noted here to prevent confusion with
security vulnerabilities:

**Bridge bearer token.** The bearer token that authorizes write access to the SSE
server is sourced from `~/.config/sovereign-bridge.env` by `comms_listener.sh`.
The codebase explicitly never logs this token to the chronicle or any audit trail.
If exposed, rotate it immediately and restart the SSE server. Mode `600` on the
env file is strongly recommended.

**Guardian quarantine is destructive.** `guardian_quarantine` moves files rather
than copying them. This is intentional to enforce hard isolation of flagged
content. The `release` action restores them. Do not invoke quarantine in an
automated loop without an explicit human approval gate.

**Local-only mode.** In stdio mode (the default Claude Desktop configuration) the
server is not network-accessible and the attack surface is limited to local
processes. The SSE server on port 3434 opens a network endpoint and should not
be bound to a public interface without a reverse proxy or tunnel that enforces
authentication.

**Data directory.** `~/.sovereign/` stores memory, governance audit trails, and
session handoffs in plaintext JSON. No encryption at rest is applied by this
package. Ensure the directory is not world-readable (`chmod 700 ~/.sovereign`).
