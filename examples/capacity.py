#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run as benchmark


@dataclass(frozen=True)
class Limits:
    min_achieved_ratio: float
    max_error_rate: float
    max_dropped_rate: float
    max_p95_ms: float


class DockerStatsSampler:
    def __init__(self, project: str, service_cores: int, loadgen_cores: int) -> None:
        self._project = project
        self._quotas = {
            "inventoryservice": service_cores,
            "orderservice": service_cores,
            "loadgen": loadgen_cores,
        }
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> DockerStatsSampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                containers = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "--filter",
                        f"label=com.docker.compose.project={self._project}",
                        "--format",
                        '{{.ID}}|{{.Label "com.docker.compose.service"}}',
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                names_by_id = {}
                for row in containers:
                    container_id, separator, service = row.partition("|")
                    if separator and service in self._quotas:
                        names_by_id[container_id] = service
                if names_by_id:
                    output = subprocess.run(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{.ID}}|{{.CPUPerc}}",
                            *names_by_id,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                    for row in output.splitlines():
                        container_id, separator, percent = row.partition("|")
                        if not separator:
                            continue
                        service = next(
                            (
                                name
                                for known_id, name in names_by_id.items()
                                if known_id.startswith(container_id)
                                or container_id.startswith(known_id)
                            ),
                            None,
                        )
                        if service is not None:
                            self._samples[service].append(
                                float(percent.rstrip("%"))
                            )
            except (OSError, ValueError, subprocess.SubprocessError):
                # CPU observations are diagnostic and must not invalidate a run.
                pass
            self._stop.wait(0.5)

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for service, quota in self._quotas.items():
            samples = self._samples.get(service, [])
            if not samples:
                result[service] = {"samples": 0}
                continue
            average = statistics.fmean(samples)
            maximum = max(samples)
            result[service] = {
                "samples": len(samples),
                "cpu_percent_avg": average,
                "cpu_percent_max": maximum,
                "quota_utilization_avg": average / (quota * 100),
                "quota_utilization_max": maximum / (quota * 100),
            }
        return result


def project_name(language: benchmark.Language) -> str:
    return f"servicelib-example-benchmark-{language.name}"


def is_sustainable(result: dict[str, Any], rate: int, limits: Limits) -> bool:
    return (
        result["achieved_ratio"] >= limits.min_achieved_ratio
        and result["error_rate"] <= limits.max_error_rate
        and result["dropped_rate"] <= limits.max_dropped_rate
        and result["latency_ms"]["p95"] <= limits.max_p95_ms
    )


def failure_reasons(
    result: dict[str, Any], rate: int, limits: Limits
) -> list[str]:
    reasons = []
    achieved_ratio = result["achieved_ratio"]
    if achieved_ratio < limits.min_achieved_ratio:
        reasons.append(
            f"achieved {achieved_ratio:.3f} < {limits.min_achieved_ratio:.3f}"
        )
    if result["error_rate"] > limits.max_error_rate:
        reasons.append(
            f"errors {result['error_rate']:.6f} > {limits.max_error_rate:.6f}"
        )
    if result["dropped_rate"] > limits.max_dropped_rate:
        reasons.append(
            f"dropped {result['dropped_rate']:.6f} > "
            f"{limits.max_dropped_rate:.6f}"
        )
    if result["latency_ms"]["p95"] > limits.max_p95_ms:
        reasons.append(
            f"p95 {result['latency_ms']['p95']:.3f}ms > "
            f"{limits.max_p95_ms:.3f}ms"
        )
    return reasons


def measure(
    language: benchmark.Language,
    env: dict[str, str],
    args: argparse.Namespace,
    limits: Limits,
    rate: int,
    sequence: int,
    phase: str,
) -> dict[str, Any]:
    result_name = (
        f"capacity.{language.name}.{sequence:02d}.{phase}.{rate}rps.json"
    )
    rate_env = {
        **env,
        "BENCHMARK_MODE": "arrival-rate",
        "BENCHMARK_RATE": str(rate),
        "BENCHMARK_PRE_ALLOCATED_VUS": str(args.preallocated_vus),
        "BENCHMARK_MAX_VUS": str(args.max_vus),
    }
    with DockerStatsSampler(
        project_name(language), args.cores, args.loadgen_cores
    ) as sampler:
        result = benchmark.load(
            language,
            rate_env,
            duration=args.duration,
            result_name=result_name,
        )
    result["target_rate"] = rate
    # http_reqs.rate includes the short drain period after the scheduling
    # window and therefore understates an arrival rate in short runs. k6's
    # dropped_iterations directly reports work it could not start on time.
    result["achieved_ratio"] = 1.0 - result["dropped_rate"]
    result["achieved_rate"] = rate * result["achieved_ratio"]
    result["sustainable"] = is_sustainable(result, rate, limits)
    result["failure_reasons"] = failure_reasons(result, rate, limits)
    result["phase"] = phase
    result["cpu"] = sampler.summary()
    verdict = "PASS" if result["sustainable"] else "FAIL"
    print(
        f"{language.name}: {rate}/s {verdict}, "
        f"actual={result['requests_per_second']:.1f}/s, "
        f"p95={result['latency_ms']['p95']:.2f}ms, "
        f"dropped={result['dropped_rate'] * 100:.3f}%, "
        f"errors={result['error_rate'] * 100:.3f}%",
        flush=True,
    )
    return result


