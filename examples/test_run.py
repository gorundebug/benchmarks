import tempfile
import unittest
from pathlib import Path

import run as benchmark


class PoolVerificationTest(unittest.TestCase):
    def language_with_graph(self, call_semantics: str) -> benchmark.Language:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        example = Path(temporary_directory.name)
        graph_directory = example / "orderservice" / "graph"
        graph_directory.mkdir(parents=True)
        (graph_directory / "orderservice.generated.yaml").write_text(
            "services:\n"
            "  orderService:\n"
            "    links:\n"
            f"      link1:\n        callSemantics: {call_semantics}\n"
        )
        return benchmark.Language("test", example, example / "compose.yml")

    def test_detects_priority_task_pool_link(self) -> None:
        language = self.language_with_graph("PriorityTaskPool")
        self.assertTrue(
            benchmark.service_uses_priority_task_pool(language, "orderservice")
        )

    def test_function_call_graph_does_not_require_pool_metric(self) -> None:
        language = self.language_with_graph("FunctionCall")
        self.assertFalse(
            benchmark.service_uses_priority_task_pool(language, "orderservice")
        )

    def test_missing_generated_graph_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            language = benchmark.Language(
                "test", Path(directory), Path(directory) / "compose.yml"
            )
            with self.assertRaisesRegex(RuntimeError, "failed to read"):
                benchmark.service_uses_priority_task_pool(
                    language, "orderservice"
                )


if __name__ == "__main__":
    unittest.main()
