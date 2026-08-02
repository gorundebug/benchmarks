#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = Path(__file__).resolve().parent
ARTIFACTS = BENCHMARK_DIR / ".artifacts"
COMMON_COMPOSE = BENCHMARK_DIR / "compose.common.yml"


@dataclass(frozen=True)
class Language:
    name: str
    example: Path
    overlay: Path


LANGUAGES = (
    Language("go", ROOT / "goexample", BENCHMARK_DIR / "compose.go.yml"),
    Language("cpp", ROOT / "cppexample", BENCHMARK_DIR / "compose.cpp.yml"),
    Language("python", ROOT / "pyexample", BENCHMARK_DIR / "compose.python.yml"),
    Language("rust", ROOT / "rustexample", BENCHMARK_DIR / "compose.rust.yml"),
)


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=check,
        text=True,
        capture_output=capture,
    )


def compose_command(language: Language, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        f"servicelib-example-benchmark-{language.name}",
        "--project-directory",
        str(language.example),
        "--file",
        str(language.example / "docker-compose.yml"),
        "--file",
        str(COMMON_COMPOSE),
        "--file",
        str(language.overlay),
        *args,
    ]


def environment(args: argparse.Namespace, language: Language) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "BENCHMARK_ARTIFACTS_DIR": str(ARTIFACTS),
            "BENCHMARK_CPP_CONFIG_DIR": str(ARTIFACTS / "cpp-config"),
            "BENCHMARK_DIR": str(BENCHMARK_DIR),
            "BENCHMARK_DURATION": args.duration,
            "BENCHMARK_LOADGEN_CORES": str(args.loadgen_cores),
            "BENCHMARK_RESULT_FILE": "/results/unused.json",
            "BENCHMARK_SERVICE_CORES": str(args.cores),
            "BENCHMARK_VUS": str(args.vus),
        }
    )
    if language.name == "cpp":
        env["COMPOSE_PROJECT_NAME"] = "cppexample"
        env["SERVICELIB_SOURCE_CONTEXT"] = str(ROOT / "cppservicelib")
        env["USERVER_LTO"] = "ON"
    elif language.name == "python":
        env["PYSERVICELIB_SOURCE_CONTEXT"] = str(ROOT / "pyservicelib")
    return env


def build(language: Language, env: dict[str, str]) -> None:
    if language.name == "go":
        run(["make", "docker-build"], cwd=language.example, env=env)
    elif language.name == "cpp":
        run(
            ["./scripts/build.generated.sh", "docker-release"],
            cwd=language.example,
            env=env,
        )
    else:
        run(
            compose_command(language, "build", "inventoryservice", "orderservice"),
            cwd=language.example,
            env=env,
        )


# userver splits work across several independently-pooled task processors,
# unlike Go's single GOMAXPROCS-sized scheduler. Setting every pool's
# worker_threads to `cores` (as before) oversubscribes the cgroup CPU quota
# 3x over (main + fs + grpc-blocking, each sized at `cores`). For parity with
# Go: only main-task-processor (the CPU-bound pool that actually runs request
# handling fibers) gets `cores` threads, matching GOMAXPROCS exactly. The
# blocking/I/O-only pools (fs-task-processor, grpc-blocking-task-processor,
# and the gRPC server's completion queues) get a minimal fixed size, the same
# role Go's runtime fills with extra OS threads for blocking syscalls that
# don't count against GOMAXPROCS.
AUX_TASK_PROCESSOR_THREADS = 1


def _set_worker_threads(static_config: str, processor: str, threads: int) -> str:
    pattern = re.compile(rf"(?m)^(\s*{re.escape(processor)}:\n\s+worker_threads:)\s+\d+\s*$")
    new_config, count = pattern.subn(rf"\1 {threads}", static_config)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one worker_threads under {processor}, found {count}"
        )
    return new_config


