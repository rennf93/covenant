"""Session persistence tests: state.json save/restore used by --resume in
the shadow and real runners. Stdlib only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vouch.engine.session import load_state, save_state


class SessionStateTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            save_state(d, {"mode": "shadow", "cash": 12.5, "trades": [{"tick": 1}]})
            state = load_state(d)
            self.assertEqual(state["cash"], 12.5)
            self.assertEqual(state["trades"], [{"tick": 1}])

    def test_missing_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(Path(tmp)))

    def test_corrupt_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "state.json").write_text("{broken", encoding="utf-8")
            self.assertIsNone(load_state(Path(tmp)))

    def test_save_is_atomic_no_tmp_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            save_state(d, {"a": 1})
            self.assertEqual([p.name for p in d.iterdir()], ["state.json"])


if __name__ == "__main__":
    unittest.main()
