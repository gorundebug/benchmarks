import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class NativeExampleFetchTest(unittest.TestCase):
    def test_fetches_missing_native_example_at_pinned_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "native"
            language = benchmark.Language(
                "test-native",
                destination,
                root / "overlay.yml",
                verify_framework_pool=False,
                repository="https://example.test/native.git",
                revision="v1.2.3",
            )
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_: object) -> None:
                commands.append(command)
                checkout = Path(command[-1])
                checkout.mkdir(parents=True)
                (checkout / "docker-compose.yml").write_text("services: {}\n")

            with patch.object(benchmark, "run", side_effect=fake_run):
                benchmark.ensure_example(language, {})

            self.assertTrue((destination / "docker-compose.yml").is_file())
            self.assertEqual(commands[0][0:4], [
                "git", "clone", "--branch", "v1.2.3",
            ])
            self.assertIn("--depth", commands[0])

    def test_existing_checkout_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "native"
            destination.mkdir()
            (destination / "docker-compose.yml").write_text("services: {}\n")
            language = benchmark.Language(
                "test-native",
                destination,
                destination / "overlay.yml",
                repository="https://example.test/native.git",
                revision="v1.2.3",
            )
            with patch.object(benchmark, "run") as mocked_run:
                benchmark.ensure_example(language, {})
            mocked_run.assert_not_called()

    def test_incomplete_existing_directory_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "native"
            destination.mkdir()
            language = benchmark.Language(
                "test-native",
                destination,
                destination / "overlay.yml",
                repository="https://example.test/native.git",
                revision="v1.2.3",
            )
            with self.assertRaisesRegex(RuntimeError, "refusing to replace"):
                benchmark.ensure_example(language, {})


if __name__ == "__main__":
    unittest.main()