def prepare_cpp_configs(service_cores: int) -> None:
    output = ARTIFACTS / "cpp-config"
    output.mkdir(parents=True, exist_ok=True)
    for service, prefix, port, grpc_port in (
        ("inventoryservice", "inventoryService", 9092, 9202),
        ("orderservice", "orderService", 9091, 9201),
    ):
        static_config = (
            ROOT / "cppexample" / service / "static_config.yaml"
        ).read_text()
        # The generated binary registers userver's OTLP components, therefore
        # they must remain present in static_config. Route both sinks to the
        # local logger so no telemetry is exported during the benchmark.
        static_config = static_config.replace(
            "        tracing: otlp", "        tracing: default"
        )
        # "none" fully suppresses log output (userver logging::Level::kNone).
        # servicelib bridges its own logging through userver's LOG_* macros
        # too, so this also silences servicelib's warnings/errors -- fine
        # here since a non-zero error rate already fails the benchmark on
        # its own, independent of whether we can see a log line about it.
        static_config = static_config.replace("level: info", "level: none")
        static_config = _set_worker_threads(static_config, "main-task-processor", service_cores)
        static_config = _set_worker_threads(
            static_config, "fs-task-processor", AUX_TASK_PROCESSOR_THREADS
        )
        static_config = _set_worker_threads(
            static_config, "grpc-blocking-task-processor", AUX_TASK_PROCESSOR_THREADS
        )
        static_config, completion_queue_count = re.subn(
            r"(?m)^(\s+completion-queue-count:)\s+\d+\s*$",
            rf"\1 {AUX_TASK_PROCESSOR_THREADS}",
            static_config,
        )
        expected_completion_queues = 1 if "grpc-server:" in static_config else 0
        if completion_queue_count != expected_completion_queues:
            raise RuntimeError(
                f"{service} must define exactly {expected_completion_queues} "
                f"gRPC completion-queue counts, found {completion_queue_count}"
            )
        (output / f"{service}.static_config.yaml").write_text(static_config)

        values = {
            f"{prefix}ConfigOverridePath": "config/overrides.integration.generated.yaml",
            f"{prefix}Environment": "",
            f"{prefix}GrpcHost": "0.0.0.0",
            f"{prefix}GrpcPort": grpc_port,
            f"{prefix}HttpHost": "0.0.0.0",
            f"{prefix}HttpPort": port,
            f"{prefix}OtlpEndpoint": "disabled:4317",
        }
        if service == "inventoryservice":
            values["inventoryServiceApiAddress"] = "dns:///inventoryservice:9202"
        else:
            values["inventoryServiceApiAddress"] = "dns:///inventoryservice:9202"
        text = "".join(
            f"{key}: {json.dumps(value) if isinstance(value, str) else value}\n"
            for key, value in values.items()
        )
        (output / f"{service}.config_vars.yaml").write_text(text)


def raise_max_map_count(value: int) -> None:
    """vm.max_map_count is a global, non-namespaced host sysctl -- Docker
    does not allow setting it per-container (`--sysctl vm.max_map_count=...`
    is rejected outright). userver mmaps a stack per coroutine, and this
    example's pipeline fans out into far more coroutines than VUs (e.g.
    ~130k coroutines observed at VUS=512), so high-concurrency runs can
    exhaust the default host limit and make every request fail with
    "Failed to allocate a coroutine (ENOMEM)". Raise it once via a
    throwaway --privileged container before the benchmark starts; this
    persists host/VM-wide across all subsequent containers."""
    subprocess.run(
        [
            "docker", "run", "--rm", "--privileged", "debian:bookworm-slim",
            "sh", "-c", f"echo {value} > /proc/sys/vm/max_map_count",
        ],
        check=True,
    )


def wait_for_service(
    language: Language, service: str, url: str, env: dict[str, str]
) -> None:
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.5)
    logs = run(
        compose_command(language, "logs", "--no-color", "--tail", "100", service),
        cwd=language.example,
        env=env,
        capture=True,
        check=False,
    )
    print(logs.stdout + logs.stderr, file=sys.stderr)
    raise RuntimeError(f"{language.name} {service} did not become ready: {last_error}")


