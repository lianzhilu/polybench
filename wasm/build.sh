#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
BENCHMARK=${1:-all}
DATASET=${2:-MEDIUM}
OUTPUT_DIR=${OUTPUT_DIR:-"$ROOT_DIR/build/wasm"}
INITIAL_MEMORY=${INITIAL_MEMORY:-1073741824}
STACK_SIZE=${STACK_SIZE:-16777216}
OPT_LEVEL=${OPT_LEVEL:--O3}

if ! command -v emcc >/dev/null 2>&1; then
  echo "error: emcc was not found; activate emsdk first" >&2
  exit 1
fi

case "$DATASET" in
  MINI|SMALL|MEDIUM|LARGE|EXTRALARGE) ;;
  *)
    echo "error: dataset must be MINI, SMALL, MEDIUM, LARGE, or EXTRALARGE" >&2
    exit 1
    ;;
esac

resolve_benchmark() {
  local requested=$1
  local source
  while IFS= read -r source; do
    if [[ "$requested" == "$source" || "$requested" == "$(basename "${source%.c}")" ]]; then
      printf '%s\n' "$source"
      return 0
    fi
  done < "$ROOT_DIR/utilities/benchmark_list"
  echo "error: unknown benchmark: $requested" >&2
  return 1
}

build_one() {
  local source=$1
  local name
  local benchmark_dir
  local arch
  local memory_flag
  local output

  name=$(basename "${source%.c}")
  benchmark_dir=$(dirname "$ROOT_DIR/${source#./}")

  for arch in 32 64; do
    memory_flag="-m${arch}"
    output="$OUTPUT_DIR/${name}-wasm${arch}.wasm"
    mkdir -p "$OUTPUT_DIR"

    echo "building $name wasm$arch ($DATASET)"
    emcc "$memory_flag" "$OPT_LEVEL" \
      -D"${DATASET}_DATASET" \
      -DPOLYBENCH_WASM_REACTOR \
      -Dmain=polybench_original_main \
      -I"$ROOT_DIR/utilities" \
      -I"$benchmark_dir" \
      "$ROOT_DIR/utilities/polybench-wasm.c" \
      "$ROOT_DIR/wasm/reactor.c" \
      "$ROOT_DIR/${source#./}" \
      --no-entry \
      -sSTANDALONE_WASM \
      -sFILESYSTEM=0 \
      -sMALLOC=emmalloc \
      -sINITIAL_MEMORY="$INITIAL_MEMORY" \
      -sSTACK_SIZE="$STACK_SIZE" \
      -Wl,--export=benchmark_run \
      -Wl,--export=benchmark_checksum \
      -o "$output"
  done
}

if [[ "$BENCHMARK" == all ]]; then
  while IFS= read -r source; do
    build_one "$source"
  done < "$ROOT_DIR/utilities/benchmark_list"
else
  build_one "$(resolve_benchmark "$BENCHMARK")"
fi

echo "artifacts written to $OUTPUT_DIR"
