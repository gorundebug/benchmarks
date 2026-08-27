#!/usr/bin/env bash
set -euo pipefail

# Clones or updates the repositories this benchmark needs and runs it with
# sensible defaults. Intended entry point for someone who just cloned
# `benchmarks` and wants to reproduce the Go/C++/Python/Rust/TypeScript comparison
# without first learning the multi-repo layout. Dependencies are stored inside
# this checkout under .dependencies by default.
#
# Usage:
#   ./quickstart.sh                # clone what's missing, then run with 256 VUs
#   ./quickstart.sh --clone-only   # only fetch missing repositories, don't run
#   ./quickstart.sh --dependencies-dir /path/to/repos
#   ./quickstart.sh --skip-git-mirror-refresh  # trust cached Git revisions
#   ./quickstart.sh -- call-semantics  # pooled generated graph performance
#
# Anything after the flags is forwarded to `make run` in benchmarks/examples,
# e.g.:
#   ./quickstart.sh -- CORES=4 VUS=64 DURATION=10s WARMUP=3s RUNS=1

ORG="https://github.com/gorundebug"
BENCHMARK_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPENDENCIES_DIR="$BENCHMARK_ROOT/.dependencies"
MANAGED_DEPENDENCIES=1

REPOS=(goexample cppexample cppboostexample pyexample rustexample tsexample servicelib cppservicelib cppboostservicelib pyservicelib rustservicelib tsservicelib servicegen)

clone_only=0
refresh_git_mirror=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --clone-only)
      clone_only=1
      shift
      ;;
    --skip-git-mirror-refresh)
      refresh_git_mirror=0
      shift
      ;;
    --dependencies-dir)
      if [ "$#" -lt 2 ]; then
        echo "--dependencies-dir requires a path" >&2
        exit 2
      fi
      DEPENDENCIES_DIR="$2"
      MANAGED_DEPENDENCIES=0
      shift 2
      ;;
    --dependencies-dir=*)
      DEPENDENCIES_DIR="${1#*=}"
      MANAGED_DEPENDENCIES=0
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

mkdir -p "$DEPENDENCIES_DIR"
DEPENDENCIES_DIR="$(CDPATH= cd -- "$DEPENDENCIES_DIR" && pwd)"
export BENCHMARK_DEPENDENCIES_DIR="$DEPENDENCIES_DIR"
export BENCHMARK_UPDATE_MANAGED_DEPENDENCIES="$MANAGED_DEPENDENCIES"

echo "==> Checking prerequisites"
missing=0
for tool in git docker python3 curl; do
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
echo "  git, docker, docker compose, python3, curl: OK"

if [ -n "${SERVICEGEN_DEPENDENCY_PROXY_DIR:-}" ]; then
  proxy_host="${SERVICEGEN_DEPENDENCY_PROXY_HOST:-localhost}"
  git_mirror_port="${SERVICEGEN_GIT_MIRROR_PORT:-18084}"
  bootstrap_git_mirror="http://$proxy_host:$git_mirror_port/cgi-bin/git"
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="url.$bootstrap_git_mirror/github.com/.insteadOf"
  export GIT_CONFIG_VALUE_0=https://github.com/
  export GIT_CONFIG_KEY_1="url.$bootstrap_git_mirror/gitlab.com/.insteadOf"
  export GIT_CONFIG_VALUE_1=https://gitlab.com/
  if [ "$refresh_git_mirror" -eq 1 ]; then
    echo "==> Refreshing every cached Git mirror before resolving revisions"
    curl --fail --show-error --silent --request POST \
      "$bootstrap_git_mirror/__servicegen_refresh"
  else
    echo "==> Trusting cached Git mirror revisions (--skip-git-mirror-refresh)"
  fi
fi

