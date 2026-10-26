#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
CC=${CC:-cc}
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

while IFS= read -r source; do
  name=$(basename "${source%.c}")
  benchmark_dir=$(dirname "$ROOT_DIR/${source#./}")
  object="$TEMP_DIR/$name.o"
  executable="$TEMP_DIR/$name"

  echo "testing $name"
  "$CC" -std=c99 -O2 \
    -DMINI_DATASET \
    -DPOLYBENCH_WASM_REACTOR \
    -Dmain=polybench_original_main \
    -I"$ROOT_DIR/utilities" \
    -I"$benchmark_dir" \
    -c "$ROOT_DIR/${source#./}" \
    -o "$object"

  "$CC" -std=c99 -O2 \
    "$ROOT_DIR/utilities/polybench-wasm.c" \
    "$ROOT_DIR/wasm/reactor.c" \
    "$ROOT_DIR/wasm/native-smoke.c" \
    "$object" \
    -lm \
    -o "$executable"

  "$executable"
done < "$ROOT_DIR/utilities/benchmark_list"
