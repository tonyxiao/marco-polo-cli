import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "marco_polo_cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


@unittest.skipUnless(os.environ.get("RUN_MARCO_POLO_INTEGRATION") == "1", "set RUN_MARCO_POLO_INTEGRATION=1")
class LiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        token = os.environ.get("MARCO_POLO_TOKEN_FILE")
        if not token:
            self.skipTest("set MARCO_POLO_TOKEN_FILE to an ignored token file")
        self.token_file = Path(token)
        if not self.token_file.exists():
            self.skipTest(f"token file does not exist: {self.token_file}")
        self.tmp = tempfile.TemporaryDirectory(prefix="marco-polo-live-test-")
        self.sync_file = Path(self.tmp.name) / "sync.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_cli_workflow(self):
        auth = run_cli("auth", "check", "--token-file", str(self.token_file), "--live")
        self.assertIn("sync auth header: present", auth.stdout)
        self.assertIn("live sync: ok", auth.stdout)

        synced = run_cli("sync", "--token-file", str(self.token_file), "--out", str(self.sync_file))
        self.assertIn(f"wrote {self.sync_file}", synced.stdout)

        data = json.loads(self.sync_file.read_text())
        self.assertTrue(data)

        conversations = run_cli("conversations", "--sync-file", str(self.sync_file))
        conversation_lines = [line for line in conversations.stdout.splitlines() if line.strip()]
        self.assertGreater(len(conversation_lines), 0)
        first_conversation_id = conversation_lines[0].split("\t", 1)[0]

        participants = run_cli("participants", "--sync-file", str(self.sync_file), "--conversation", first_conversation_id)
        self.assertIn(first_conversation_id, participants.stdout)

        videos = run_cli("videos", "--sync-file", str(self.sync_file))
        video_lines = [line for line in videos.stdout.splitlines() if line.strip()]
        self.assertGreater(len(video_lines), 0)

        video_id = None
        for line in video_lines:
            parts = line.split("\t")
            if len(parts) >= 5 and parts[4].strip():
                video_id = parts[2]
                break
        if video_id is None:
            video_id = video_lines[0].split("\t")[2]

        transcript = run_cli("transcript", video_id, "--sync-file", str(self.sync_file), "--json")
        transcript_data = json.loads(transcript.stdout)
        self.assertEqual(transcript_data["video_id"], video_id)
        self.assertIn("transcript", transcript_data)

        actionables = run_cli("actionables", video_id, "--sync-file", str(self.sync_file))
        actionables_data = json.loads(actionables.stdout)
        self.assertEqual(actionables_data["video_id"], video_id)
        self.assertIn("actionables", actionables_data)

        video_auth = run_cli(
            "auth",
            "check",
            "--profile",
            "video",
            "--token-file",
            str(self.token_file),
            "--sync-file",
            str(self.sync_file),
            "--video-id",
            video_id,
        )
        self.assertIn("video auth header: present", video_auth.stdout)
        self.assertIn("video download: ok", video_auth.stdout)


if __name__ == "__main__":
    unittest.main()