echo "==> Preparing repositories in $DEPENDENCIES_DIR"
for repo in "${REPOS[@]}"; do
  dir="$DEPENDENCIES_DIR/$repo"
  if [ -d "$dir/.git" ]; then
    if [ "$MANAGED_DEPENDENCIES" -eq 1 ]; then
      if ! git -C "$dir" diff --quiet || ! git -C "$dir" diff --cached --quiet; then
        echo "  $repo: managed checkout has local changes; refusing to update" >&2
        exit 1
      fi
      echo "  $repo: updating managed main checkout"
      git -C "$dir" fetch origin main:refs/remotes/origin/main
      if git -C "$dir" show-ref --verify --quiet refs/heads/main; then
        git -C "$dir" checkout main
      else
        git -C "$dir" checkout --no-track -b main origin/main
      fi
      git -C "$dir" pull --ff-only origin main
    else
      echo "  $repo: external checkout, leaving unchanged"
    fi
    continue
  fi
  echo "  cloning $repo"
  git clone --branch main --single-branch --depth 1 "$ORG/$repo.git" "$dir"
done

if [ -n "${SERVICEGEN_DEPENDENCY_PROXY_DIR:-}" ]; then
  proxy_script="$DEPENDENCIES_DIR/goexample/scripts/dependency-cache.generated.sh"
  if [ ! -x "$proxy_script" ]; then
    echo "Shared dependency proxy requested, but $proxy_script is missing" >&2
    exit 1
  fi
  export SERVICEGEN_NEXUS_CLIENT_HOST="${SERVICEGEN_DEPENDENCY_PROXY_HOST:-localhost}"
  eval "$("$proxy_script" env)"
  userver_revision="$(sed -nE \
    's|.*userver\.git#([0-9a-f]+).*|\1|p' \
    "$DEPENDENCIES_DIR/cppservicelib/docker-compose.cmake.yml" | head -n 1)"
  if [ -z "$userver_revision" ]; then
    echo "Unable to resolve the pinned userver revision" >&2
    exit 1
  fi
  proxy_docker_host="${SERVICEGEN_DEPENDENCY_PROXY_DOCKER_HOST:-host.docker.internal}"
  proxy_port="${SERVICEGEN_DEPENDENCY_PROXY_PORT:-${SERVICEGEN_NEXUS_PORT:-18081}}"
  export USERVER_SOURCE_CONTEXT="http://$proxy_docker_host:$proxy_port/repository/github-raw/userver-framework/userver/archive/$userver_revision.tar.gz"
  export SERVICEGEN_REAL_DOCKER="$(command -v docker)"
  proxy_bin="$BENCHMARK_ROOT/.artifacts/dependency-proxy-bin"
  mkdir -p "$proxy_bin"
  ln -sfn "$BENCHMARK_ROOT/scripts/docker-dependency-proxy.sh" "$proxy_bin/docker"
  export PATH="$proxy_bin:$PATH"
  echo "==> Using shared dependency proxy (host: $SERVICEGEN_NEXUS_CLIENT_HOST, containers: ${SERVICEGEN_DEPENDENCY_PROXY_DOCKER_HOST:-host.docker.internal})"
fi

echo "==> Restoring pinned native benchmark projects"
python3 "$BENCHMARK_ROOT/examples/run.py" --fetch-native \
  --language go-native \
  --language cpp-native \
  --language cpp-boost-native \
  --language python-native \
  --language rust-native \
  --language typescript-native

# goexample/cppexample/cppboostexample/pyexample each split their service/module code into
# further separate repos (orderservice, inventoryservice, order_service_api,
# inventory_service_api, model), restored via their own clone.generated.sh.
# Rust keeps the equivalent code force-added inside rustexample itself, so it
# needs no extra step.
echo "==> Restoring each example's own service/module repos"
for example in goexample cppexample cppboostexample pyexample tsexample; do
  script="$DEPENDENCIES_DIR/$example/clone.generated.sh"
  if [ -f "$script" ]; then
    echo "  $example"
    (cd "$DEPENDENCIES_DIR/$example" && bash clone.generated.sh)
  fi
done

if [ "$clone_only" -eq 1 ]; then
  echo "==> --clone-only requested, not running the benchmark"
  exit 0
fi

# Keep the lower Makefile default useful for direct smoke runs, while the
# user-facing full quickstart comparison exercises every implementation at the
# agreed common load. An environment value or an explicit `VUS=...` make
# argument still overrides this default.
export VUS="${VUS:-256}"

echo "==> Running the benchmark with $VUS VUs (framework and native variants run sequentially)"
cd "$BENCHMARK_ROOT/examples"
if [ "$#" -gt 0 ] && [[ "$1" != *=* ]]; then
  exec make "$@"
fi
exec make run "$@"
