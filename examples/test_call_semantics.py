import tempfile
import unittest
from pathlib import Path

import call_semantics


class CurrentGraphContractTest(unittest.TestCase):
    def test_accepts_complete_current_call_semantics_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory)
            graph = example / "graph"
            graph.mkdir()
            (graph / "example.generated.yaml").write_text(
                "callSemantics: TaskPool\n" * 4
                + "callSemantics: PriorityTaskPool\n" * 4
                + "callSemantics: ParallelCall\n" * 3
            )
            call_semantics.verify_graph(example)

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
                call_semantics.verify_graph(example)


if __name__ == "__main__":
    unittest.main()
