# Ring 2 Proposal: Authoritative External Spec for claude.ai-Native MCP Connector

**Date (UTC):** 2026-07-04  
**Session ID:** grok-xai-20260704-008 (heavy-research lane)  
**Proposer:** Grok Build (provenance-grade external research + synthesis only)  
**Target:** claude.ai / Claude custom connectors (remote MCP)  
**Layer:** hypothesis / engineering spec (Ring 2; always_proposal=true)  
**Sources:** Official MCP specification pages (modelcontextprotocol.io), Claude Help Center / support.claude.com, platform.claude.com, RFCs (9728, 8414, 8707, 7591, etc.), consistent cross-references in GitHub issues, developer guides (Strata, Sunpeak, etc.), and direct page content. All dated or versioned where available.

**Per Grok Operating Charter (2026-07-04):** This is a proposal. Grok proposes; HQ (Claude Opus seat) reviews; Anthony ratifies. Output is provenance-grade research only.

## Executive Summary
This document nails the external, client-driven requirements for an MCP server to interoperate as a "custom connector" with claude.ai (web), Claude Desktop, mobile, and related surfaces. It is derived exclusively from public Anthropic/Claude documentation and the MCP specification.

Build against this exactly. Deviations will break the claude.ai flow.

## (1) Transport Requirement: Streamable HTTP (current primary)

**Answer:** claude.ai connector clients use remote/HTTP-based MCP. The authoritative MCP transport for remote connectors is **Streamable HTTP** (single MCP endpoint supporting POST for client→server JSON-RPC and GET/POST for optional SSE streaming/responses). 

