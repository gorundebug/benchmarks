import grpc from "k6/net/grpc";
import { check } from "k6";

const duration = __ENV.BENCHMARK_DURATION || "20s";
const durationSeconds = Number.parseFloat(
  __ENV.BENCHMARK_DURATION_SECONDS || "20",
);
const resultFile = __ENV.BENCHMARK_RESULT_FILE || "/results/grpc-result.json";
const target = __ENV.BENCHMARK_GRPC_TARGET || "inventoryservice:9202";
const vus = Number.parseInt(__ENV.BENCHMARK_VUS || "256", 10);

const client = new grpc.Client();
client.load(
  ["/proto"],
  "inventoryserviceapi.proto",
  "processorderitem.proto",
);
let connected = false;

export const options = {
  vus,
  duration,
  discardResponseBodies: true,
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  if (!connected) {
    client.connect(target, { plaintext: true });
    connected = true;
  }
  const response = client.invoke(
    "inventoryserviceapi.InventoryServiceApi/ProcessOrderItem",
    {
      orderId: "benchmark-order",
      itemId: "benchmark-item",
      sku: "BENCHMARK-MISSING-SKU",
      quantity: 1,
    },
  );
  check(response, {
    "gRPC status is OK": (value) => value && value.status === grpc.StatusOK,
  });
}

export function handleSummary(data) {
  const iterations = data.metrics.iterations?.values || {};
  const durationValues = data.metrics.grpc_req_duration?.values || {};
  const checks = data.metrics.checks?.values || {};
  const summary = {
    scenario: "native_inventory_unary_grpc",
    request_count: iterations.count || 0,
    requests_per_second: durationSeconds > 0
      ? (iterations.count || 0) / durationSeconds
      : 0,
    error_rate: 1 - (checks.rate ?? 1),
    dropped_iterations: 0,
    latency_ms: {
      avg: durationValues.avg || 0,
      p50: durationValues.med || 0,
      p90: durationValues["p(90)"] || 0,
      p95: durationValues["p(95)"] || 0,
      p99: durationValues["p(99)"] || 0,
      max: durationValues.max || 0,
    },
  };
  return {
    [resultFile]: JSON.stringify(summary, null, 2) + "\n",
    stdout:
      `requests=${summary.request_count} ` +
      `rate=${summary.requests_per_second.toFixed(2)}/s ` +
      `p95=${summary.latency_ms.p95.toFixed(2)}ms ` +
      `p99=${summary.latency_ms.p99.toFixed(2)}ms ` +
      `errors=${(summary.error_rate * 100).toFixed(4)}%\n`,
  };
}
