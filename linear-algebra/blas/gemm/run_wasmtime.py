#!/usr/bin/env python3
"""Validate and benchmark GEMM wasm32/wasm64 modules. Run from the repository root."""

from array import array
from importlib.metadata import version
import math
import platform
import random
import statistics
import sys
from time import perf_counter_ns
import wasmtime


def close(actual, expected):
    assert math.isfinite(actual) and math.isfinite(expected)
    assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-9), (actual, expected)


def read_c(store, exports, ni, nj):
    offset = exports["gemm_c_offset"](store)
    stride = exports["gemm_row_stride"](store)
    raw = exports["memory"].read(store, offset, offset + ni * stride * 8)
    values = array("d")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    assert len(values) == ni * stride
    return [values[i * stride + j] for i in range(ni) for j in range(nj)]


def main():
    config = wasmtime.Config()
    config.wasm_memory64 = True
    config.strategy = "cranelift"
    config.cranelift_opt_level = "speed"
    engine = wasmtime.Engine(config)
    modules = {}

    for bits in (32, 64):
        module = wasmtime.Module.from_file(engine, f"build/gemm-remain-kernel-wasm{bits}.wasm")
        assert not module.imports, [(i.module, i.name) for i in module.imports]
        store = wasmtime.Store(engine)
        instance = wasmtime.Instance(store, module, [])
        exports = instance.exports(store)
        if "_initialize" in exports:
            exports["_initialize"](store)
        assert exports["gemm_pointer_bits"](store) == bits
        assert exports["memory"].type(store).is_64 == (bits == 64)
        assert exports["memory"].data_len(store) == 536870912
        shape = tuple(exports[name](store) for name in ("gemm_ni", "gemm_nj", "gemm_nk"))
        exports["gemm_init"](store)
        exports["gemm_run"](store)
        values = read_c(store, exports, shape[0], shape[1])
        modules[bits] = (store, exports, shape, values)

    assert modules[32][2] == modules[64][2]
    ni, nj, nk = modules[32][2]
    assert ni * nj * nk <= 100_000_000, "Full Python reference is slow; use MEDIUM or smaller"

    # Independent reference: original initialization formulas and i/k/j loop order.
    a = [[(i * (k + 1) % nk) / nk for k in range(nk)] for i in range(ni)]
    b = [[(k * (j + 2) % nj) / nj for j in range(nj)] for k in range(nk)]
    expected = []
    for i in range(ni):
        row = [1.2 * (((i * j + 1) % ni) / ni) for j in range(nj)]
        for k in range(nk):
            scale = 1.5 * a[i][k]
            for j in range(nj):
                row[j] += scale * b[k][j]
        expected.extend(row)
    expected_sum = math.fsum(expected)
    for bits in (32, 64):
        store, exports, _, values = modules[bits]
        assert len(values) == len(expected)
        for actual, reference in zip(values, expected):
            close(actual, reference)
        close(exports["gemm_checksum"](store), expected_sum)

    # Each warmup and sample starts from the same input values.
    for _ in range(5):
        for store, exports, _, _ in modules.values():
            exports["gemm_init"](store)
            exports["gemm_run"](store)

    times = {32: [], 64: []}
    rng = random.Random(20261027)
    for sample in range(31):
        order = [32, 64]
        rng.shuffle(order)
        for bits in order:
            store, exports, _, _ = modules[bits]
            init, run, checksum = (exports[n] for n in ("gemm_init", "gemm_run", "gemm_checksum"))
            init(store)                         # Outside timing: restore inputs.
            start = perf_counter_ns()
            run(store)                          # Timed: one host call and GEMM.
            elapsed = perf_counter_ns() - start
            close(checksum(store), expected_sum) # Outside timing: validate.
            times[bits].append(elapsed / 1e6)

    for bits in (32, 64):
        store, exports, _, _ = modules[bits]
        for actual, reference in zip(read_c(store, exports, ni, nj), expected):
            close(actual, reference)
        exports["gemm_free"](store)
        q1, _, q3 = statistics.quantiles(times[bits], n=4, method="inclusive")
        print(f"wasm{bits}: median={statistics.median(times[bits]):.6f} ms; IQR=[{q1:.6f}, {q3:.6f}] ms")
        print(f"wasm{bits} raw samples (ms): {times[bits]}")
    print("T64/T32 =", statistics.median(times[64]) / statistics.median(times[32]))
    print("Validation passed:", ni * nj, "elements per module; imports=0")
    print("Wasmtime Python:", version("wasmtime"), "system:", platform.system(), platform.machine())
    print("Cache policy: reset inputs before timing; no explicit cache flush")


if __name__ == "__main__":
    main()
