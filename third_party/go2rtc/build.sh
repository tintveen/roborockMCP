#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=${ROBOROCKMCP_GO2RTC_SOURCE:-"$root_dir/.cache/go2rtc-v1.9.14"}
output_dir=${ROBOROCKMCP_GO2RTC_OUTPUT:-"$root_dir/build/media"}

if [ ! -d "$source_dir/.git" ]; then
  mkdir -p "$(dirname -- "$source_dir")"
  git clone --depth 1 --branch v1.9.14 https://github.com/AlexxIT/go2rtc.git "$source_dir"
  git -C "$source_dir" apply "$root_dir/third_party/go2rtc/patches/0001-roborock-region-client-id.patch"
fi

mkdir -p "$output_dir"
go -C "$source_dir" build -trimpath -ldflags "-s -w" -o "$output_dir/go2rtc-roborockmcp" .
shasum -a 256 "$output_dir/go2rtc-roborockmcp"
