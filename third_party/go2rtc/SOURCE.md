# go2rtc media sidecar

The media sidecar is built from upstream `go2rtc` tag `v1.9.14`
(`b5948cfb25404cc5cb37b166ecaa2dca20b11d4b`) plus the narrow Roborock
region and MQTT client-id backport in `patches/0001-roborock-region-client-id.patch`.

The backport originates from commit
`09d7843d6028639c8ceabe97dedf6325aa00dede` and remains licensed under the
upstream MIT license. The release workflow records source and binary SHA-256
checksums. No Roborock credentials are compiled into or written beside the
binary.