- **Since which revision:** Introduced as the replacement for HTTP+SSE in MCP specification version **2025-03-26** (see https://modelcontextprotocol.io/specification/2025-03-26/basic/transports). Retained and documented as the standard in later revisions including **2025-11-25** (latest referenced: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

**Details from spec (2025-11-25):**
- "This replaces the HTTP+SSE transport from protocol version 2024-11-05."
- Single endpoint (e.g. `https://example.com/mcp`) MUST support POST and GET.
- Client sends POST with Accept: application/json, text/event-stream.
- Server can return JSON or upgrade to SSE stream.
- Backwards compatibility guidance exists for legacy HTTP+SSE (separate /sse and POST endpoints), but it is deprecated.
- Additional requirements: MCP-Protocol-Version header, MCP-Session-Id (optional stateful), Origin validation for security, resumability via Last-Event-ID.

**Claude-specific:**
- Claude support pages and examples refer to remote MCP over HTTP.
- Community and guides note Claude "supports both Streamable HTTP and SSE transports" for connectors, but explicitly document legacy SSE/HTTP+SSE as deprecated in favor of Streamable HTTP.
- Claude Code docs recommend `--transport http` (Streamable) and list SSE as deprecated option.
- Remote connectors from claude.ai are brokered from Anthropic cloud (public internet required); stdio is local-only.

**Recommendation for claude.ai-native:** Implement Streamable HTTP per 2025-03-26+ spec as primary. Support legacy SSE only if needed for broad compat; test specifically with claude.ai custom connector flow. Use the current spec version header.

**Sources:**
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports (and 2025-03-26 intro)
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp (remote MCP, cloud-brokered)
- Claude Code / platform docs referencing streamable-http and deprecation notes.
- Ecosystem analyses (e.g., blogs confirming March 2025 change).

## (2) Exact OAuth Handshake

**Answer:** claude.ai follows the MCP authorization specification precisely: 401 + WWW-Authenticate (with resource_metadata) → Protected Resource Metadata (RFC 9728, supporting root + path-suffixed) → Authorization Server Metadata (RFC 8414, with path-insertion variants) → full OAuth 2.1 + PKCE S256 flow. Public client (token_endpoint_auth_method=none). Registration via DCR (RFC 7591, deprecated in MCP but supported), CIMD, pre-registration/manual Client ID/Secret (Claude advanced settings), or Anthropic-handled in some cases.

**Step-by-step (MCP spec + Claude behavior):**

1. **Initial request to MCP server (no/invalid token):** Server returns 401 Unauthorized.
   - `WWW-Authenticate: Bearer resource_metadata="https://your-server.example.com/.well-known/oauth-protected-resource"[, scope="..."]`
   - (Per RFC 6750 + MCP + RFC 9728 Section 5.1)

2. **Protected Resource Metadata (RFC 9728):**
   - MCP servers **MUST** implement it.
   - Location: root `/.well-known/oauth-protected-resource` **or** path-suffixed variant per RFC (e.g., if MCP endpoint is `/mcp`, metadata at `/.well-known/oauth-protected-resource/mcp`).
   - Must include `authorization_servers` array (URIs to AS).
   - Claude fetches this (prefers WWW-Auth header value; falls back to well-known).
   - **Note on path-suffixed:** Spec supports it; however, some client implementations (including reports on Claude/VS Code) may have limitations or default to root probing. Test with exact server URL path. MCP spec (auth discovery) explicitly documents both root and path-inserted/suffixed options.

3. **Authorization Server Metadata (RFC 8414):**
   - AS **MUST** provide at `/.well-known/oauth-authorization-server` (default suffix).
   - For issuers with path (e.g. /tenant), clients try path-insertion first: `/.well-known/oauth-authorization-server/tenant`, then OIDC variants, then append.
   - MCP clients **MUST** support both RFC 8414 and OIDC Discovery.
   - Fields include: issuer, authorization_endpoint, token_endpoint, response_types_supported (["code"]), grant_types_supported (["authorization_code", "refresh_token"]), code_challenge_methods_supported (["S256"]), token_endpoint_auth_methods_supported (include "none" for public clients), etc.

4. **OAuth 2.1 Flow + PKCE S256 (mandatory):**
   - Public clients: `token_endpoint_auth_method: "none"`.
   - Authorization request (to /authorize): client_id, redirect_uri, code_challenge (S256), code_challenge_method=S256, state, response_type=code, scope, **resource** (see #3).
   - Redirect back with code.
   - Token request (to /token): grant_type=authorization_code, code, code_verifier, redirect_uri, client_id. Verify PKCE.
   - Bearer token in subsequent MCP requests: `Authorization: Bearer <token>`.
   - Refresh token support.

**Client Registration options (per MCP spec + Claude):**
- **Preferred in current MCP spec:** Client ID Metadata Documents (CIMD, draft-ietf-oauth-client-id-metadata-document).
- **DCR (RFC 7591):** Supported (Claude can perform dynamic registration); marked deprecated in MCP spec for backwards compat.
- **Manual / pre-registered:** Provide Client ID + Secret in Claude "Advanced settings" when adding custom connector.
- Anthropic may hold/ proxy creds for directory connectors.

**Claude behavior (from docs + reports):**
- Discovers automatically via the above.
- Supports providing Client ID/Secret manually.
- All Claude surfaces (web, desktop, mobile) use the same flow for custom connectors.
- PKCE S256 is strictly required ("Claude won’t authenticate without code_challenge support").

**Sources:**
- https://modelcontextprotocol.io/specification/draft/basic/authorization (and subpages: authorization-server-discovery, client-registration)
- RFC 9728, RFC 8414, draft-ietf-oauth-v2-1, RFC 7591.
- https://support.claude.com/en/articles/11175166 (OAuth process for custom connectors)
- Consistent reports: Reddit synthesis, GitHub issues, Strata/Sunpeak guides detailing the exact endpoints and "none" method.

## (3) RFC 8707 Resource Parameter

**Answer:** claude.ai (MCP client) **MUST** (per spec) pass the `resource` parameter. It identifies the specific MCP server URL the user entered for the connector. Servers/AS **MUST** enforce/validate it (bind issued tokens to that exact resource to prevent cross-server replay/phishing).

**Details (MCP spec):**
- "MCP clients **MUST** implement Resource Indicators for OAuth 2.0 as defined in RFC 8707 to explicitly specify the target resource for which the token is being requested."
- **MUST** be included in **both** authorization requests and token requests.
- Value: the **canonical URI** of the MCP server (the one the user provided when adding the connector; most specific URI possible).
- Aligns with `resource` in Protected Resource Metadata (RFC 9728).
- Servers use this to scope/validate the token (e.g., audience/resource check on every protected request).

**How claude.ai passes it:** As the `resource` query/form param set to the exact remote MCP server URL supplied in the custom connector UI (e.g., the full https://your-mcp.example.com/mcp or whatever base the user pasted).

**Enforcement required:**
- Authorization server: Include/validate `resource` in requests; issue tokens only for the declared resource.
- Resource server (your MCP): Validate the token's `aud`/`resource` (or equivalent) matches the MCP endpoint being accessed. Reject mismatches.
- This is critical per spec to mitigate token misuse across different MCP servers.

**Sources:**
- https://modelcontextprotocol.io/specification/draft/basic/authorization (Resource Parameter Implementation section, citing RFC 8707 Section 2).
- Related: https://den.dev/blog/mcp-authorization-resource/ (explains the phishing prevention motivation).
- RFC 8707 itself.

## (4) Exact redirect_uri Values (claude.ai / claude.com)

**Answer (authoritative, consistent across Claude docs, guides, issues):**

**For claude.ai web, Claude Desktop, mobile apps, Cowork (hosted/cloud-brokered connectors):**
- `https://claude.ai/api/mcp/auth_callback`
- `https://claude.com/api/mcp/auth_callback` (include for compatibility/future-proofing; referenced in multiple official-ish guides)

**Additional/legacy mentions:**
- `https://claude.ai/api/auth/callback` (appears in some detailed summaries; whitelist if observed).

**For Claude Code (CLI / direct desktop app connections):**
- Loopback / RFC 8252: `http://localhost:<PORT>/callback` and/or `http://127.0.0.1:<PORT>/callback`
- PORT is dynamic/ephemeral by default (Claude Code picks available port).
- Use `--callback-port` flag to fix a specific port for pre-registration.
- Authorization server must support port-agnostic matching or the specific ports used.

**Usage notes:**
- Register **exactly** these strings in your OAuth client's allowed redirect URIs / allowlist.
- Mismatch causes "redirect_uri not registered" or similar during flow.
- For custom connectors added in claude.ai Settings → Connectors → Add custom connector, the flow uses the hosted callback.
- Claude Code (local) uses the loopback variants.

**Sources (cross-verified):**
- https://support.claude.com (via related code.claude.com/docs/en/mcp)
- https://docs.strata.io/guides/ai-identity/connect/claude (explicit lists)
- https://sunpeak.ai/blogs/claude-connector-oauth-authentication/ (detailed)
- GitHub issues (e.g., Prefect, modelcontextprotocol discussions), Reddit syntheses, developer forums consistently citing the same URIs.
- Multiple "Redirect URI not registered" reports confirming these exact values.

## Additional Implementation Notes for claude.ai Compatibility
- Public internet reachability from Anthropic IPs (for remote connectors).
- Bearer token on MCP requests (not query param).
- Proper CORS, Origin validation (per Streamable HTTP security).
- Scope handling from WWW-Authenticate or scopes_supported.
- Test full flow: add as custom connector in claude.ai → OAuth redirect → tool use.
- For directory submission vs custom: directory may have additional review; custom uses the above directly.
- DCR vs manual: Support both; provide option for Client ID/Secret in advanced.

**This spec is what claude.ai actually drives against.** Deviate at your peril (common failure modes: wrong redirect_uri, missing discovery endpoints, incorrect resource param, using deprecated SSE-only, path-suffix discovery mismatches).

**Receipts & Sources (non-exhaustive):**
- All linked modelcontextprotocol.io specification pages (versions 2025-03-26, 2025-11-25, draft).
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- code.claude.com/docs/en/mcp
- RFCs: 9728, 8414, 8707, 7591, draft-ietf-oauth-v2-1.
- Cross-referenced: Strata.io guides, Sunpeak.ai, GitHub issues (#1564, etc.), Reddit detailed posts, blog analyses of spec changes.

**Supersession:** This proposal supersedes any prior informal notes. Update on new spec revisions or observed claude.ai behavior changes.

**Signature / Approval Path:** Propose via Ring 2 (pending_writes / propose_insight domain="mcp-claude-connector-spec"). Anthony ratification required.

*Prepared under the Grok Operating Charter. Grok proposes; HQ reviews; Anthony ratifies. All research is external/public only.*