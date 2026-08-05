# Cross-language example benchmark

This project measures the same generated Order Service → Inventory Service
request path in the Go, C++, Python and Rust examples.

Each language is measured separately. Both service containers receive the same
CPU quota and the k6 load generator has its own quota, so lack of client CPU is
less likely to be mistaken for a server limit.

## Reproducibility contract

- production/release compiler settings are used: normal optimized Go build,
  C++ `Release` with userver LTO, Python `-OO`, and Cargo `--release`;
- OTLP export is disabled and k6 sends neither `X-Trace` nor a sampled remote
  trace context, so framework spans are not created; metrics remain active;
- per-request access logging is disabled for every runtime;
- Go `GOMAXPROCS`, C++ main task-processor workers, Rust Tokio workers and all
  generated ServiceLib task pools are set to the requested service core count
  (Python remains a single event loop outside its ServiceLib task pools);
- Inventory Service starts before Order Service;
- every language receives the same JSON request, warm-up, VU count, duration,
  repetitions and per-service CPU quota;
- languages run sequentially to avoid competing with one another;
- every request uses a missing SKU, keeping the business path stable instead
  of exhausting the examples' in-memory stock;
- the reported value for repeated runs is the median.

Docker's `cpus` setting is a quota, not CPU pinning. On Docker Desktop the
virtual machine must have at least `2 × service cores + load-generator cores`
available, otherwise the host/VM becomes the shared bottleneck. Avoid running
other heavy workloads during a comparison.

## Run

The default is three 20-second measurements after a 5-second warm-up:

```bash
cd examples
make run CORES=2 VUS=32 DURATION=20s WARMUP=5s RUNS=3
```

The requested number of cores applies independently to `orderservice` and
`inventoryservice`. The load generator defaults to eight cores and can be changed
directly:

```bash
python3 run.py \
  --cores 4 \
  --loadgen-cores 4 \
  --vus 128 \
  --duration 30s \
  --warmup 10s \
  --runs 5
```

Run one language while tuning:

```bash
python3 run.py --language cpp --cores 4 --vus 64
```

Reuse already built release images:

```bash
python3 run.py --skip-build --cores 2 --vus 32
```

A short smoke benchmark is available as:

```bash
make quick CORES=1 VUS=8
```

## Find maximum sustainable throughput

The fixed-VU benchmark above measures achieved throughput at a chosen
concurrency. It does not search for the service limit. The capacity benchmark
uses k6's `constant-arrival-rate` executor, doubles the requested rate until
the first failed step, refines the boundary with a binary search, and confirms
the final candidate with three additional runs. Every measured step runs on
fresh service containers, so a queue left by an overloaded step cannot
contaminate lower-rate refinement or confirmation:

```bash
make capacity \
  CORES=2 \
  LOADGEN_CORES=8 \
  START_RATE=100 \
  MAX_RATE=100000 \
  MAX_P95_MS=100
```

A rate is sustainable only when all of these conditions hold:

- at least 99% of the requested rate was achieved;
- HTTP/check errors do not exceed 0.1%;
- k6 dropped iterations do not exceed 0.1%;
- p95 latency does not exceed `MAX_P95_MS`.

The latency limit is part of the capacity definition. Without it a growing
server queue can hide overload while k6 temporarily continues to start the
requested number of iterations.

Use the short form only to validate the machinery:

```bash
make capacity-quick CORES=2 START_RATE=100 MAX_RATE=2000
```

The detailed result is written to `.artifacts/capacity.json`; the summary is
written to `.artifacts/capacity.md`. Every measured step is preserved as a
separate `capacity.<language>.*.json` file. CPU samples for both services and
the load generator are included in the detailed result. If loadgen CPU is
close to its quota, increase `LOADGEN_CORES` before treating the observed
boundary as a server limit.

## Compare p99 under a fixed overload

The overload benchmark requests the same fixed arrival rate from every
language and ranks the median p99 of completed requests:

```bash
make overload \
  CORES=2 \
  LOADGEN_CORES=8 \
  RATE=50000 \
  DURATION=15s \
  WARMUP=3s \
  RUNS=3
```

Each run gets fresh service containers. This test deliberately does not require
the target rate to be sustainable. Its table therefore always reports completed
RPS, scheduled percentage and dropped percentage next to p99. Comparing p99
alone would reward an implementation when the load generator was unable to
start most of its intended requests.

Use `make overload-quick CORES=2 LOADGEN_CORES=8 RATE=50000` to validate the
test mechanics with existing images. Results are written to
`.artifacts/overload.json` and `.artifacts/overload.md`; raw k6 summaries are
stored as `overload.<language>.<run>.json`.

## Results

The runner writes:

- `.artifacts/results.md` — human-readable comparison table;
- `.artifacts/results.csv` — table for spreadsheets and plotting;
- `.artifacts/results.json` — results plus host and run metadata;
- `.artifacts/<language>.run-<n>.json` — raw summary for every measured run;
- `.artifacts/<language>.warmup.json` — warm-up summary.

The table includes median requests/second, error rate and
average/p50/p95/p99/max latency. Throughput across different machines, Docker
Desktop resource allocations or CPU architectures must not be compared as if
it came from the same experiment.
