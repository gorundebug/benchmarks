#!/usr/bin/env python3

import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DependencyProxyMakeTest(unittest.TestCase):
    def test_direct_make_uses_the_generated_proxy_contract(self) -> None:
        makefile = (ROOT / "examples" / "Makefile").read_text()
        self.assertIn("DEPENDENCY_PROXY_DIR", makefile)
        self.assertIn(
            "export DEPENDENCY_GITHUB_RAW_URL := "
            "$(DEPENDENCY_PROXY_BASE)/github-raw",
            makefile,
        )
        self.assertIn("scripts/dependency-proxy-bin", makefile)
        launcher = ROOT / "scripts/dependency-proxy-bin/docker"
        self.assertTrue(os.access(launcher, os.X_OK))
        launcher_text = launcher.read_text()
        self.assertIn(
            "cppexample/scripts/docker-dependency-proxy.generated.sh",
            launcher_text,
        )


if __name__ == "__main__":
    unittest.main()
