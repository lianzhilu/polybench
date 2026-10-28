#!/usr/bin/env python3
"""Export compiled PolyBench code and optionally collect Linux perf JIT samples."""

import argparse
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
from time import monotonic

from run_wasmtime import ROOT, discover

RUNNER = Path(__file__).resolve().with_name("run_wasmtime.py")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_path(name):
    path = shutil.which(name)
    if path is None:
        raise ValueError(f"Required tool not found: {name}")
    return str(Path(path).absolute())


def sample_plan(args, name, bits, wasm_hash, baseline):
    if args.samples is not None:
        return {"samples": args.samples, "source": "explicit --samples", "capped": False}
    if args.mode == "assembly":
        return {"samples": 5, "source": "assembly validation", "capped": False}
    if baseline is None:
        return {"samples": 1001, "source": "default (no baseline supplied)", "capped": False}
    matches = [r for r in baseline.get("results", []) if r.get("benchmark") == name]
    if len(matches) != 1 or matches[0].get("status") != "PASS":
        raise ValueError(f"Baseline needs exactly one PASS record for {name}")
    record = matches[0]
    if record.get("module_sha256", {}).get(str(bits)) != wasm_hash:
        raise ValueError(f"Baseline wasm{bits} hash differs; use matching results or explicit --samples")
    median = record["summary"][str(bits)]["median_ms"]
    if not isinstance(median, (int, float)) or not math.isfinite(median) or median <= 0:
        raise ValueError("Baseline median must be positive and finite")
    requested = max(5, math.ceil(args.target_kernel_seconds * 1000 / median))
    count = min(requested, args.max_samples)
    return {"samples": count, "source": "baseline median", "baseline_median_ms": median,
            "target_kernel_seconds": args.target_kernel_seconds,
            "estimated_kernel_seconds": count * median / 1000,
            "requested_samples": requested, "capped": count < requested}


def run_command(argv, directory, output, job, timeout):
    """No shell; preserve diagnostics and kill the entire child group on timeout."""
    command = [str(a) for a in argv]
    step = {"argv": command, "stdout": output, "stderr": "command.log"}
    job["commands"].append(step)
    started = monotonic()
    with (directory / "command.log").open("a", encoding="utf-8") as log, \
            (directory / output).open("w", encoding="utf-8") as stdout:
        log.write("\n$ " + shlex.join(command) + "\n")
        log.flush()
        process = subprocess.Popen(command, cwd=directory, stdout=stdout, stderr=log,
                                   start_new_session=True)
        try:
            step["returncode"] = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            step["returncode"] = process.returncode
            step["interrupted_or_timed_out"] = True
            raise
        finally:
            step["elapsed_seconds"] = monotonic() - started
    if step["returncode"] != 0:
        raise ValueError(f"{Path(command[0]).name} exited {step['returncode']}; see {directory / 'command.log'}")


def elf_machine(path):
    with path.open("rb") as source:
        header = source.read(20)
    if len(header) != 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
        raise ValueError("Compiled module is not a little-endian ELF64 artifact")
    return struct.unpack_from("<H", header, 18)[0]


def disassemble(args, objdump, directory, job):
    machine = elf_machine(directory / "module.cwasm")
    job["elf_machine"] = machine
    expected = {"x86_64": 62, "AMD64": 62, "arm64": 183, "aarch64": 183}.get(platform.machine())
    if machine != expected:
        raise ValueError(f"Unexpected ELF machine {machine} for host {platform.machine()}")
    style = ["-M", "intel"] if machine == 62 else []
    # -z retains zero bytes; -D walks the entire .text section, including cold
    # code with no samples. Embedded data/padding can also be decoded as code.
    run_command([objdump, "-D", "-z", "--section=.text", *style, "module.cwasm"],
                directory, "full.asm", job, args.timeout)
    if not re.search(r"^\s*[0-9a-fA-F]+:\s+\S", (directory / "full.asm").read_text(), re.M):
        raise ValueError("Disassembler produced no instructions; inspect full.asm and command.log")
    run_command([objdump, "-t", "module.cwasm"], directory, "symbols.txt", job, args.timeout)