def load(
    language: Language,
    env: dict[str, str],
    *,
    duration: str,
    result_name: str,
) -> dict[str, Any]:
    result_path = ARTIFACTS / result_name
    result_path.unlink(missing_ok=True)
    load_env = {
        **env,
        "BENCHMARK_DURATION": duration,
        "BENCHMARK_RESULT_FILE": f"/results/{result_name}",
    }
    run(
        compose_command(
            language,
            "--profile",
            "benchmark",
            "run",
            "--rm",
            "--no-deps",
            "loadgen",
        ),
        cwd=language.example,
        env=load_env,
    )
    if not result_path.exists():
        raise RuntimeError(f"k6 did not create {result_path}")
    return json.loads(result_path.read_text())


def benchmark_language(
    language: Language, args: argparse.Namespace
) -> list[dict[str, Any]]:
    env = environment(args, language)
    try:
        run(
            compose_command(
                language, "up", "--detach", "--no-deps", "inventoryservice"
            ),
            cwd=language.example,
            env=env,
        )
        wait_for_service(
            language,
            "inventoryservice",
            "http://localhost:9092/status/data",
            env,
        )
        run(
            compose_command(language, "up", "--detach", "--no-deps", "orderservice"),
            cwd=language.example,
            env=env,
        )
        wait_for_service(
            language,
            "orderservice",
            "http://localhost:9091/status/data",
            env,
        )

        if args.warmup != "0" and args.warmup != "0s":
            load(
                language,
                env,
                duration=args.warmup,
                result_name=f"{language.name}.warmup.json",
            )

        results = []
        for index in range(1, args.runs + 1):
            result = load(
                language,
                env,
                duration=args.duration,
                result_name=f"{language.name}.run-{index}.json",
            )
            if result["error_rate"] > args.max_error_rate:
                raise RuntimeError(
                    f"{language.name} run {index} error rate "
                    f"{result['error_rate']:.6f} exceeds {args.max_error_rate:.6f}"
                )
            results.append(result)
        return results
    finally:
        run(
            compose_command(language, "down", "--volumes", "--remove-orphans"),
            cwd=language.example,
            env=env,
            check=False,
        )


def median(values: list[float | int]) -> float:
    return float(statistics.median(values))


def aggregate(
    language: Language, runs: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any]:
    return {
        "language": language.name,
        "service_cores": args.cores,
        "loadgen_cores": args.loadgen_cores,
        "vus": args.vus,
        "runs": len(runs),
        "duration": args.duration,
        "requests_total": sum(run["request_count"] for run in runs),
        "requests_per_second": median([run["requests_per_second"] for run in runs]),
        "error_rate": median([run["error_rate"] for run in runs]),
        "latency_ms": {
            key: median([run["latency_ms"][key] for run in runs])
            for key in ("avg", "p50", "p90", "p95", "p99", "max")
        },
    }


