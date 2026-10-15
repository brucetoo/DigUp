import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_fork_check import ROOT, fixture, git


FAKE_GH = """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if os.environ.get("GH_FAIL") == " ".join(args[:2]):
    sys.exit(7)
state = pathlib.Path(os.environ["GH_STATE"])
data = json.loads(state.read_text()) if state.exists() else {}
if args[:2] == ["pr", "list"]:
    print(json.dumps(data.get("prs", [])))
elif args[:2] == ["pr", "create"]:
    data["prs"] = [{"number": 1, "state": "OPEN", "url": "local-pr"}]
elif args[:2] == ["issue", "list"]:
    print(json.dumps(data.get("issues", [])))
elif args[:2] == ["issue", "create"]:
    title = args[args.index("--title") + 1]
    data["issues"] = [{"title": title, "url": "local-issue"}]
state.write_text(json.dumps(data))
"""


class ForkSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="digup-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        fixture(self.source)
        (self.source / "shared.txt").write_text("baseline\n")
        git(self.source, "init", "-b", "main")
        git(self.source, "config", "user.name", "Test")
        git(self.source, "config", "user.email", "test@example.invalid")
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "baseline")
        self.base = git(self.source, "rev-parse", "HEAD")
        self.origin = self.root / "fork.git"
        self.upstream = self.root / "upstream.git"
        git(self.root, "clone", "--bare", str(self.source), str(self.origin))
        git(self.root, "clone", "--bare", str(self.source), str(self.upstream))
        git(self.origin, "branch", "upstream-main", self.base)
        git(self.source, "remote", "add", "upstream", str(self.upstream))
        git(self.source, "remote", "add", "fork", str(self.origin))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH)
        (self.bin / "gh").chmod(0o755)
        self.log = self.root / "gh.log"
        self.state = self.root / "gh-state.json"
        self.report = self.root / "report.md"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "GH_LOG": str(self.log), "GH_STATE": str(self.state),
                    "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}

    def commit(self, name, text, remote):
        (self.source / name).write_text(text)
        git(self.source, "add", name)
        git(self.source, "commit", "-m", name)
        sha = git(self.source, "rev-parse", "HEAD")
        git(self.source, "push", remote, "HEAD:main")
        return sha

    def sync(self, *args):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/sync-upstream.sh"), "--origin", str(self.origin),
             "--upstream", str(self.upstream), "--report", str(self.report), *args],
            cwd=self.source, env=self.env, text=True, capture_output=True, timeout=30,
        )

    def calls(self, command):
        return [args for args in map(json.loads, self.log.read_text().splitlines())
                if args[:2] == command] if self.log.exists() else []

    def assert_main_preserved(self):
        self.assertEqual(git(self.origin, "rev-parse", "main"), self.main)
        self.assertEqual(git(self.origin, "rev-parse", "upstream-main"), self.base)

    def separate_fork_change(self):
        git(self.source, "checkout", "--detach", self.base)
        self.main = self.commit("custom.txt", "fork customization\n", "fork")

    def test_dry_run_and_no_publish_preserve_dirty_caller_and_remotes(self):
        target = self.commit("upstream.txt", "new upstream\n", "upstream")
        self.separate_fork_change()
        (self.source / "custom.txt").write_text("uncommitted\n")
        (self.source / "untracked.txt").write_text("leave alone\n")
        before = git(self.source, "status", "--porcelain")
        for mode in ("--dry-run", "--no-publish"):
            result = self.sync(mode)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(target, self.report.read_text())
            self.assert_main_preserved()
            self.assertEqual(git(self.source, "status", "--porcelain"), before)
            self.assertNotIn("sync/upstream-", git(self.origin, "for-each-ref", "--format=%(refname)"))
        self.assertFalse(self.log.exists())

    def test_publish_merge_pr_and_reuse_same_target(self):
        target = self.commit("upstream.txt", "new upstream\n", "upstream")
        self.separate_fork_change()
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        branch = f"sync/upstream-{target}"
        merged = git(self.origin, "rev-parse", branch)
        git(self.origin, "merge-base", "--is-ancestor", target, merged)
        git(self.origin, "merge-base", "--is-ancestor", self.main, merged)
        self.assertEqual(len(git(self.origin, "rev-list", "--parents", "-n", "1", merged).split()), 3)
        self.assert_main_preserved()
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(git(self.origin, "rev-parse", branch), merged)
        self.assertEqual(len(self.calls(["pr", "create"])), 1)
        self.assertEqual(len(self.calls(["workflow", "run"])), 2)
        self.assertIn("--draft", self.calls(["pr", "create"])[0])
        self.assertIn("brucetoo/DigUp", self.calls(["pr", "create"])[0])

    def test_conflict_abort_issue_dedup_no_branch(self):
        target = self.commit("shared.txt", "upstream side\n", "upstream")
        git(self.source, "checkout", "--detach", self.base)
        self.main = self.commit("shared.txt", "fork side\n", "fork")
        for _ in range(2):
            result = self.sync()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("shared.txt", self.report.read_text())
            self.assertIn("Merge aborted", self.report.read_text())
            self.assert_main_preserved()
        self.assertEqual(len(self.calls(["issue", "create"])), 1)
        self.assertFalse(self.calls(["pr", "create"]))
        self.assertNotIn(f"sync/upstream-{target}", git(self.origin, "for-each-ref", "--format=%(refname)"))

    def test_conflict_no_publish_does_not_contact_gh(self):
        self.commit("shared.txt", "upstream\n", "upstream")
        git(self.source, "checkout", "--detach", self.base)
        self.main = self.commit("shared.txt", "fork\n", "fork")
        self.assertEqual(self.sync("--no-publish").returncode, 1)
        self.assertFalse(self.log.exists())
        self.assert_main_preserved()

    def test_translation_failure_still_proposes_draft(self):
        self.commit("Sources/DigUpApp/App.swift", 'L10n.tr("New untranslated")\n', "upstream")
        self.separate_fork_change()
        self.assertEqual(self.sync().returncode, 1)
        self.assertIn("missing translation", self.report.read_text())
        self.assertEqual(len(self.calls(["pr", "create"])), 1)
        self.assert_main_preserved()

    def test_pin_change_fails_without_publishing(self):
        path = self.source / "Sources/DigUpKit/ModelFiles.swift"
        self.commit(str(path.relative_to(self.source)), path.read_text().replace(
            "bfcd298762cc34d0357ece5ebdd31791a3a374d8", "c" * 40), "upstream")
        self.separate_fork_change()
        self.assertEqual(self.sync("--dry-run").returncode, 1)
        self.assertIn("MODEL PIN CHANGE", self.report.read_text())
        self.assert_main_preserved()

    def test_integrated_target_advances_only_pristine_baseline(self):
        target = self.commit("upstream.txt", "new upstream\n", "upstream")
        git(self.source, "push", "fork", "HEAD:main")
        self.main = target
        result = self.sync("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_main_preserved()
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.origin, "rev-parse", "main"), target)
        self.assertEqual(git(self.origin, "rev-parse", "upstream-main"), target)
        self.assertFalse(self.calls(["pr", "create"]))

    def test_invalid_baseline_refuses_divergence(self):
        self.commit("upstream.txt", "upstream\n", "upstream")
        self.separate_fork_change()
        git(self.origin, "update-ref", "refs/heads/upstream-main", self.main)
        result = self.sync("--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pristine upstream history", result.stderr)
        self.assertEqual(git(self.origin, "rev-parse", "main"), self.main)

    def test_closed_pr_requires_human_review(self):
        self.commit("upstream.txt", "upstream\n", "upstream")
        self.separate_fork_change()
        self.state.write_text(json.dumps({"prs": [{"number": 1, "state": "CLOSED", "url": "closed-pr"}]}))
        self.assertEqual(self.sync().returncode, 1)
        self.assertFalse(self.calls(["pr", "create"]))
        self.assert_main_preserved()

    def test_failed_pr_creation_recovers_pushed_branch_without_overwrite(self):
        target = self.commit("upstream.txt", "upstream\n", "upstream")
        self.separate_fork_change()
        self.env["GH_FAIL"] = "pr create"
        result = self.sync()
        self.assertEqual(result.returncode, 7)
        self.assertTrue(self.report.exists())
        branch = f"sync/upstream-{target}"
        pushed = git(self.origin, "rev-parse", branch)
        del self.env["GH_FAIL"]
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(git(self.origin, "rev-parse", branch), pushed)
        self.assertIn("Recovered existing remote branch", self.report.read_text())
        self.assert_main_preserved()

    def test_missing_baseline_uses_shared_ancestor_without_publishing(self):
        self.commit("upstream.txt", "upstream\n", "upstream")
        self.separate_fork_change()
        git(self.origin, "update-ref", "-d", "refs/heads/upstream-main")
        result = self.sync("--no-publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Baseline: `{self.base}`", self.report.read_text())
        self.assertEqual(git(self.origin, "rev-parse", "main"), self.main)


if __name__ == "__main__":
    unittest.main()
