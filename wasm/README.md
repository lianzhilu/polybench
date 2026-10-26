# Import-free wasm32/wasm64 reactors

This directory adds a WebAssembly build path without changing the original
PolyBench/C programs.  Each program is built as a reactor with two exports:

- `benchmark_run() -> i32`: initialize, execute, checksum, and clean up once;
- `benchmark_checksum() -> i64`: return an FNV-1a digest of live-out values.

The modules intentionally import neither WASI nor Emscripten JavaScript APIs.
They can therefore be instantiated by a Core WebAssembly runtime with
Memory64 support.  Timing is performed by the host.  A single CLI invocation
includes allocation, initialization, checksum, and cleanup; use an embedding
harness when measuring individual phases or repeated calls.

## Build

Activate emsdk, then build one benchmark in both address widths:

```sh
./wasm/build.sh 2mm MEDIUM
```

Build all 30 benchmarks:

```sh
./wasm/build.sh all MEDIUM
```

Environment variables can override build settings:

```sh
INITIAL_MEMORY=536870912 STACK_SIZE=16777216 OPT_LEVEL=-O3 \
  ./wasm/build.sh gemm SMALL
```

Artifacts are written to `build/wasm/` by default. Set `OUTPUT_DIR` to use a
different directory.

## Verify

Use `wasm-tools` to validate the modules and fail if any import remains:

```sh
./wasm/check.sh build/wasm/2mm-wasm32.wasm build/wasm/2mm-wasm64.wasm
```

## Run

Wasmtime:

```sh
wasmtime run --invoke benchmark_run build/wasm/2mm-wasm32.wasm
wasmtime run -W memory64=y --invoke benchmark_run build/wasm/2mm-wasm64.wasm
```

Wasmer (backend support varies; Emscripten wasm64 may require LLVM or V8):

```sh
wasmer run --invoke benchmark_run build/wasm/2mm-wasm32.wasm
wasmer run --enable-memory64 --llvm --invoke benchmark_run \
  build/wasm/2mm-wasm64.wasm
```

`benchmark_checksum` is intended for an embedding harness that keeps an
instance alive and reads the digest after `benchmark_run`. The Wasmtime and
Wasmer CLI commands above are smoke tests, not a statistically sound timing
method.

## Methodology note

This build uses PolyBench's default heap arrays. For experiments that
specifically amplify linear-memory shadow-stack traffic, add
`-DPOLYBENCH_STACK_ARRAYS` and increase `STACK_SIZE`; report those results as a
separate, non-default variant.
