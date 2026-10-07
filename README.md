# roborockMCP

Codex-first Model Context Protocol server with supervised stationary room-map
repair for a Roborock S8 MaxV Ultra.

> [!WARNING]
> Stationary split/merge repair was accepted in the official app on firmware
> 02.39.60. Other control paths remain experimental and hardware-unverified.
> No cleaning or movement was used for this acceptance. Connect a real robot
> only during an explicitly started, physically supervised HITL session.

## What is implemented

- MCP v2 server over local STDIO with exactly 25 semantic tools.
- Out-of-band email OTP login and OS-keyring credential storage.
- Dynamic device/capability discovery through `python-roborock` 7.1.1.
- Bounded remote-control and short-lived, loopback-only media abstractions.
- A pinned go2rtc v1.9.14 sidecar build with a narrow region/client-ID patch.
- Stable errors, single-dispatch RPC writes, and read-back guidance for uncertain outcomes.
- Fake-backed unit and in-memory MCP tests that never contact Roborock.
- Stationary map repair: native geometry/image reads, conservative room-boundary
  previews, explicit device/map revisions, single-dispatch split/merge edits, and
  restoration of existing names and native room order.

See [the stationary repair workflow](docs/MAP_REPAIR.md) for supervision,
recovery limitations, and the `--stationary-repair` server mode. Room edits now
preview by default. Existing-name restoration explicitly discloses that it
resets room-type tags. Arbitrary new names and boundary writes are disabled;
use the official app for those operations and native map backups.

There is deliberately no arbitrary `raw_command` tool. Map deletion, backup,
and recovery are not part of 0.1.0. Consumable reset is exposed but is explicitly
excluded from physical live testing because it cannot be restored.

## First safe success

Requirements: `uv`, Python 3.11–3.14, and Git.

```bash
uv sync --all-groups
uv run pytest
uv run roborock-mcp --version
uv run roborock-mcp live-test plan
```

Run the protocol server against deterministic fake state:

```bash
uv run roborock-mcp serve --fake
```

This command speaks MCP on stdout; it is not an interactive shell.

## Codex development configuration

Copy the relevant portion of [`.codex/config.toml.example`](.codex/config.toml.example)
to a trusted project or user Codex configuration. The example starts this
checkout directly and prompts for writes plus sensitive reads.

Codex supports local STDIO MCP servers and per-tool approval modes. See the
[official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

## CLI

```text
roborock-mcp serve [--profile full-s8] [--fake]
roborock-mcp auth login|status|logout|set-camera-pin
roborock-mcp profile show|set-device
roborock-mcp media build|install|status
roborock-mcp doctor
roborock-mcp live-test plan|run
```

`auth login`, real-device `serve`, app capture, and live testing are reserved for
the joint HITL phase. Credentials are never MCP tools.

## Tool surface

Read tools:

1. `get_devices`
2. `get_status`
3. `get_map`
4. `get_cleaning_settings`
5. `get_dock_status`
6. `get_automations`
7. `get_cleaning_history`
8. `get_maintenance`
9. `get_obstacles`
10. `get_device_settings`

Control and write tools:

11. `start_cleaning`
12. `control_cleaning`
13. `run_routine`
14. `set_cleaning_settings`
15. `dock_action`
16. `set_dock_settings`
17. `navigate`
18. `remote_control`
19. `manage_map`
20. `edit_rooms`
21. `edit_map_boundaries`
22. `manage_schedule`
23. `reset_consumable`
24. `set_device_settings`
25. `telepresence`

Responses distinguish read verification from unverified write acknowledgements.
The runtime does not certify hardware acceptance for an individual call; see
[the firmware compatibility report](docs/COMPATIBILITY.md) for observed results.

## Media sidecar

The repository does not commit a binary. Build from the pinned source and patch:

```bash
uv run roborock-mcp media build
uv run roborock-mcp media install --profile full-s8
```

The sidecar binds only to `127.0.0.1`. Roborock source URIs and media credentials
exist only in process memory. Video/audio recording is not supported. `ffmpeg` is
required later for live JPEG snapshots and is checked by `doctor`.

## Human-in-the-loop boundary

The code-complete milestone ends after fake tests, static analysis, build checks,
and a secret/history audit. Hardware work then proceeds one stage at a time:

1. local doctor;
2. user-entered OTP and device selection;
3. reads;
4. reversible low-risk writes;
5. targeted official-app protocol capture;
6. map and schedule mutation/restore;
7. dock, navigation, and bounded RC;
8. camera and voice;
9. sanitized acceptance report.

See [`docs/HITL.md`](docs/HITL.md). This sequence is not blanket authorization to
exercise every feature. A stationary repair session blocks motion and all other
write tools, even if they are visible in the tool list.

## Privacy and affiliation

This is an unofficial community project and is not affiliated with or endorsed
by Roborock. Roborock names and marks belong to their respective owner. Home
maps, obstacle photos, camera streams, account material, protocol captures, and
device identifiers must never be committed.

## License

Project code is Apache-2.0. The patched go2rtc build remains subject to its MIT
license; see [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).
