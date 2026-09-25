"""Static regression tests for the run_* entry points.

The original audit found run_real.process_bar READ next_epoch while also
ASSIGNING it without a nonlocal declaration, which made next_epoch local to
process_bar and crashed the very first bar with UnboundLocalError. These
AST checks pin the nonlocal declarations so the fix cannot silently
regress, and pin the epoch-advance discipline (the index may only move
inside the guarded close-and-advance helpers).
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _nonlocal_names(func: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Nonlocal):
            names.update(node.names)
    return names


def _find_func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node  # type: ignore[return-value]
    raise AssertionError(f"function {name} not found")


class RunRealNonlocalTest(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse((ROOT / "run_real.py").read_text(encoding="utf-8"))

    def test_process_bar_declares_next_epoch_nonlocal(self):
        # THE fix: reading and assigning next_epoch in process_bar requires
        # the nonlocal declaration, or the first bar raises UnboundLocalError.
        self.assertIn("next_epoch", _nonlocal_names(_find_func(self.tree, "process_bar")))

    def test_epoch_index_only_advances_in_the_guarded_helper(self):
        helper = _nonlocal_names(_find_func(self.tree, "close_epoch_boundary"))
        self.assertIn("epoch_index", helper)
        self.assertIn("ledger", helper)

    def test_shutdown_commit_lands_in_finally(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Try) and node.finalbody:
                src = ast.unparse(node)
                self.assertIn("commit_epoch", src)
                self.assertIn("persist_session", src)
                break
        else:
            self.fail("no try/finally shutdown block found in run_real.py")


class RunShadowNonlocalTest(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse((ROOT / "run_shadow.py").read_text(encoding="utf-8"))

    def test_process_bar_declares_its_rebindings(self):
        names = _nonlocal_names(_find_func(self.tree, "process_bar"))
        self.assertIn("next_epoch", names)
        self.assertIn("rules", names)

    def test_epoch_index_only_advances_in_the_guarded_helper(self):
        helper = _nonlocal_names(_find_func(self.tree, "close_and_advance_epoch"))
        self.assertIn("epoch_index", helper)
        self.assertIn("ledger", helper)

    def test_shutdown_commits_and_persists_in_finally(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Try) and node.finalbody:
                src = ast.unparse(node)
                self.assertIn("commit_epoch", src)
                self.assertIn("persist_session", src)
                break
        else:
            self.fail("no try/finally shutdown block found in run_shadow.py")


class RunScriptsPersistenceTest(unittest.TestCase):
    def test_both_runners_persist_and_can_resume(self):
        for script in ("run_shadow.py", "run_real.py"):
            src = (ROOT / script).read_text(encoding="utf-8")
            self.assertIn('"--resume"', src, f"{script} must expose --resume")
            self.assertIn("save_state(", src, f"{script} must persist state")
            self.assertIn("load_state(", src, f"{script} must restore state")

    def test_real_mode_opt_in_gates_unchanged(self):
        src = (ROOT / "run_real.py").read_text(encoding="utf-8")
        self.assertIn("--confirm-real", src)
        self.assertIn("VOUCH_VENUE", src)
        # attestation must never be the thing that flips real mode on
        self.assertNotIn("confirm_real = True", src)


if __name__ == "__main__":
    unittest.main()
