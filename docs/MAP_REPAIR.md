# Stationary room-map repair

Status: implemented and tested offline. Supervised read-only discovery, status,
room membership and native map reads worked on S8 MaxV Ultra firmware 02.39.60.
Room writes, restoration and complete repair acceptance remain unverified.
Public release follows successful supervised acceptance. The tool surface
remains exactly 25 semantic tools.

## What the feature does

`get_map` returns a device/map-bound content revision, fresh room membership,
native V1 coordinates, and actual PNG image content. `geometry` exposes a
50 mm occupancy grid encoded as `[offset, length, cell_kind_or_segment]` runs.
Offsets are row-major; rows increase in native y. PNG calibration comes from
the upstream renderer and accounts for its scale, rotation and y inversion.
Never estimate command coordinates from an app screenshot.

`summary` excludes images and grid geometry. `geometry` excludes the image.
`image` includes an MCP image content block. `all` includes geometry and the
image. `repair_preview` additionally returns a code-generated SVG diagram,
candidate split lines, supporting measurements, uncertainties, and a proposed
split/read-back/merge sequence. It does not change the robot or map.

Only the active map can currently be read. Requesting another map fails rather
than switching maps behind the caller's back. Reads detect active-map and room
binding changes during acquisition. Revisions include native floor membership,
room names and bindings, restrictions, device identity and map identity. Robot
position, route, map sequence counters, and rendered pixels are excluded.

Candidate generation extends long observed wall axes and looks for small room
protrusions toward an adjacent room predominantly on the opposite side. Each
candidate must divide the selected room into two connected floor regions. All
candidates require review: furniture, missing observations and corrupted SLAM
geometry can look like walls. A single observation does not establish that a
wall is persistent or that the historical layout has been recovered. Absence of
a supported candidate is reported explicitly; it does not trigger remapping.

`edit_rooms` requires an opaque device key, an explicit active map ID and the
latest map revision. `dry_run` defaults to true. Preview a split or merge, review
it, then set `dry_run=false` to dispatch that one operation. Refresh afterwards
and resolve any newly assigned room IDs before constructing another operation.
Names and room keys from a different device or map cannot identify a room.

The initial supervised session stopped before any write: the user confirmed
incorrect wall geometry and no usable native backup. This is a diagnostic stop,
not successful repair or hardware acceptance. Split previews also require
connected floor parts; maps with disconnected fragments can produce no candidate.
Do not weaken that result into an automatic correction or claim reconstructed
walls. With remapping excluded, this case has no supported repair path.

## Separately authorized remapping

If the user subsequently authorizes a mapping run, the stationary-only constraint
changes for that run. Keep `--stationary-repair` unchanged: it must still reject
mapping. Use a separately configured session for the authorized mapping action.

`manage_map` now previews by default. `start_quick_mapping` requires a fresh map
revision, the selected device at its charging dock, at least 20% battery, and a
fresh inventory proving a free map slot. With only one occupied slot, enable
multiple floors in the official app first. The adapter never deletes a map to
make room. Preview, then explicitly apply once with `dry_run=false` after the
human has opened the intended interior doors and prepared the mapping area.
No cleaning command is sent. Actual mapping and retention behavior still need
hardware acceptance; a free slot is a precondition, not proof of retention.

Refresh status and the newly active map after dispatch; do not reconcile using
the old map ID because mapping may change it. On uncertain dispatch, stop and
inspect those reads instead of resending. Save and name the new map through the
official app where the adapter lacks verified semantics, confirm the original
map remains recoverable, and inspect all room selections against the floor plan.

Room renaming through the previous guessed `name_segment` payload is disabled.
The command associates cloud room identifiers on some implementations, which is
not a verified display-name operation for this device. Rename in the official
app and read back. The earlier unverified `save_map` boundary-write payload is
also disabled. Repair does not add barriers to hide segmentation errors.

## Start only after explicit HITL authorization

The human must be present at the robot and confirm starting HITL. A phone
notification or approval of offline development is not that confirmation.
Configure credentials through the existing out-of-band OTP/keyring flow only
after that confirmation; never put credentials in a chat or repository.

Start the server for this session with:

```sh
uv run roborock-mcp serve --profile full-s8 --stationary-repair
```

