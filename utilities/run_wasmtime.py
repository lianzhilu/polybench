#!/usr/bin/env python3
"""Validate and time host-driven PolyBench modules (ABI v1)."""

import argparse
from array import array
import ctypes
import hashlib
from importlib.metadata import version
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import struct
import sys
from time import perf_counter_ns

import wasmtime

ROOT = Path(__file__).resolve().parent.parent
TYPES = {1: ("float64", "d", 8), 2: ("float32", "f", 4), 3: ("int32", "i", 4)}
MEMORY_BYTES = 536870912
PERF_EVENTS = {"cycles": 0, "instructions": 1, "branches": 4, "branch-misses": 5}


class PerfEventAttr(ctypes.Structure):
    """Linux perf_event_attr ABI v0 (64 bytes); newer fields are unused."""

    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("config", ctypes.c_uint64), ("sample_period", ctypes.c_uint64),
                ("sample_type", ctypes.c_uint64), ("read_format", ctypes.c_uint64),
                ("flags", ctypes.c_uint64), ("wakeup_events", ctypes.c_uint32),
                ("bp_type", ctypes.c_uint32), ("config1", ctypes.c_uint64)]


class PerfCounters:
    """Thread-local user-space hardware event group, Linux x86_64 only.

    Constants follow linux/perf_event.h and the x86_64 syscall ABI. No
    subprocess or Wasm import is needed. Counts include host-call overhead.
    """

    def __init__(self, events):
        if (platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64")
                or ctypes.sizeof(ctypes.c_void_p) != 8):
            raise ValueError("--profile counters requires 64-bit Python on Linux x86_64")
        import fcntl
        self.ioctl = fcntl.ioctl
        self.fds, self.ids = [], {}
        self.events = events
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        syscall.argtypes = [ctypes.c_long, ctypes.POINTER(PerfEventAttr),
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        try:
            for event in events:
                attr = PerfEventAttr()
                attr.size = ctypes.sizeof(attr)
                attr.type, attr.config = 0, PERF_EVENTS[event]  # PERF_TYPE_HARDWARE
                attr.read_format = 1 | 2 | 4 | 8  # enabled, running, ID, GROUP
                attr.flags = (1 if not self.fds else 0) | (1 << 5) | (1 << 6)
                # pid=0: this thread; cpu=-1: follows it; no inheritance.
                fd = syscall(298, ctypes.byref(attr), 0, -1,
                             self.fds[0] if self.fds else -1, 8)  # FD_CLOEXEC
                if fd < 0:
                    error = ctypes.get_errno()
                    raise OSError(error, f"perf_event_open({event}): {os.strerror(error)}. "
                                  "Check /proc/sys/kernel/perf_event_paranoid, CAP_PERFMON "
                                  "and hardware/VM PMU support; no timing-only fallback.")
                self.fds.append(fd)
                event_id = array("Q", [0])
                self.ioctl(fd, 0x80082407, event_id, True)  # PERF_EVENT_IOC_ID
                self.ids[event_id[0]] = event
        except BaseException:
            self.close()
            raise

    def read(self):
        size = 24 + 16 * len(self.events)
        raw = os.read(self.fds[0], size)
        if len(raw) != size:
            raise ValueError("Incomplete PMU group read; events may not be schedulable")
        words = struct.unpack("=" + "Q" * (size // 8), raw)
        if words[0] != len(self.events):
            raise ValueError("Incorrect PMU event group size")
        counts = {}
        for value, event_id in zip(words[3::2], words[4::2]):
            if event_id not in self.ids or self.ids[event_id] in counts:
                raise ValueError("Incorrect PMU event ID")
            counts[self.ids[event_id]] = value
        return words[1], words[2], counts

    def start(self):
        # RESET clears counts, but NOT cumulative time_enabled/time_running.
        # Save the latter while disabled and subtract them after this sample.
        self.before = self.read()[:2]
        self.ioctl(self.fds[0], 0x2403, 1)  # RESET, PERF_IOC_FLAG_GROUP
        self.ioctl(self.fds[0], 0x2400, 1)  # ENABLE group

    def stop(self):
        self.ioctl(self.fds[0], 0x2401, 1)  # DISABLE group

    def sample(self):
        enabled, running, counts = self.read()
        enabled -= self.before[0]
        running -= self.before[1]
        if enabled <= 0 or running <= 0 or running > enabled:
            raise ValueError(f"Invalid PMU running time: enabled={enabled}, running={running}")
        if running != enabled:
            raise ValueError(f"PMU group was multiplexed: enabled={enabled}, running={running}. "
                             "Use fewer --events or stop competing profilers; counts are not scaled.")
        if "cycles" in counts and counts["cycles"] == 0:
            raise ValueError("PMU reported zero cycles for the kernel call")
        return {"counts": counts, "time_enabled_ns": enabled, "time_running_ns": running,
                "running_ratio": running / enabled,
                "ipc": (counts["instructions"] / counts["cycles"]
                        if "instructions" in counts and "cycles" in counts else None)}

    def close(self):
        # Closing all group descriptors also stops counting on failure.
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds.clear()


def measure(bench, counters):
    if counters is not None:
        counters.start()
    try:
        start = perf_counter_ns()
        bench.run()
        elapsed_ms = (perf_counter_ns() - start) / 1e6
    finally:
        if counters is not None:
            counters.stop()
    return elapsed_ms, counters.sample() if counters is not None else None


def counter_summary(records, bits, events):
    samples = [r["counters"] for r in records if r["bits"] == bits]
    totals = {event: sum(s["counts"][event] for s in samples) for event in events}
    return {"samples": len(samples), "total_counts": totals,
            "median_counts_per_call": {
                event: statistics.median(s["counts"][event] for s in samples) for event in events},
            "mean_counts_per_call": {event: total / len(samples) for event, total in totals.items()},
            "ipc": (totals["instructions"] / totals["cycles"]
                    if "instructions" in totals and "cycles" in totals else None),
            "min_running_ratio": min(s["running_ratio"] for s in samples)}


def metadata(bench):
    if bench.call("bench_abi_version") != 1:
        raise ValueError("Unsupported benchmark ABI")
    outputs = []
    count = bench.call("bench_output_count")
    if not 1 <= count <= 4:
        raise ValueError("Invalid output count")
    for index in range(count):
        rank = bench.call("bench_output_rank", index)
        dtype = bench.call("bench_output_type", index)
        region = bench.call("bench_output_region", index)
        if rank not in (1, 2, 3) or dtype not in TYPES or region not in (0, 1, 2):
            raise ValueError("Invalid output descriptor")
        if region and rank != 2:
            raise ValueError("Triangular regions require a matrix")
        dims = [bench.call("bench_output_dim", index, axis) for axis in range(rank)]
        strides = [bench.call("bench_output_stride", index, axis) for axis in range(rank)]
        width = TYPES[dtype][2]
        if any(d <= 0 for d in dims) or any(s <= 0 or s % width for s in strides):
            raise ValueError("Invalid dimensions/strides")
        outputs.append({"type": dtype, "shape": dims, "strides": strides, "region": region})
    params = [bench.call("bench_param", i) for i in range(bench.call("bench_param_count"))]
    return {"params": params, "outputs": outputs}


def snapshot(bench, descriptors):
    results = []
    for index, desc in enumerate(descriptors["outputs"]):
        _, typecode, width = TYPES[desc["type"]]
        dims, strides = desc["shape"], desc["strides"]
        size = sum((d - 1) * s for d, s in zip(dims, strides)) + width
        if size > MEMORY_BYTES:
            raise ValueError("Output exceeds configured memory budget")
        offset = bench.call("bench_output_offset", index)
        raw = bench.read(offset, size)
        if len(raw) != size:
            raise ValueError("Incomplete result buffer")
        values = array(typecode)
        if values.itemsize != width:
            raise ValueError("Unsupported host array element width")
        values.frombytes(raw)
        if bench.little_endian and sys.byteorder != "little":
            values.byteswap()
        selected = []
        for coords in itertools.product(*(range(d) for d in dims)):
            if desc["region"] == 1 and coords[1] > coords[0]:
                continue
            if desc["region"] == 2 and coords[1] < coords[0]:
                continue
            selected.append(values[sum(c * s for c, s in zip(coords, strides)) // width])
        results.append(selected)
    return results


class WasmBenchmark:
    little_endian = True

    def __init__(self, engine, path, bits, emit_code_dir=None):
        module = wasmtime.Module.from_file(engine, str(path))
        if module.imports:
            raise ValueError(f"{path}: unexpected imports " +
                             str([(i.module, i.name) for i in module.imports]))
        self.code_artifact = None
        if emit_code_dir is not None:
            # Serialize the exact Module that is instantiated below, outside timing.
            directory = emit_code_dir / path.name.removesuffix(f"-remain-kernel-wasm{bits}.wasm") / f"wasm{bits}"
            directory.mkdir(parents=True, exist_ok=True)
            compiled = bytes(module.serialize())
            target = directory / "module.cwasm"
            with target.open("xb") as output:  # never silently overwrite an experiment
                output.write(compiled)
            self.code_artifact = {"path": str(target.resolve()),
                                  "sha256": hashlib.sha256(compiled).hexdigest(),
                                  "bytes": len(compiled)}
        self.store = wasmtime.Store(engine)
        self.instance = wasmtime.Instance(self.store, module, [])
        self.exports = self.instance.exports(self.store)
        if "_initialize" in self.exports:
            self.exports["_initialize"](self.store)
        memory = self.exports["memory"]
        if memory.type(self.store).is_64 != (bits == 64):
            raise ValueError(f"{path}: incorrect memory type")
        if memory.data_len(self.store) != MEMORY_BYTES:
            raise ValueError(f"{path}: expected 512 MiB fixed memory")
        if self.call("bench_pointer_bits") != bits:
            raise ValueError(f"{path}: incorrect C pointer width")
        self.init_func = self.exports["bench_init"]
        self.run_func = self.exports["bench_run"]
        self.free_func = self.exports["bench_free"]
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    def call(self, name, *args):
        return self.exports[name](self.store, *args)

    def init(self):
        self.init_func(self.store)

    def run(self):
        self.run_func(self.store)

    def free(self):
        self.free_func(self.store)

    def read(self, offset, size):
        return self.exports["memory"].read(self.store, offset, offset + size)


class NativeBenchmark:
    little_endian = False

    def __init__(self, path):
        self.library = ctypes.CDLL(str(path.resolve()))
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ("bench_init", "bench_run", "bench_free"):
            func = getattr(self.library, name)
            func.argtypes, func.restype = [], None
        for name in ("bench_abi_version", "bench_pointer_bits", "bench_output_count", "bench_param_count"):
            func = getattr(self.library, name)
            func.argtypes, func.restype = [], ctypes.c_int
        for name in ("bench_output_type", "bench_output_rank", "bench_output_region", "bench_param"):
            func = getattr(self.library, name)
            func.argtypes, func.restype = [ctypes.c_int], ctypes.c_int
        self.library.bench_output_dim.argtypes = [ctypes.c_int, ctypes.c_int]
        self.library.bench_output_dim.restype = ctypes.c_int
        self.library.bench_output_stride.argtypes = [ctypes.c_int, ctypes.c_int]
        self.library.bench_output_stride.restype = ctypes.c_uint64
        self.library.bench_output_offset.argtypes = [ctypes.c_int]
        self.library.bench_output_offset.restype = ctypes.c_uint64
        self.library.bench_checksum.argtypes = [ctypes.c_int]
        self.library.bench_checksum.restype = ctypes.c_double
        if self.call("bench_pointer_bits") != ctypes.sizeof(ctypes.c_void_p) * 8:
            raise ValueError("Incorrect native pointer width")

    def call(self, name, *args):
        return getattr(self.library, name)(*args)

    def init(self):
        self.library.bench_init()

    def run(self):
        self.library.bench_run()

    def free(self):
        self.library.bench_free()

    def read(self, offset, size):
        if not offset:
            raise ValueError("Null native output pointer")
        return ctypes.string_at(offset, size)


def tolerances(dtype, args):
    if dtype == 3:
        return 0.0, 0.0
    default = (1e-4, 1e-5) if dtype == 2 else (1e-8, 1e-9)
    return (args.rtol if args.rtol is not None else default[0],
            args.atol if args.atol is not None else default[1])


def check_scalar(actual, expected, dtype, args, label):
    if not math.isfinite(actual) or not math.isfinite(expected):
        if args.allow_nonfinite and (actual == expected or
                                     (math.isnan(actual) and math.isnan(expected))):
            return
        raise ValueError(f"{label}: non-finite values {actual!r}, {expected!r}")
    if dtype == 3:
        equal = actual == expected
    else:
        rtol, atol = tolerances(dtype, args)
        equal = math.isclose(actual, expected, rel_tol=rtol, abs_tol=atol)
    if not equal:
        raise ValueError(f"{label}: mismatch {actual!r} vs {expected!r}")


def compare(actual, expected, descriptors, args, label):
    if len(actual) != len(expected):
        raise ValueError(f"{label}: different output counts")
    for index, (left, right, desc) in enumerate(zip(actual, expected, descriptors["outputs"])):
        if len(left) != len(right):
            raise ValueError(f"{label}: output {index} sizes differ")
        for element, (x, y) in enumerate(zip(left, right)):
            check_scalar(x, y, desc["type"], args, f"{label} output {index} element {element}")


def checksums(bench, count):
    return [bench.call("bench_checksum", i) for i in range(count)]


def check_checksums(actual, expected, descriptors, args, label):
    for index, (x, y, desc) in enumerate(zip(actual, expected, descriptors["outputs"])):
        # Integer outputs accumulate into f64 in C; use a floating comparison
        # for the checksum, while individual integer elements are always exact.
        dtype = 1 if desc["type"] == 3 else desc["type"]
        check_scalar(x, y, dtype, args, f"{label} checksum {index}")


def stats(values):
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"median_ms": statistics.median(values), "q1_ms": q1, "q3_ms": q3,
            "min_ms": min(values), "max_ms": max(values)}


def run_benchmark(name, engine, args):
    benches = {}
    native = None
    counters = None
    try:
        if args.profile == "counters":
            counters = PerfCounters(args.events)
        for bits in args.bits:
            path = args.build_dir / f"{name}-remain-kernel-wasm{bits}.wasm"
            benches[bits] = WasmBenchmark(engine, path, bits, args.emit_code_dir)
        baselines, descriptors, sums = {}, {}, {}
        for bits, bench in benches.items():
            bench.init()
            descriptors[bits] = metadata(bench)
            bench.run()
            baselines[bits] = snapshot(bench, descriptors[bits])
            sums[bits] = checksums(bench, len(baselines[bits]))
            # Compare checksum against a host traversal in the same order.
            expected_sums = []
            for values in baselines[bits]:
                total = 0.0
                for value in values:
                    total += value
                expected_sums.append(total)
            check_checksums(sums[bits], expected_sums, descriptors[bits], args, f"wasm{bits} host read")
            compare(baselines[bits], baselines[bits], descriptors[bits], args, f"wasm{bits} finite check")
        cross_mode = len(benches) == 2
        first_bits = args.bits[0]
        if cross_mode:
            if descriptors[32] != descriptors[64]:
                raise ValueError("wasm32 and wasm64 parameters/output layouts differ")
            compare(baselines[64], baselines[32], descriptors[32], args, "wasm32/wasm64")
        desc = descriptors[first_bits]
        if args.native_dir is not None:
            native = NativeBenchmark(args.native_dir / f"lib{name}-remain-kernel.so")
            native.init()
            if metadata(native) != desc:
                raise ValueError("Native reference parameters/output layouts differ")
            native.run()
            reference = snapshot(native, desc)
            for bits in args.bits:
                compare(baselines[bits], reference, desc, args, f"wasm{bits}/native")
        # Release/reset lifecycle and every output must reproduce the baseline.
        for bits, bench in benches.items():
            bench.free()
            bench.free()
            bench.init()
            bench.run()
            compare(snapshot(bench, desc), baselines[bits], desc, args, f"wasm{bits} reinitialize")
        for _ in range(args.warmups):
            for bench in benches.values():
                bench.init()
                bench.run()

        records, times = [], {bits: [] for bits in args.bits}
        rng = random.Random(args.seed)
        for sample in range(args.samples):
            order = list(args.bits)
            rng.shuffle(order)
            for position, bits in enumerate(order):
                bench = benches[bits]
                bench.init()
                # Keep the original timing path unchanged when profiling is off.
                if counters is None:
                    start = perf_counter_ns()
                    bench.run()
                    elapsed_ms = (perf_counter_ns() - start) / 1e6
                    counter_sample = None
                else:
                    elapsed_ms, counter_sample = measure(bench, counters)
                check_checksums(checksums(bench, len(desc["outputs"])), sums[bits], desc,
                                args, f"wasm{bits} sample {sample}")
                times[bits].append(elapsed_ms)
                records.append({"sample": sample, "position": position,
                                "bits": bits, "elapsed_ms": elapsed_ms})
                if counter_sample is not None:
                    records[-1]["counters"] = counter_sample
        for bits, bench in benches.items():
            compare(snapshot(bench, desc), baselines[bits], desc, args, f"wasm{bits} final")
        summary = {bits: stats(values) for bits, values in times.items()}
        ratio = summary[64]["median_ms"] / summary[32]["median_ms"] if cross_mode else None
        nonfinite = [sum(not math.isfinite(v) for v in values) for values in baselines[first_bits]]
        hardware = ({bits: counter_summary(records, bits, args.events) for bits in args.bits}
                    if counters is not None else None)
        status = "PASS_NONFINITE_ALLOWED" if any(nonfinite) else "PASS"
        print(f"{name}: {status}; native_reference={native is not None}; cross_mode={cross_mode}; "
              f"elements={[len(v) for v in baselines[first_bits]]}; imports=0", flush=True)
        for bits in args.bits:
            s = summary[bits]
            print(f"  wasm{bits}: median={s['median_ms']:.6f} ms; "
                  f"IQR=[{s['q1_ms']:.6f}, {s['q3_ms']:.6f}] ms")
            if hardware is not None:
                h = hardware[bits]
                print(f"    PMU median counts/call: {h['median_counts_per_call']}; "
                      f"IPC={h['ipc']}; min_running_ratio={h['min_running_ratio']}", flush=True)
        if ratio is not None:
            print(f"  T64/T32={ratio:.6f}", flush=True)
        return {"benchmark": name, "status": status, "native_reference": native is not None,
                "cross_mode_validation": cross_mode, "profile": args.profile,
                "counter_summary": hardware,
                "code_artifacts": {bits: bench.code_artifact for bits, bench in benches.items()},
                "native_sha256": native.sha256 if native else None,
                "descriptors": desc, "nonfinite_elements": nonfinite,
                "module_sha256": {bits: bench.sha256 for bits, bench in benches.items()},
                "summary": summary, "median_time_ratio_64_over_32": ratio, "records": records}
    finally:
        if counters is not None:
            counters.close()
        for bench in benches.values():
            bench.free()
        if native is not None:
            native.free()


def discover():
    result = []
    for category in ("datamining", "linear-algebra", "medley", "stencils"):
        for source in (ROOT / category).rglob("*.c"):
            if "POLYBENCH_REMAIN_KERNEL" in source.read_text():
                result.append(source.stem)
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", nargs="+", required=True,
                        help="one or more names, or 'all' (all 30 benchmarks, including GEMM)")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--native-dir", type=Path,
                        help="require a native reference .so for every requested benchmark")
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--bits", choices=("32", "64", "both"), default="both",
                        help="Wasm pointer width(s); single-width runs omit the time ratio")
    parser.add_argument("--profile", choices=("none", "counters"), default="none",
                        help="optional Linux x86_64 thread-local hardware counters")
    parser.add_argument("--events", help="comma-separated counters: " + ",".join(PERF_EVENTS))
    parser.add_argument("--cpu", type=int, help="bind to one allowed Linux logical CPU before engine creation")
    parser.add_argument("--emit-code-dir", type=Path,
                        help="save the executed compiled Module as DIR/name/wasmBITS/module.cwasm; refuses overwrite")
    parser.add_argument("--jit-profile", choices=("none", "jitdump"), default="none",
                        help="JIT code mapping for external perf; independent of hardware counters")
    parser.add_argument("--rtol", type=float)
    parser.add_argument("--atol", type=float)
    parser.add_argument("--allow-nonfinite", action="store_true",
                        help="explicitly allow matching NaN/Inf; marks results, not a numerical pass")
    parser.add_argument("--json", type=Path, help="save raw samples, metadata and validation results")
    args = parser.parse_args()
    args.bits = [32, 64] if args.bits == "both" else [int(args.bits)]
    if args.events is not None and args.profile != "counters":
        parser.error("--events requires --profile counters")
    args.events = list(PERF_EVENTS) if args.events is None else [e.strip() for e in args.events.split(",")]
    if any(e not in PERF_EVENTS for e in args.events) or len(set(args.events)) != len(args.events):
        parser.error("Unknown, empty or duplicate event. Available: " + ",".join(PERF_EVENTS))
    if args.cpu is not None:
        if not hasattr(os, "sched_setaffinity"):
            parser.error("--cpu requires Linux CPU affinity support")
        if args.cpu not in os.sched_getaffinity(0):
            parser.error("--cpu must be in the current thread's allowed CPU set")
        try:
            os.sched_setaffinity(0, {args.cpu})
        except OSError as error:
            parser.error(str(error))
    if args.warmups < 1 or args.samples < 5:
        parser.error("Use at least one warmup and five samples")
    if any(v is not None and (not math.isfinite(v) or v < 0) for v in (args.rtol, args.atol)):
        parser.error("Tolerances must be finite and nonnegative")
    available = discover()
    names = available if args.benchmark == ["all"] else args.benchmark
    if any(name not in available for name in names) or len(names) != len(set(names)):
        parser.error("Unknown or duplicate benchmark. Available: " + ", ".join(available))
    config = wasmtime.Config()
    config.wasm_memory64 = True
    config.strategy = "cranelift"
    config.cranelift_opt_level = "speed"
    config.profiler = args.jit_profile
    engine = wasmtime.Engine(config)
    report = {"wasmtime_python_version": version("wasmtime"),
              "python_version": platform.python_version(), "system": platform.system(),
              "release": platform.release(), "machine": platform.machine(),
              "processor": platform.processor(), "samples": args.samples,
              "warmups": args.warmups, "seed": args.seed,
              "bits": args.bits,
              "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
              "profiling": {"mode": args.profile,
                            "events": args.events if args.profile == "counters" else [],
                            "scope": ("current thread, user space; kernel call plus Python/FFI/"
                                      "Wasmtime, clock and counter-control boundary overhead"
                                      if args.profile == "counters" else None),
                            "multiplex_policy": "reject; no scaling",
                            "timing_with_profiling": args.profile != "none" or args.jit_profile != "none"},
              "rtol_override": args.rtol, "atol_override": args.atol,
              "allow_nonfinite": args.allow_nonfinite,
              "runtime": {"memory64_enabled_for_both": True, "compiler": "cranelift", "opt_level": "speed",
                          "jit_profile": args.jit_profile},
              "cache_policy": "reset all arrays before timing; no explicit cache flush",
              "timed_region": "one Python/Wasmtime call to the original kernel, includes call overhead",
              "results": []}
    print(f"Wasmtime Python {version('wasmtime')}; {platform.system()} {platform.machine()}", flush=True)
    print("Cache policy: reset all arrays; no explicit cache flush", flush=True)
    if args.profile == "counters":
        print("Profiling: user-space current-thread counters around bench_run; includes host/clock "
              "and control-boundary overhead. Timings are profiling-enabled measurements.", flush=True)
    if args.native_dir is None:
        print("Validation: " + ("cross-mode only" if len(args.bits) == 2 else
              "single-width self-consistency only; no cross-mode comparison") +
              "; no native reference was requested", flush=True)
    for name in names:
        print(f"Validating {name} ...", flush=True)
        try:
            result = run_benchmark(name, engine, args)
        except (ValueError, OSError, KeyError, AttributeError,
                wasmtime.WasmtimeError, wasmtime.Trap) as error:
            result = {"benchmark": name, "status": "FAIL", "error": str(error)}
            print(f"{name}: FAIL: {error}", file=sys.stderr, flush=True)
        report["results"].append(result)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if any(result["status"] == "FAIL" for result in report["results"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
