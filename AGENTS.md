# Repository instructions

- Never run `auth login`, connect a real Roborock gateway, or invoke a `live` test unless the user explicitly starts the HITL phase.
- Keep stdout protocol-clean when the MCP server is running; diagnostics belong on stderr and must be redacted.
- Do not add a generic raw command tool.
- Do not retry a write after dispatch begins. Return `OUTCOME_UNCERTAIN` and a reconciliation tool.
- Never commit account data, DUIDs, local keys, maps, images, SDP/ICE/TURN material, PINs, or raw captures.
- Keep the public MCP surface at exactly 25 tools for 0.1.0. Update schema snapshots when intentionally changing it.
- Use `apply_patch` for edits, `uv run ruff`, `uv run mypy`, and `uv run pytest` before handoff.
