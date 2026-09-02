# ServiceLib benchmarks

Cross-language performance benchmarks for the generated Go, userver C++,
Boost C++, Python, Rust and TypeScript services plus their native baselines.

## Quickstart

Only this repository needs to be cloned by hand. `quickstart.sh` clones the
framework repositories and all six pinned native projects into
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

The two complete profiles are therefore:

```bash
./quickstart.sh                    # function-call
./quickstart.sh -- call-semantics # current pooled/parallel graph
```

Each implementation writes its complete build, proxy, Compose and load output
to `examples/.artifacts/logs/<profile>/<language>.log`. The terminal remains
compact (`START`/`PASS`/`FAIL`); on failure it prints the log path and tail.
`results.json` records the same paths, so proxy/cache routing can be audited
without rerunning the benchmark.

`call-semantics` is a quickstart/Make target, not an argument accepted by the
Python load runner. Put benchmark variable overrides after it, for example
`./quickstart.sh -- call-semantics RUNS=1 DURATION=10s`.

The comparative path does not start Redpanda. Its generated framework
configurations explicitly disable the `orderProcessed` Kafka endpoint; native
implementations contain no Kafka branch and receive no Kafka-specific flag.

Use `./quickstart.sh --clone-only` to fetch dependencies without running
anything. To keep them elsewhere, pass an explicit directory:

```bash
./quickstart.sh --dependencies-dir /path/to/benchmark-repositories
```

Delete `examples/.artifacts` before a deliberately clean measurement. The
quickstart normally refreshes existing Git mirrors before resolving managed
revisions; `--skip-git-mirror-refresh` is only for a known-fresh offline cache,
not a fallback after refresh failure.

### Optional shared package proxy

To route package downloads through the generated shared Nexus proxy, opt in
with one global data directory:

```bash
./quickstart.sh --clone-only
export DEPENDENCY_PROXY_DIR="$HOME/.servicegen/dependency-proxy"
make -C .dependencies/goexample DEPENDENCY_PROXY_ACCEPT_EULA=true dependency-cache-up # first start only
./quickstart.sh
```

The quickstart configures host and Docker consumers automatically, including
Docker Engine on Linux through `host-gateway`. Without the variable it uses
normal upstreams. The persistent proxy data is shared with profiling,
conformance and generated projects. It caches package registries,
Debian/Ubuntu APT packages and immutable source archives. The companion Git
mirror caches project clones; compiler and benchmark outputs remain separate.

The same directory can be used with direct Make invocations; the tracked
Docker wrapper resolves to the generated wrapper in `DEPENDENCIES_DIR`, so
direct Make and quickstart use the same proxy contract:

```bash
DEPENDENCY_PROXY_DIR="$HOME/.servicegen/dependency-proxy" \
  make -C examples run DEPENDENCIES_DIR=/path/to/benchmark-repositories
```

After changing a pinned C++ dependency version, discard prepared sources and
CMake state while preserving ccache and Nexus data:

```bash
make -C examples dependency-source-cache-invalidate
```

See [examples/README.md](examples/README.md) for the reproducibility contract,
benchmark modes and run instructions.
