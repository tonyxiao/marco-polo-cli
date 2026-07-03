import os
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from argparse import Namespace
from io import StringIO
from pathlib import Path

from marco_polo_cli import cli


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def write_fake_har(path, url, authorization, x_auth_token=None):
    headers = [{"name": "Authorization", "value": authorization}]
    if x_auth_token:
        headers.append({"name": "X-Auth-Token", "value": x_auth_token})
    path.write_text(json.dumps({"log": {"entries": [{"request": {"url": url, "headers": headers}}]}}))


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
            self.assertIn("initialized private/, exports/, and token path .marco-polo-token", result.stdout)
            self.assertTrue((Path(td) / "private" / ".gitignore").exists())
            self.assertTrue((Path(td) / "private" / "README.md").exists())
            self.assertTrue((Path(td) / "exports").is_dir())

    def test_auth_import_writes_token_and_check_does_not_leak_values(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            har = td / "capture.har"
            token = td / ".marco-polo-token"
            write_fake_har(
                har,
                "https://video-2-redirect.marcopolo.me/api/v4/videos/video-1/mp4/video",
                "Bearer fake-video-token",
                "fake-x-auth",
            )
            imported = run_cli("auth", "import-har", str(har), "--profile", "video", "--token-file", str(token))
            self.assertIn(f"wrote video token to {token}", imported.stdout)

            result = run_cli("auth", "check", "--profile", "video", "--token-file", str(token))
            self.assertIn("video auth header: present", result.stdout)
            self.assertIn("video x-auth-token header: present", result.stdout)
            self.assertNotIn("fake-video-token", result.stdout)
            self.assertNotIn("fake-x-auth", result.stdout)
            self.assertEqual(oct(token.stat().st_mode & 0o777), "0o600")

    def test_doctor_reports_token_file(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            token = td / ".marco-polo-token"
            token.write_text(json.dumps({
                "version": 1,
                "tokens": {
                    "sync": {"authorization": "Bearer fake-sync-token"},
                    "video": {"authorization": "Bearer fake-video-token", "x_auth_token": "fake-x-auth"},
                },
            }))
            result = run_cli(
                "doctor",
                "--token-file",
                str(token),
                "--sync-file",
                str(FIXTURES / "sync.json"),
            )
            self.assertIn("token file: ok", result.stdout)
            self.assertIn("sync metadata: ok", result.stdout)
            self.assertIn("video token: Authorization=present X-Auth-Token=present", result.stdout)

    def test_auth_login_verifies_code_and_writes_token_file(self):
        calls = []

        def fake_post_json(url, body):
            calls.append((url, body))
            return {"api_token": "fake-api-token", "video_auth_token": "fake-video-auth"}

        old_post_json = cli.post_json
        cli.post_json = fake_post_json
        try:
            with tempfile.TemporaryDirectory() as td:
                token = Path(td) / ".marco-polo-token"
                with redirect_stdout(StringIO()):
                    cli.auth_login(
                        Namespace(
                            phone="+1 (555) 010-1234",
                            code="123456",
                            country_code="US",
                            delivery="sms",
                            existing_user_only=False,
                            token_file=token,
                        )
                    )
                data = json.loads(token.read_text())
        finally:
            cli.post_json = old_post_json

        self.assertEqual(calls[0][0], "https://marcopolo.me/api/v4/auth/verify-phone-code")
        self.assertEqual(calls[0][1]["phone"], "5550101234")
        self.assertEqual(calls[0][1]["verification_code"], "123456")
        self.assertEqual(data["tokens"]["sync"]["authorization"], "Bearer fake-api-token")
        self.assertEqual(data["tokens"]["video"]["x_auth_token"], "fake-video-auth")

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

        participants = run_cli("participants", "--sync-file", sync, "--conversation", "conv-1")
        self.assertIn("conv-1", participants.stdout)
        self.assertIn("Example Person", participants.stdout)


if __name__ == "__main__":
    unittest.main()
