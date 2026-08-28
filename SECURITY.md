# Security policy

## Supported version

Only the newest private development revision is supported before 0.1.0.

## Sensitive material

Never attach Roborock tokens, local keys, DUIDs, maps, obstacle images, camera
media, Homesec PINs, MQTT credentials, SDP/ICE/TURN data, or raw app captures to
an issue or commit. Reports should use synthetic or fully sanitized fixtures.

The server is designed for local Codex STDIO use. The media viewer and sidecar
must remain loopback-only. Other MCP clients may not honor Codex approval hints
and are not security-supported in 0.1.0.

## Reporting

Use a private GitHub security advisory for vulnerabilities. Do not demonstrate a
report against a robot or account you do not own.
