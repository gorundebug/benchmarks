# ServiceLib benchmarks

Cross-language performance benchmarks for the generated Go, C++, Python and
Rust example services.

## Quickstart

Only this repository needs to be cloned by hand. `quickstart.sh` clones the
framework siblings and all four pinned native projects it depends on (if
missing), then runs the benchmark with sensible defaults:

```bash
git clone https://github.com/gorundebug/benchmarks.git
cd benchmarks
./quickstart.sh
```

Requires `git`, `docker` (with the `compose` plugin) and `python3`. Extra
arguments after `--` are forwarded to `make run` in `examples/`, e.g.:

```bash
./quickstart.sh -- CORES=4 VUS=64 DURATION=10s WARMUP=3s RUNS=1
```

Use `./quickstart.sh --clone-only` to just fetch the sibling repos without
running anything.

See [examples/README.md](examples/README.md) for the reproducibility contract,
benchmark modes and run instructions.