def start_services(
    language: benchmark.Language, env: dict[str, str]
) -> None:
    benchmark.run(
        benchmark.compose_command(
            language, "up", "--detach", "--no-deps", "inventoryservice"
        ),
        cwd=language.example,
        env=env,
    )
    benchmark.wait_for_service(
        language,
        "inventoryservice",
        "http://localhost:9092/status/data",
        env,
    )
    benchmark.run(
        benchmark.compose_command(
            language, "up", "--detach", "--no-deps", "orderservice"
        ),
        cwd=language.example,
        env=env,
    )
    benchmark.wait_for_service(
        language,
        "orderservice",
        "http://localhost:9091/status/data",
        env,
    )


def find_capacity(
    language: benchmark.Language,
    args: argparse.Namespace,
    limits: Limits,
) -> dict[str, Any]:
    env = benchmark.environment(args, language)
    env.update(
        {
            "BENCHMARK_MODE": "arrival-rate",
            "BENCHMARK_PRE_ALLOCATED_VUS": str(args.preallocated_vus),
            "BENCHMARK_MAX_VUS": str(args.max_vus),
        }
    )
    observations: list[dict[str, Any]] = []
    sequence = 0

    def run_rate(rate: int, phase: str) -> dict[str, Any]:
        nonlocal sequence
        sequence += 1
        try:
            start_services(language, env)
            if args.warmup != "0" and args.warmup != "0s":
                warmup_rate = min(rate, args.warmup_rate)
                warmup_env = {
                    **env,
                    "BENCHMARK_RATE": str(warmup_rate),
                }
                benchmark.load(
                    language,
                    warmup_env,
                    duration=args.warmup,
                    result_name=(
                        f"capacity.{language.name}.{sequence:02d}."
                        f"warmup.{warmup_rate}rps.json"
                    ),
                )
            result = measure(
                language, env, args, limits, rate, sequence, phase
            )
            observations.append(result)
            return result
        finally:
            benchmark.run(
                benchmark.compose_command(
                    language, "down", "--volumes", "--remove-orphans"
                ),
                cwd=language.example,
                env=env,
                check=False,
            )

    last_pass = 0
    first_fail: int | None = None
    rate = args.start_rate
    while rate <= args.max_rate:
        result = run_rate(rate, "search")
        if result["sustainable"]:
            last_pass = rate
            next_rate = max(rate + 1, math.ceil(rate * args.growth_factor))
            if next_rate == rate:
                break
            rate = next_rate
        else:
            first_fail = rate
            break

    if first_fail is None and last_pass < args.max_rate:
        result = run_rate(args.max_rate, "search")
        if result["sustainable"]:
            last_pass = args.max_rate
        else:
            first_fail = args.max_rate

    if first_fail is not None:
        low = last_pass
        high = first_fail
        while high - low > max(1, math.ceil(max(low, 1) * args.resolution)):
            candidate = (low + high) // 2
            result = run_rate(candidate, "refine")
            if result["sustainable"]:
                low = candidate
            else:
                high = candidate
        last_pass = low
        first_fail = high

    confirmed_rate = 0
    confirmation_attempts: list[dict[str, Any]] = []
    passing_candidates = sorted(
        {
            result["target_rate"]
            for result in observations
            if result["sustainable"] and result["target_rate"] <= last_pass
        },
        reverse=True,
    )
    for candidate in passing_candidates:
        candidate_confirmation = [
            run_rate(candidate, "confirm") for _ in range(args.confirm_runs)
        ]
        confirmation_attempts.extend(candidate_confirmation)
        if all(result["sustainable"] for result in candidate_confirmation):
            confirmed_rate = candidate
            break
        if first_fail is None or candidate < first_fail:
            first_fail = candidate

    boundary_found = first_fail is not None
    confirmed = confirmed_rate > 0
    return {
        "language": language.name,
        "maximum_sustainable_rps": (
            confirmed_rate if confirmed and boundary_found else None
        ),
        "minimum_sustainable_rps": confirmed_rate,
        "candidate_rps": last_pass,
        "first_unsustainable_rps": first_fail,
        "search_limit_reached": not boundary_found,
        "confirmed": confirmed,
        "confirmation_attempts": len(confirmation_attempts),
        "observations": observations,
    }


