PYTHON ?= python3
CORES ?= 2
VUS ?= 32
DURATION ?= 20s
WARMUP ?= 5s
RUNS ?= 3
START_RATE ?= 100
MAX_RATE ?= 100000
MAX_P95_MS ?= 100
LOADGEN_CORES ?= 8
RATE ?= 50000
MAX_MAP_COUNT ?= 1048576

.PHONY: setup-native run quick capacity capacity-quick overload overload-quick build clean help

setup-native: ## Fetch missing pinned native example projects
	$(PYTHON) run.py --fetch-native --language go-native --language cpp-native \
		--language python-native --language rust-native

run: ## Build and benchmark all framework and native examples
	$(PYTHON) run.py --cores $(CORES) --loadgen-cores $(LOADGEN_CORES) --vus $(VUS) \
		--duration $(DURATION) --warmup $(WARMUP) --runs $(RUNS) \
		--max-map-count $(MAX_MAP_COUNT)

quick: ## Reuse current images and run one short measurement
	$(PYTHON) run.py --skip-build --cores $(CORES) --loadgen-cores $(LOADGEN_CORES) --vus $(VUS) \
		--duration 5s --warmup 2s --runs 1 --max-map-count $(MAX_MAP_COUNT)

capacity: ## Find maximum sustainable RPS using an arrival-rate sweep
	$(PYTHON) capacity.py --cores $(CORES) \
		--loadgen-cores $(LOADGEN_CORES) --start-rate $(START_RATE) \
		--max-rate $(MAX_RATE) --max-p95-ms $(MAX_P95_MS) \
		--max-map-count $(MAX_MAP_COUNT)

capacity-quick: ## Smoke-test capacity search using existing images
	$(PYTHON) capacity.py --skip-build --cores $(CORES) \
		--loadgen-cores $(LOADGEN_CORES) --start-rate $(START_RATE) \
		--max-rate $(MAX_RATE) --max-p95-ms $(MAX_P95_MS) \
		--duration 3s --warmup 1s --confirm-runs 1 --resolution 0.15 \
		--max-map-count $(MAX_MAP_COUNT)

overload: ## Compare p99 latency at the same fixed arrival rate
	$(PYTHON) overload.py --cores $(CORES) \
		--loadgen-cores $(LOADGEN_CORES) --rate $(RATE) \
		--duration $(DURATION) --warmup $(WARMUP) --runs $(RUNS) \
		--max-map-count $(MAX_MAP_COUNT)

overload-quick: ## Smoke-test overload comparison using existing images
	$(PYTHON) overload.py --skip-build --cores $(CORES) \
		--loadgen-cores $(LOADGEN_CORES) --rate $(RATE) \
		--duration 3s --warmup 1s --runs 1 --max-map-count $(MAX_MAP_COUNT)

build: ## Build release images/binaries without running load
	$(PYTHON) run.py --build-only

clean: ## Stop benchmark containers and remove generated results
	$(PYTHON) run.py --clean

help: ## Show benchmark commands and parameters
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'
