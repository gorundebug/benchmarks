#!/usr/bin/env bash
set -euo pipefail

# Clones the sibling repos this benchmark needs (if missing) and runs it with
# sensible defaults. Intended entry point for someone who just cloned
# `benchmarks` and wants to reproduce the Go/C++/Python/Rust comparison
# without first learning the multi-repo layout.
#
# Usage:
#   ./quickstart.sh                # clone what's missing, then run the benchmark
#   ./quickstart.sh --clone-only   # only clone/update sibling repos, don't run
#
# Anything after the flags is forwarded to `make run` in benchmarks/examples,
# e.g.:
#   ./quickstart.sh -- CORES=4 VUS=64 DURATION=10s WARMUP=3s RUNS=1

ORG="https://github.com/gorundebug"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

REPOS=(goexample cppexample pyexample rustexample servicelib cppservicelib pyservicelib)

clone_only=0
if [ "${1:-}" = "--clone-only" ]; then
  clone_only=1
  shift
fi
if [ "${1:-}" = "--" ]; then
  shift
fi

echo "==> Checking prerequisites"
missing=0
for tool in git docker python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "  missing: $tool" >&2
    missing=1
  fi
done
if ! docker compose version >/dev/null 2>&1; then
  echo "  missing: docker compose plugin (needs Docker Desktop or the compose-plugin package)" >&2
  missing=1
fi
if [ "$missing" -ne 0 ]; then
  echo "Install the missing tools above and re-run." >&2
  exit 1
fi
echo "  git, docker, docker compose, python3: OK"

echo "==> Cloning sibling repositories into $ROOT (skipping any that already exist)"
for repo in "${REPOS[@]}"; do
  dir="$ROOT/$repo"
  if [ -d "$dir/.git" ]; then
    echo "  $repo: already present, skipping"
    continue
  fi
  echo "  cloning $repo"
  git clone --depth 1 "$ORG/$repo.git" "$dir"
done

# goexample/cppexample/pyexample each split their service/module code into
# further separate repos (orderservice, inventoryservice, order_service_api,
# inventory_service_api, model), restored via their own clone.generated.sh.
# Rust keeps the equivalent code force-added inside rustexample itself, so it
# needs no extra step.
echo "==> Restoring each example's own service/module repos"
for example in goexample cppexample pyexample; do
  script="$ROOT/$example/clone.generated.sh"
  if [ -f "$script" ]; then
    echo "  $example"
    (cd "$ROOT/$example" && bash clone.generated.sh)
  fi
done

if [ "$clone_only" -eq 1 ]; then
  echo "==> --clone-only requested, not running the benchmark"
  exit 0
fi

echo "==> Running the benchmark (this builds all four release images, then measures each in turn)"
cd "$ROOT/benchmarks/examples"
exec make run "$@"
