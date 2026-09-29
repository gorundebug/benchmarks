import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import call_semantics
import run as benchmark


class CoroGenerationTest(unittest.TestCase):
    def test_retired_runtime_is_not_registered_or_required(self) -> None:
        self.assertNotIn("cpp-boost", {item.name for item in benchmark.LANGUAGES})
        self.assertNotIn("cpp-boost", call_semantics.VARIANTS)
        self.assertNotIn("cppboostservicelib", call_semantics.FRAMEWORKS)
        self.assertIn("cppcoroservicelib", call_semantics.FRAMEWORKS)

    def test_generator_uses_matching_local_api_without_mutating_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(call_semantics, "ROOT", root),
                patch.object(call_semantics, "SERVICEGEN", root / "servicegen"),
                patch.object(call_semantics, "run", return_value=Mock(stdout="generated")) as run,
            ):
                result = call_semantics.generate_archives(root / "archives", "current")
            self.assertEqual(result, "generated")
            self.assertEqual(run.call_count, 2)
            initialize, generate = run.call_args_list
            self.assertEqual(initialize.args[0], [
                "go", "work", "init", str(root / "servicegen"), str(root / "servicelib"),
            ])
            self.assertEqual(generate.kwargs["env"]["GOWORK"], initialize.kwargs["env"]["GOWORK"])
            self.assertNotEqual(generate.kwargs["env"]["GOWORK"], "off")
            self.assertFalse(Path(generate.kwargs["env"]["GOWORK"]).parent.exists())
            self.assertEqual(generate.kwargs["env"]["EXAMPLE_PROFILE"], "current")

    def test_both_profiles_merge_coro_archive_before_graph_verification(self) -> None:
        for profile in ("function-call", "current"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "cppcoroexample"
                source.mkdir()
                (source / "business.cpp").write_text("user-owned\n")
                workspace = root / "workspace"
                workspace.mkdir()
                archives = root / "archives"
                archives.mkdir()
                archive = archives / "cppcoro.zip"
                archive.write_bytes(b"test archive")
                events = []
                def merge(*args, **kwargs):
                    events.append("merge")
                    self.assertEqual(args[0], ["bash", "scripts/merge.generated.sh", str(archive)])
                    return Mock(stdout="merged")
                def verify(project, selected_profile):
                    events.append("verify")
                    self.assertEqual(selected_profile, profile)
                    self.assertEqual((project / "business.cpp").read_text(), "user-owned\n")
                with (
                    patch.object(call_semantics, "ROOT", root),
                    patch.object(call_semantics, "ARTIFACTS", root / "artifacts"),
                    patch.object(call_semantics, "FRAMEWORKS", ()),
                    patch.object(call_semantics, "generate_archives", return_value="generated"),
                    patch.object(call_semantics, "run", side_effect=merge),
                    patch.object(call_semantics, "verify_graph", side_effect=verify),
                ):
                    call_semantics.prepare_workspace(workspace, archives, ["cpp-coro"], profile)
                self.assertEqual(events, ["merge", "verify"])


if __name__ == "__main__":
    unittest.main()
