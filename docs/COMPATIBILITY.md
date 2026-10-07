# Compatibility

| Model | Protocol | Firmware | Region | Status |
|---|---|---|---|---|
| Roborock S8 MaxV Ultra (`roborock.vacuum.a97`) | V1 | 02.39.60 | Not independently qualified | Supervised native map reads and stationary split/merge repair; official-app boundary acceptance |

The human created a native backup in the official app after accepting the room
boundaries. Existing cloud names were restored and confirmed in the app; native
order was acknowledged and read back. Name restoration resets room-type tags to
0 and required reconciliation after an unfamiliar acknowledgement. Arbitrary
new names and adapter backup/restore remain unsupported. Cleaning, mapping,
navigation, dock actions, camera/audio and other write branches are not hardware
qualified by this result. No robot movement was used during map acceptance.

See [the repair workflow](MAP_REPAIR.md) for observed identifier changes,
fragment handling, recovery and remaining limits. A runtime response's
`live_verified_on_a97: false` means that call does not carry a live acceptance
certificate; it does not replace this narrowly scoped compatibility report.

Other V1 vacuums are capability-gated best effort and are not certified by the
0.1.0 acceptance process.
