# Changelog

## 0.1.0 — stationary map repair

- Add native map geometry, MCP image output, and read-only repair previews.
- Bind revisions to device, active map, room assignments and restrictions.
- Require explicit device/map identity and default room edits to dry runs.
- Block other writes in stationary repair mode and prevent map-write transport retries.
- Disable guessed arbitrary room-renaming and boundary-write payloads; document app-assisted recovery.
- Add synthetic repair and transport regression tests plus the full 25-tool schema snapshot.
- Supervised split/merge room boundaries and restored names accepted on a97
  firmware 02.39.60; native backup created in the official app.
- Restore existing cloud names with explicit room-type reset consent, and native
  room order with a fresh revision and expected previous order. Native order was
  acknowledged and read back; unknown naming acknowledgements require reconciliation.
- Require final app save verification: the human saved the order and backed up
  the completed map after native order read-back. No automatic app-save claim.
- Extend the single-dispatch transport to other RPC writes; uncertain operations
  never fall through to another transport or continue a settings batch.
- No cleaning or movement acceptance. Native backup creation and restore remain
  app operations; no permanent protection against future drift is claimed.

## 0.1.0.dev0

- Private code-complete candidate with exactly 25 semantic MCP tools.
- OTP/keyring authentication, semantic gateway, fake gateway, media sidecar
  supervisor, Codex approval example and guarded HITL workflow.
- No hardware compatibility claim for that development milestone.
