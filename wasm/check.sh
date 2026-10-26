#!/usr/bin/env bash
set -euo pipefail

if (($# == 0)); then
  echo "usage: $0 FILE.wasm [...]" >&2
  exit 1
fi

if ! command -v wasm-tools >/dev/null 2>&1; then
  echo "error: wasm-tools was not found" >&2
  exit 1
fi

status=0
for module in "$@"; do
  echo "checking $module"
  wasm-tools validate --features memory64 "$module"
  imports=$(wasm-tools print "$module" | grep '(import' || true)
  if [[ -n "$imports" ]]; then
    echo "error: reactor contains imports:" >&2
    echo "$imports" >&2
    status=1
  else
    echo "ok: no imports"
  fi
done
exit "$status"
