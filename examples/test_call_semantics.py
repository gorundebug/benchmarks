import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import call_semantics


class CurrentGraphContractTest(unittest.TestCase):
    def test_profile_matrix_compares_frameworks_with_native_baselines(self) -> None:
        self.assertEqual(
            set(call_semantics.VARIANTS),
            {
                "go", "cpp", "cpp-boost", "python", "rust", "typescript",
            },
        )

    def test_native_baseline_is_copied_into_disposable_profile_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gonativeexample"
            source.mkdir()
            (source / "docker-compose.yml").write_text("services: {}\n")
            (source / "build.rs").write_text("fn main() {}\n")
            (source / "src/bin").mkdir(parents=True)
            (source / "src/bin/orderservice.rs").write_text("fn main() {}\n")
            workspace = root / "workspace"
            archives = root / "archives"
            artifacts = root / "artifacts"
            workspace.mkdir()
            archives.mkdir()
            with (
                mock.patch.object(call_semantics, "ROOT", root),
                mock.patch.object(call_semantics, "ARTIFACTS", artifacts),
                mock.patch.object(call_semantics, "FRAMEWORKS", ()),
                mock.patch.object(call_semantics, "generate_archives") as generate,
            ):
                generate.return_value = ""
                call_semantics.prepare_workspace(
                    workspace, archives, [], "function-call"
                )
            generate.assert_called_once_with(archives, "function-call")
            self.assertEqual(
                (workspace / "gonativeexample/docker-compose.yml").read_text(),
                "services: {}\n",
            )
            self.assertEqual(
                (workspace / "gonativeexample/build.rs").read_text(),
                "fn main() {}\n",
            )
            self.assertEqual(
                (workspace / "gonativeexample/src/bin/orderservice.rs").read_text(),
                "fn main() {}\n",
            )

    def test_both_profiles_forward_the_complete_comparison_matrix(self) -> None:
        args = argparse.Namespace(
            build_only=False,
            cores=2,
            duration="1s",
            grpc_connections=None,
            loadgen_cores=2,
            max_map_count=0,
            runs=1,
            vus=8,
            warmup="1s",
        )
        selected = list(call_semantics.VARIANTS)
        expected = [
            variant
            for language in selected
            for variant in (language, f"{language}-native")
        ]
        for profile in ("function-call", "current"):
            command = call_semantics.benchmark_command(args, selected, profile)
            forwarded = [
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--language"
            ]
            self.assertEqual(forwarded, expected)
            self.assertEqual(command[command.index("--graph-profile") + 1], profile)

    def test_accepts_complete_current_call_semantics_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory)
            graph = example / "graph"
            graph.mkdir()
            (graph / "example.generated.yaml").write_text(
                "callSemantics: FunctionCall\n" * 8
                + "callSemantics: TaskPool\n" * 4
                + "callSemantics: PriorityTaskPool\n" * 4
                + "callSemantics: ParallelCall\n" * 3
            )
            call_semantics.verify_graph(example, "current")

    def test_rejects_stale_reduced_current_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory)
            graph = example / "graph"
            graph.mkdir()
            (graph / "example.generated.yaml").write_text(
                "callSemantics: TaskPool\n"
                "callSemantics: PriorityTaskPool\n"
                + "callSemantics: ParallelCall\n" * 3
            )
            with self.assertRaisesRegex(RuntimeError, "current profile differs"):
                call_semantics.verify_graph(example, "current")

    def test_accepts_complete_function_call_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory)
            graph = example / "graph"
            graph.mkdir()
            (graph / "example.generated.yaml").write_text(
                "callSemantics: FunctionCall\n" * 19
            )
            call_semantics.verify_graph(example, "function-call")


if __name__ == "__main__":
    unittest.main()
