from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from service import normalize_status_label


class NormalizeStatusLabelTests(unittest.TestCase):
    def test_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_status_label("  READY  "), "ready")

    def test_empty_value_returns_unknown(self) -> None:
        self.assertEqual(normalize_status_label("   "), "unknown")


if __name__ == "__main__":
    unittest.main()
