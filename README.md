# ServiceLib benchmarks

Cross-language performance benchmarks for the generated Go, C++, Python and
Rust example services.

## Quickstart

Only this repository needs to be cloned by hand. `quickstart.sh` clones the
framework repositories and all four pinned native projects into
`benchmarks/.dependencies/`, then runs the benchmark with 256 virtual users by
default:

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

To benchmark the framework implementations with the generated mixed
`TaskPool`/`PriorityTaskPool`/`ParallelCall` profile instead of the canonical
`FunctionCall` profile:

```bash
./quickstart.sh -- call-semantics
```

This uses disposable generated examples and writes separately prefixed
artifacts; it does not modify the managed canonical checkouts.

The comparative path does not start Redpanda. Its generated framework
configurations explicitly disable the `orderProcessed` Kafka endpoint; native
implementations contain no Kafka branch and receive no Kafka-specific flag.

Use `./quickstart.sh --clone-only` to fetch dependencies without running
anything. To keep them elsewhere, pass an explicit directory:

```bash
./quickstart.sh --dependencies-dir /path/to/benchmark-repositories
```

### Optional shared package proxy

To route package downloads through the generated shared Nexus proxy, opt in
with one global data directory:

```bash
./quickstart.sh --clone-only
export SERVICEGEN_DEPENDENCY_PROXY_DIR="$HOME/.servicegen/dependency-proxy"
make -C .dependencies/goexample SERVICEGEN_NEXUS_ACCEPT_EULA=true dependency-cache-up # first start only
./quickstart.sh
```

The quickstart configures host and Docker consumers automatically, including
Docker Engine on Linux through `host-gateway`. Without the variable it uses
normal upstreams. The persistent proxy data is shared with profiling,
conformance and generated projects. It caches package registries, not compiler
output or arbitrary Git clones.

The same directory can be used with direct Make invocations:

```bash
make -C examples run BENCHMARK_DEPENDENCIES_DIR=/path/to/benchmark-repositories
```

See [examples/README.md](examples/README.md) for the reproducibility contract,
benchmark modes and run instructions.