def write_results(
    results: list[dict[str, Any]],
    args: argparse.Namespace,
    limits: Limits,
) -> None:
    document = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "architecture": platform.machine(),
            "os": platform.platform(),
        },
        "parameters": {
            "build": "reused" if args.skip_build else "release",
            "service_cores": args.cores,
            "loadgen_cores": args.loadgen_cores,
            "duration": args.duration,
            "warmup": args.warmup,
            "warmup_rate": args.warmup_rate,
            "start_rate": args.start_rate,
            "max_rate": args.max_rate,
            "growth_factor": args.growth_factor,
            "resolution": args.resolution,
            "confirm_runs": args.confirm_runs,
            "preallocated_vus": args.preallocated_vus,
            "max_vus": args.max_vus,
            "limits": vars(limits),
        },
        "results": results,
    }
    output = benchmark.ARTIFACTS / "capacity.json"
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    rows = [
        "| Language | Maximum sustainable RPS | First failing RPS | Confirmed |",
        "|---|---:|---:|:---:|",
    ]
    for result in results:
        first_fail = result["first_unsustainable_rps"]
        maximum = result["maximum_sustainable_rps"]
        if maximum is None and result["confirmed"]:
            maximum_text = f">= {result['minimum_sustainable_rps']}"
        elif maximum is None:
            maximum_text = "not confirmed"
        else:
            maximum_text = str(maximum)
        rows.append(
            f"| {result['language']} | "
            f"{maximum_text} | "
            f"{first_fail if first_fail is not None else 'not reached'} | "
            f"{'yes' if result['confirmed'] else 'no'} |"
        )
    markdown = (
        "# ServiceLib maximum sustainable throughput\n\n"
        f"- Service CPU quota: `{args.cores}` cores per service container\n"
        f"- Load generator CPU quota: `{args.loadgen_cores}` cores\n"
        f"- Measurement per step: `{args.duration}`\n"
        f"- Required achieved rate: `{limits.min_achieved_ratio * 100:.1f}%`\n"
        f"- Maximum error rate: `{limits.max_error_rate * 100:.3f}%`\n"
        f"- Maximum dropped rate: `{limits.max_dropped_rate * 100:.3f}%`\n"
        f"- Maximum p95 latency: `{limits.max_p95_ms:.1f} ms`\n\n"
        + "\n".join(rows)
        + "\n"
    )
    (benchmark.ARTIFACTS / "capacity.md").write_text(markdown)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find maximum sustainable throughput of ServiceLib examples"
    )
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--loadgen-cores", type=int, default=8)
    parser.add_argument("--duration", default="15s")
    parser.add_argument("--warmup", default="5s")
    parser.add_argument("--warmup-rate", type=int, default=100)
    parser.add_argument("--start-rate", type=int, default=100)
    parser.add_argument("--max-rate", type=int, default=100_000)
    parser.add_argument("--growth-factor", type=float, default=2.0)
    parser.add_argument("--resolution", type=float, default=0.05)
    parser.add_argument("--confirm-runs", type=int, default=3)
    parser.add_argument("--preallocated-vus", type=int, default=128)
    parser.add_argument("--max-vus", type=int, default=4096)
    parser.add_argument("--min-achieved-ratio", type=float, default=0.99)
    parser.add_argument("--max-error-rate", type=float, default=0.001)
    parser.add_argument("--max-dropped-rate", type=float, default=0.001)
    parser.add_argument("--max-p95-ms", type=float, default=100.0)
    parser.add_argument(
        "--max-map-count",
        type=int,
        default=1_048_576,
        help="vm.max_map_count to set host/VM-wide before running (0 to leave it untouched)",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--language",
        action="append",
        choices=[language.name for language in benchmark.LANGUAGES],
    )
    args = parser.parse_args()
    if (
        args.cores <= 0
        or args.loadgen_cores <= 0
        or args.start_rate <= 0
        or args.warmup_rate <= 0
        or args.max_rate < args.start_rate
        or args.growth_factor <= 1
        or not 0 < args.resolution < 1
        or args.confirm_runs <= 0
        or args.preallocated_vus <= 0
        or args.max_vus < args.preallocated_vus
        or args.max_map_count < 0
    ):
        parser.error("invalid capacity-search parameters")
    # run.environment() also configures the legacy fixed-VU load profile.
    # Capacity mode replaces it in load.js, but the common environment still
    # carries a harmless value for compatibility.
    args.vus = 1

    limits = Limits(
        min_achieved_ratio=args.min_achieved_ratio,
        max_error_rate=args.max_error_rate,
        max_dropped_rate=args.max_dropped_rate,
        max_p95_ms=args.max_p95_ms,
    )
    selected = [
        language
        for language in benchmark.LANGUAGES
        if not args.language or language.name in args.language
    ]
    benchmark.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    cpp_selected = any(language.name == "cpp" for language in selected)
    if cpp_selected:
        benchmark.prepare_cpp_configs(args.cores)
        if args.max_map_count:
            benchmark.raise_max_map_count(args.max_map_count)
    if not args.skip_build:
        for language in selected:
            benchmark.build(language, benchmark.environment(args, language))

    results = []
    for language in selected:
        print(f"\n=== capacity: {language.name} ===", flush=True)
        results.append(find_capacity(language, args, limits))
        write_results(results, args, limits)
    print("\n" + (benchmark.ARTIFACTS / "capacity.md").read_text(), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
