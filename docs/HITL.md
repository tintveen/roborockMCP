# Human-in-the-loop validation

No step in this document is authorized merely because the code exists. The user
must explicitly start the HITL phase and remain physically present.

## Required inputs

- exact device selected from `get_devices`;
- a named test room;
- a clear navigation point from a fresh map revision;
- a safe temporary boundary geometry;
- immediate physical access to the robot;
- pets and people outside the motion area.

## Stages

Run `roborock-mcp live-test plan` for the canonical order. At each stage:

1. capture the before state;
2. show the exact requested effect;
3. obtain the Codex approval;
4. invoke once;
5. read back state;
6. restore the before state;
7. read back again;
8. stop on ambiguity rather than retrying.

The targeted official-app capture uses Roborockmitmproxy and a temporary test CA.
Raw captures remain outside Git, are sanitized into narrow fixtures, and are then
deleted. The CA is removed from the phone immediately after capture.

Quick mapping may leave a deliberately named test map. Because MCP map deletion
is not in 0.1.0, the user removes that map in the official app after verification.

Consumable reset is never executed during acceptance.
