import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def run_cli(*args, cwd=ROOT):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "marco_polo_cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


class CliTests(unittest.TestCase):
    def test_init_creates_private_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_cli("init", cwd=Path(td))
            self.assertIn("initialized private/ and exports/", result.stdout)
            self.assertTrue((Path(td) / "private" / ".gitignore").exists())
            self.assertTrue((Path(td) / "private" / "README.md").exists())
            self.assertTrue((Path(td) / "exports").is_dir())

    def test_auth_check_reports_headers_without_leaking_values(self):
        result = run_cli("auth-check", "--auth-har", str(FIXTURES / "video-auth.har"))
        self.assertIn("auth header: present", result.stdout)
        self.assertIn("x-auth-token header: present", result.stdout)
        self.assertNotIn("fake-video-token", result.stdout)
        self.assertNotIn("fake-x-auth", result.stdout)

    def test_doctor_reports_fixture_paths(self):
        result = run_cli(
            "doctor",
            "--auth-har",
            str(FIXTURES / "sync-auth.har"),
            "--video-auth-har",
            str(FIXTURES / "video-auth.har"),
            "--sync-file",
            str(FIXTURES / "sync.json"),
        )
        self.assertIn("sync auth HAR: ok", result.stdout)
        self.assertIn("video auth HAR: ok", result.stdout)
        self.assertIn("sync metadata: ok", result.stdout)
        self.assertIn("video auth headers: Authorization=present X-Auth-Token=present", result.stdout)

    def test_search_transcript_and_actionables_from_sync_metadata(self):
        sync = str(FIXTURES / "sync.json")
        search = run_cli("search-videos", "--sync-file", sync, "--query", "pressure")
        self.assertIn("video-1", search.stdout)
        self.assertIn("Bethel Island Team", search.stdout)
        self.assertNotIn("video-2", search.stdout)

        transcript = run_cli("transcript", "video-1", "--sync-file", sync)
        self.assertIn("sink water pressure", transcript.stdout)

        actionables = run_cli("actionables", "video-1", "--sync-file", sync)
        self.assertIn("Check the sink/water pressure", actionables.stdout)
        self.assertIn("dishwasher", actionables.stdout)

    def test_videos_and_conversations_aliases(self):
        sync = str(FIXTURES / "sync.json")
        videos = run_cli("videos", "--sync-file", sync)
        self.assertIn("video-1", videos.stdout)
        self.assertIn("video-2", videos.stdout)

        conversations = run_cli("conversations", "--sync-file", sync)
        self.assertIn("conv-1", conversations.stdout)
        self.assertIn("Bethel Island Team", conversations.stdout)


if __name__ == "__main__":
    unittest.main()