This mode blocks every write tool except `edit_rooms`, including all
`manage_map` actions, dock activity, cleaning, navigation, remote control,
camera and microphone actions. All 25 tools remain listed; blocked calls fail
before resolving a device. It does not disable schedules, buttons, other apps
or other clients. Choose a window with no scheduled cleaning and close other
map editors. The robot must report idle/charging and no unfinished cleaning
task at each edit preflight.

1. List devices. When names collide, inspect each map read-only, match the
   intended layout and exclude the other robot. Retain the opaque device key.
2. Compare available native backups in the official app before reconstructing
   room divisions. Inspect previews without loading a different map merely to
   inspect it. Preserve any existing good backup.
3. Capture current map/rooms, room cleaning settings, restrictions, and
   schedule/routine room references privately outside Git. Inspect those
   references in the app where an API read is unavailable.
4. Review actual walls and doorways against the repair preview. If occupancy
   geometry is distorted, use a verified good native backup or stop. Room
   splitting cannot repair SLAM wall geometry.
5. Approve an exact single operation, apply once, and fetch the map again.
   Reject stale revisions and replan after every edit. Check room identities
   and settings rather than assuming that segment numbers remain stable.
6. Reopen the app. Select each affected room without starting cleaning and
   confirm its highlight covers only the intended floor area. Check unaffected
   rooms, restrictions, names and schedule/routine references as well.
7. Save a native backup of the accepted map if the firmware/app offers it.
   Read the map again to verify persistence. Record supported firmware and
   the exact operations observed, not a blanket hardware-compatibility claim.

Native backup creation, backup inventory and restoration are **not implemented
by this adapter**. A command name or a feature bit does not establish safe
payload semantics for an S8 MaxV Ultra. Recovery metadata directs users to the
official app; map snapshots and SVG/PNG files are inspection evidence, not
restorable native backups. Never overwrite the sole known-good native backup
with a drifted map. No permanent map-lock capability is claimed.

## Failure handling and limitations

Map writes use a pinned single-attempt adapter rather than the upstream
local-to-cloud fallback loop. Timeout, cancellation, rejection, or an unfamiliar
acknowledgement after dispatch yields `OUTCOME_UNCERTAIN`, `retryable=false`,
and a `get_map` reconciliation call for the exact device/map. Do not resend the
write or execute the next operation. An acknowledgement alone is not map
verification. Preflight read failures are distinguished from dispatched writes.

Per-device locks serialize map writes within this server. The device protocol
does not support an atomic compare-and-swap; another client or scheduled task
can still change state between preflight and dispatch. Human supervision and
closing other map editors remain necessary. Reconnect does not authorize
continuing an old operation sequence.

The adapter consumes raw maps only in process memory. This workflow does not
write maps, account identifiers, images or protocol captures into the checkout.
Only independently constructed synthetic fixtures belong in tests or release
materials. Native room selection may still require transit through another
room; this workflow does not promise navigation or cleaning performance without
a later, separately authorized cleaning test.

## Implementation evidence

- Grid layout and pixel flags: installed `vacuum-map-parser-roborock==0.1.5`
  `map_data_parser.py` and `image_parser.py`, via `python-roborock==7.1.1`.
- Transport: pinned `roborock.devices.rpc.v1_channel.RpcChannel` strategy loop
  and `_send_rpc` primitive. The adapter fails closed on another library version.
- Historical split/merge payloads: [Valetudo source at immutable revision](https://github.com/rand256/valetudo/blob/ab9fe2fb379f7f10d9a1556739b4fcc78cbfe03b/lib/miio/Vacuum.js).
  This establishes candidate protocol syntax, not a97 firmware compatibility.
- Mapping-only start: [Valetudo mapping capability at immutable revision](https://github.com/Hypfer/Valetudo/blob/31bbc50c8dc34d02d4ed1a24d2febe0444344c64/backend/lib/robots/roborock/capabilities/RoborockMappingPassCapability.js)
  sends `app_start_build_map` with empty parameters. This is source evidence,
  not proof of map retention or acceptance on a97 firmware.
- [Official room merge/divide guidance](https://support.roborock.com/hc/en-us/articles/360030486432-How-do-I-merge-or-divide-rooms-on-the-map).

Run Ruff, mypy, fake-only pytest, the package build, and CI before handoff.
The schema snapshot includes all 25 tools. Publication and version `0.1.0`
remain gated on the supervised acceptance above plus an audit of Git history,
release artifacts and documentation for private device/home data.
