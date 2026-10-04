"""Tests for publish.py: telling a sandbox restriction from a real login failure in gh / git output.

Run from the repository root:  .venv/bin/python -m unittest discover tests
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from p2plib import publish  # noqa: E402
from p2plib.common import P2PError  # noqa: E402

OK = """github.com
  ✓ Logged in to github.com account someone (keyring)
  - Active account: true
"""
SANDBOX = {  # what gh and git print inside Codex's seatbelt sandbox
    "token": "github.com\n  X Failed to log in to github.com account someone (keyring)\n"
             "  - Active account: true\n  - The token in keyring is invalid.\n"
             "  - To re-authenticate, run: gh auth login -h github.com\n",
    "dns": "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com",
    "dial": 'Get "https://api.github.com/user": dial tcp: lookup api.github.com: no such host',
    "git": "fatal: unable to access 'https://github.com/a/b.git/': Could not resolve host: github.com",
    "keychain": "failed to read token from keychain: User interaction is not allowed. (-25308)",
    "credential": "fatal: could not read Username for 'https://github.com': Operation not permitted",
}
NOT_LOGGED_IN = "You are not logged into any GitHub hosts. To log in, run: gh auth login"
NOT_FOUND = "GraphQL: Could not resolve to a Repository with the name 'a/b'. (repository)"


def done(code: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], code, stdout, stderr)


class Classify(unittest.TestCase):
    def test_success(self):
        self.assertEqual(publish.classify(0, OK), "ok")
        self.assertEqual(publish.classify(0, "keyring"), "ok")  # the exit code decides, not the words

    def test_sandbox_like_failures(self):
        for name, output in SANDBOX.items():
            with self.subTest(name):
                self.assertEqual(publish.classify(1, output), "sandbox")

    def test_real_login_failure(self):
        self.assertEqual(publish.classify(1, NOT_LOGGED_IN), "auth")

    def test_other_failures(self):
        self.assertEqual(publish.classify(1, NOT_FOUND), "other")
        self.assertEqual(publish.classify(1, "HTTP 409: GitHub Pages is already enabled."), "other")

    def test_message_has_the_hint_only_for_a_sandbox_failure(self):
        cmd = ["git", "push", "-u", "origin", "main"]
        self.assertIn("escalated permissions", publish.failure(cmd, done(128, SANDBOX["git"])))
        self.assertIn("`git push -u origin main` failed", publish.failure(cmd, done(128, SANDBOX["git"])))
        self.assertNotIn("escalated permissions", publish.failure(cmd, done(1, "! [rejected] main -> main (fetch first)")))


class CheckGh(unittest.TestCase):
    def run_with(self, result):
        with mock.patch.object(publish.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(publish.subprocess, "run", return_value=result):
            publish.check_gh()

    def test_logged_in(self):
        self.run_with(done(0, stdout=OK))

    def test_invalid_token_in_a_sandbox_is_not_reported_as_logged_out(self):
        with self.assertRaises(P2PError) as e:
            self.run_with(done(1, SANDBOX["token"]))
        self.assertIn("escalated permissions", str(e.exception))
        self.assertIn("The token in keyring is invalid", str(e.exception))
        self.assertNotIn("is not logged in", str(e.exception))

    def test_not_logged_in(self):
        with self.assertRaisesRegex(P2PError, "is not logged in. Run `gh auth login`"):
            self.run_with(done(1, NOT_LOGGED_IN))


class RepoQuestions(unittest.TestCase):
    def ask(self, result):
        with mock.patch.object(publish.subprocess, "run", return_value=result):
            return publish._repo_exists("a/b", Path("."))

    def test_yes_and_no(self):
        self.assertTrue(self.ask(done(0, stdout='{"name":"b"}')))
        self.assertFalse(self.ask(done(1, NOT_FOUND)))

    def test_blocked_network_is_an_error_not_a_missing_repository(self):
        with self.assertRaisesRegex(P2PError, "escalated permissions"):
            self.ask(done(1, SANDBOX["dns"]))


if __name__ == "__main__":
    unittest.main()