def collect_one(args, tools, name, bits, baseline):
    directory = args.output_dir / name / f"wasm{bits}"
    directory.mkdir(parents=True, exist_ok=False)
    job = {"benchmark": name, "bits": bits, "directory": str(directory),
           "status": "RUNNING", "commands": [], "mode": args.mode}
    started = monotonic()
    try:
        wasm = args.build_dir / f"{name}-remain-kernel-wasm{bits}.wasm"
        job["wasm_sha256"] = sha256(wasm)
        plan = sample_plan(args, name, bits, job["wasm_sha256"], baseline)
        job["sample_plan"] = plan
        if plan["capped"]:
            print(f"  Sample cap reached for {name}/wasm{bits}; see manifest estimate", flush=True)
        command = [sys.executable, str(RUNNER), "--benchmark", name, "--bits", str(bits),
                   "--profile", "none", "--warmups", str(args.warmups),
                   "--samples", str(plan["samples"]), "--build-dir", str(args.build_dir),
                   "--emit-code-dir", str(args.output_dir), "--json", "run-results.json",
                   "--jit-profile", "jitdump" if args.mode == "sample" else "none"]
        if args.cpu is not None:
            command += ["--cpu", str(args.cpu)]
        if args.native_dir is not None:
            command += ["--native-dir", str(args.native_dir)]
        if args.mode == "sample":
            command = [tools["perf"], "record", "-e", "cycles:u", "-F", str(args.frequency),
                       "-k", "mono", "-o", "perf.data", "--", *command]
        run_command(command, directory, "run.log", job, args.timeout)
        result = json.loads((directory / "run-results.json").read_text())
        record = result["results"][0]
        if record["status"] != "PASS" or record["module_sha256"][str(bits)] != job["wasm_sha256"]:
            raise ValueError("Runner failed validation or input module changed during collection")
        job["runtime"] = result["runtime"]
        job["cpu_affinity"] = result["cpu_affinity"]
        job["native_reference"] = record["native_reference"]
        job["compiled_sha256"] = sha256(directory / "module.cwasm")
        if job["compiled_sha256"] != record["code_artifacts"][str(bits)]["sha256"]:
            raise ValueError("Serialized module hash differs from runner report")
        job["measured_kernel_call_seconds"] = sum(r["elapsed_ms"] for r in record["records"]) / 1000
        disassemble(args, tools["objdump"], directory, job)
        job["assembly_status"] = "PASS"
        if args.mode == "sample":
            perf = tools["perf"]
            run_command([perf, "inject", "--jit", "-i", "perf.data", "-o", "perf.jit.data"],
                        directory, "inject.log", job, args.timeout)
            # Without these ELF images, successful perf commands can still hide
            # an unusable JIT mapping behind [unknown] symbols.
            jitted = sorted(directory.glob("jitted-*.so"))
            if not jitted:
                raise ValueError("perf inject produced no jitted-*.so; JIT mapping is incomplete")
            run_command([perf, "report", "--stdio", "--percent-limit", "0", "-i", "perf.jit.data",
                         "--sort", "dso,symbol"], directory, "report.txt", job, args.timeout)
            run_command([perf, "annotate", "--stdio", "--no-source", "-M", "intel", "-i",
                         "perf.jit.data", "--dsos", ",".join(p.name for p in jitted),
                         "--objdump", tools["objdump"]], directory, "annotated.asm", job, args.timeout)
            if not re.search(r"[0-9a-fA-F]+:\s+\S", (directory / "annotated.asm").read_text()):
                raise ValueError("No annotated samples; increase sampling duration or inspect command.log")
        job["status"] = "PASS"
    except KeyboardInterrupt:
        job["status"] = "INTERRUPTED"
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        job["status"], job["error"] = "FAIL", str(error)
    finally:
        job["elapsed_seconds"] = monotonic() - started
        job["files"] = sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file())
        write_json(directory / "job.json", job)
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", nargs="+", required=True)
    parser.add_argument("--bits", choices=("both", "32", "64"), default="both")
    parser.add_argument("--mode", choices=("sample", "assembly"), default="sample")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--native-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True, help="new experiment directory; must not exist")
    parser.add_argument("--baseline-results", type=Path, help="matching prior results used to estimate sample counts")
    parser.add_argument("--target-kernel-seconds", type=float, default=10)
    parser.add_argument("--max-samples", type=int, default=100000)
    parser.add_argument("--samples", type=int, help="explicit count; overrides baseline estimate")
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--frequency", type=int, default=997, help="requested perf sampling frequency in Hz")
    parser.add_argument("--timeout", type=float, default=600, help="maximum seconds for each external command")
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--objdump", default="objdump", help="GNU objdump or compatible llvm-objdump")
    args = parser.parse_args()
    if (args.warmups < 1 or args.max_samples < 5 or args.frequency < 1 or
            (args.samples is not None and args.samples < 5)):
        parser.error("Use warmups >= 1, samples/max-samples >= 5 and frequency >= 1")
    if any(not math.isfinite(x) or x <= 0 for x in (args.target_kernel_seconds, args.timeout)):
        parser.error("Durations must be positive and finite")
    if args.mode == "sample" and (platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64")):
        parser.error("Sampling mode requires Linux x86_64; assembly mode exports the current host architecture")
    if args.cpu is not None:
        if not hasattr(os, "sched_getaffinity") or args.cpu not in os.sched_getaffinity(0):
            parser.error("--cpu requires an allowed Linux logical CPU")
    names = discover() if args.benchmark == ["all"] else args.benchmark
    if len(set(names)) != len(names) or any(n not in discover() for n in names):
        parser.error("Unknown or duplicate benchmark")
    args.build_dir = args.build_dir.resolve()
    args.native_dir = args.native_dir.resolve() if args.native_dir else None
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.exists():
        parser.error("--output-dir already exists; choose a new experiment directory")
    baseline = None
    try:
        tools = {"objdump": tool_path(args.objdump)}
        if args.mode == "sample":
            tools["perf"] = tool_path("perf")
        if args.baseline_results:
            baseline = json.loads(args.baseline_results.read_text())
            if (not isinstance(baseline, dict) or not isinstance(baseline.get("results"), list)
                    or any(not isinstance(r, dict) for r in baseline["results"])):
                raise ValueError("Baseline must contain a results list")
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    manifest = {"mode": args.mode, "system": platform.system(), "machine": platform.machine(),
                "release": platform.release(), "python_version": platform.python_version(),
                "wasmtime_python_version": version("wasmtime"), "cpu_requested": args.cpu,
                "runner_sha256": sha256(RUNNER), "tools": tools,
                "sampling_scope": ("whole process, including validation and warmups; filter JIT kernel functions"
                                   if args.mode == "sample" else None),
                "baseline": ({"path": str(args.baseline_results.resolve()),
                              "sha256": sha256(args.baseline_results)} if args.baseline_results else None),
                "options": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                "status": "RUNNING", "jobs": []}
    if Path("/proc/cpuinfo").exists():
        (args.output_dir / "cpuinfo.txt").write_text(Path("/proc/cpuinfo").read_text())
    manifest_path = args.output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    try:
        for label, tool in tools.items():
            result = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise ValueError(f"{label} --version failed: {result.stderr.strip()}")
            manifest.setdefault("tool_versions", {})[label] = result.stdout.strip() or result.stderr.strip()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        manifest.update(status="FAIL", error=str(error))
        write_json(manifest_path, manifest)
        print(str(error), file=sys.stderr)
        return 1
    bits_list = [32, 64] if args.bits == "both" else [int(args.bits)]
    try:
        for name in names:
            for bits in bits_list:
                print(f"Collecting {name}/wasm{bits} ({args.mode}) ...", flush=True)
                job = collect_one(args, tools, name, bits, baseline)
                manifest["jobs"].append(job)
                write_json(manifest_path, manifest)
                print(f"  {job['status']}" + (f": {job['error']}" if "error" in job else ""), flush=True)
    except KeyboardInterrupt:
        manifest["status"] = "INTERRUPTED"
        write_json(manifest_path, manifest)
        return 130
    manifest["status"] = "FAIL" if any(j["status"] != "PASS" for j in manifest["jobs"]) else "PASS"
    write_json(manifest_path, manifest)
    print(f"{manifest['status']}: {manifest_path}", flush=True)
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