def write_results(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "architecture": platform.machine(),
            "os": platform.platform(),
        },
        "scenario": "process_order_out_of_stock",
        "parameters": {
            "build": "reused" if args.skip_build else "release",
            "service_cores": args.cores,
            "loadgen_cores": args.loadgen_cores,
            "vus": args.vus,
            "duration": args.duration,
            "warmup": args.warmup,
            "runs": args.runs,
        },
        "results": results,
    }
    (ARTIFACTS / "results.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )

    columns = [
        "language",
        "service_cores",
        "loadgen_cores",
        "vus",
        "runs",
        "requests_total",
        "requests_per_second",
        "error_rate_percent",
        "latency_avg_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "latency_max_ms",
    ]
    rows = []
    for result in results:
        latency = result["latency_ms"]
        rows.append(
            {
                "language": result["language"],
                "service_cores": result["service_cores"],
                "loadgen_cores": result["loadgen_cores"],
                "vus": result["vus"],
                "runs": result["runs"],
                "requests_total": result["requests_total"],
                "requests_per_second": round(result["requests_per_second"], 2),
                "error_rate_percent": round(result["error_rate"] * 100, 6),
                "latency_avg_ms": round(latency["avg"], 3),
                "latency_p50_ms": round(latency["p50"], 3),
                "latency_p95_ms": round(latency["p95"], 3),
                "latency_p99_ms": round(latency["p99"], 3),
                "latency_max_ms": round(latency["max"], 3),
            }
        )
    with (ARTIFACTS / "results.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    header = (
        "| Language | Cores/service | VUs | Runs | Requests/s | "
        "Errors | Avg ms | p50 ms | p95 ms | p99 ms | Max ms |\n"
    )
    separator = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    table_rows = []
    for row in rows:
        table_rows.append(
            f"| {row['language']} | {row['service_cores']} | {row['vus']} | "
            f"{row['runs']} | {row['requests_per_second']:.2f} | "
            f"{row['error_rate_percent']:.4f}% | {row['latency_avg_ms']:.3f} | "
            f"{row['latency_p50_ms']:.3f} | {row['latency_p95_ms']:.3f} | "
            f"{row['latency_p99_ms']:.3f} | {row['latency_max_ms']:.3f} |"
        )
    (ARTIFACTS / "results.md").write_text(
        "# ServiceLib example benchmark\n\n"
        f"- Scenario: `process_order_out_of_stock`\n"
        f"- Service CPU quota: `{args.cores}` cores per container\n"
        f"- Load generator CPU quota: `{args.loadgen_cores}` cores\n"
        f"- Virtual users: `{args.vus}`\n"
        f"- Warm-up: `{args.warmup}`\n"
        f"- Measurement: `{args.runs} × {args.duration}`\n\n"
        + header
        + separator
        + "\n".join(table_rows)
        + "\n"
    )


def clean() -> None:
    for language in LANGUAGES:
        args = argparse.Namespace(
            cores=1,
            loadgen_cores=1,
            duration="1s",
            vus=1,
        )
        run(
            compose_command(language, "down", "--volumes", "--remove-orphans"),
            cwd=language.example,
            env=environment(args, language),
            check=False,
        )
    shutil.rmtree(ARTIFACTS, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark equivalent ServiceLib examples in four languages"
    )
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--loadgen-cores", type=int, default=2)
    parser.add_argument("--vus", type=int, default=32)
    parser.add_argument("--duration", default="20s")
    parser.add_argument("--warmup", default="5s")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-error-rate", type=float, default=0.001)
    parser.add_argument(
        "--max-map-count",
        type=int,
        default=1_048_576,
        help="vm.max_map_count to set host/VM-wide before running (0 to leave it untouched)",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--language",
        action="append",
        choices=[language.name for language in LANGUAGES],
    )
    args = parser.parse_args()

    if args.clean:
        clean()
        return 0
    if args.cores <= 0 or args.loadgen_cores <= 0:
        parser.error("CPU core counts must be positive integers")
    if args.vus <= 0 or args.runs <= 0:
        parser.error("VUs and runs must be positive integers")
    if args.max_map_count < 0:
        parser.error("--max-map-count must not be negative")

    selected = [
        language
        for language in LANGUAGES
        if not args.language or language.name in args.language
    ]
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    prepare_cpp_configs(args.cores)
    if args.max_map_count:
        raise_max_map_count(args.max_map_count)

    if not args.skip_build:
        for language in selected:
            build(language, environment(args, language))
    if args.build_only:
        return 0

    results = []
    for language in selected:
        print(f"\n=== {language.name} ===", flush=True)
        runs = benchmark_language(language, args)
        results.append(aggregate(language, runs, args))
        write_results(results, args)

    print("\n" + (ARTIFACTS / "results.md").read_text(), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
