# marco-polo-cli

Command-line tools for exporting Marco Polo metadata, transcripts, and videos through the reverse-engineered Marco Polo app API.

This is an internal utility for people who already have access to the Marco Polo account or conversation they are working with. It is best used as a small local tool that your coding agent can call for you: the agent can authenticate once, refresh metadata, search transcripts, identify video IDs, export MP4s, and keep all private token and export files out of Git.

The CLI operates from its own token file, not from saved proxy captures. By default it reads and writes `.marco-polo-token` in the current directory. That file can contain live credentials and is ignored by Git.

## What It Is Good For

- Exporting one Marco Polo video as a standard MP4 for review, archiving, or forwarding.
- Exporting a batch of videos from a conversation.
- Refreshing conversation metadata and searching video transcripts.
- Pulling transcript text or simple action items from a video.
- Letting an agent handle the repetitive parts of: login, sync, search, pick video, export, verify.

This is not an official Marco Polo client or SDK. The API is private and may change without notice.

## Recommended: Point An Agent At It

The smoothest workflow is usually to give your coding agent this repo and a goal in plain English. For example:

```text
Use projects/marco-polo-cli. Log in with this phone number using a token under tmp/,
refresh sync metadata, find the latest video from Sarah, export it as MP4, verify it,
and tell me where the file is. Do not commit tokens, sync files, transcripts, or videos.
```

Or:

```text
Use projects/marco-polo-cli with MARCO_POLO_TOKEN_FILE=tmp/mp-token.json.
Search my latest sync for videos mentioning "dishwasher" and summarize the action items.
```

Agent-friendly conventions:

- Put scratch data under ignored `tmp/` or private account data under ignored `private/`.
- Use `--token-file tmp/<name>/token.json` when testing a separate phone number or account.
- Use `marco-polo doctor` and `marco-polo auth check --live` before assuming a token is valid.
- Use `--verify` when exporting videos so `ffmpeg`/`ffprobe` catch broken outputs.
- Never paste token values into chat, docs, issues, or commits.

## Install

```bash
python3 -m pip install -e .
```

Requires Python 3.9 or newer. Video conversion and verification require `ffmpeg` and `ffprobe` on `PATH`.

## First Run

From a fresh checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
marco-polo init
```

Manual login flow:

```bash
marco-polo auth login 5550101234
marco-polo auth login 5550101234 --code 123456
marco-polo doctor
```

The first `auth login` command bootstraps an anonymous app token with
`POST /api/v4/auth` before requesting the verification code. Proxyman is not
part of the normal login path.

To test only that first step without sending a verification code:

```bash
marco-polo auth bootstrap
```

If login breaks because the private app API changes, use a temporary capture only to import the auth headers once:

```bash
marco-polo auth import-har /path/to/sync-capture.har --profile sync
marco-polo auth import-har /path/to/video-capture.har --profile video
marco-polo auth check --live
```

After import, delete the capture. Normal commands should use `.marco-polo-token`, not HAR files.

Supported environment variables:

- `MARCO_POLO_TOKEN_FILE`
- `MARCO_POLO_SYNC_FILE`

You can also pass token and sync paths explicitly:

```bash
marco-polo auth login 5550101234 --token-file tmp/work-token.json
marco-polo auth login 5550101234 --code 123456 --token-file tmp/work-token.json
marco-polo sync --token-file tmp/work-token.json --out tmp/work-sync.json
```

## Token File

The token file is JSON and supports separate profiles because the sync and video endpoints may require different headers:

```json
{
  "version": 1,
  "tokens": {
    "sync": {
      "authorization": "Bearer ...",
      "x_auth_token": "..."
    },
    "video": {
      "authorization": "Bearer ...",
      "x_auth_token": "..."
    }
  }
}
```

The CLI writes the file with `0600` permissions. It never prints token values.

## Common Commands

Check saved auth:

```bash
marco-polo auth check --profile sync --live
```

Refresh sync metadata:

```bash
marco-polo sync --out private/sync.json
```

Search videos:

```bash
marco-polo search-videos \
  --sync-file private/sync.json \
  --query "Sara" \
  --limit 10
```

Export one standard MP4:

```bash
marco-polo standard-mp4 VIDEO_ID ./exports/VIDEO_ID.mp4 \
  --sync-file private/sync.json \
  --verify
```

Export a batch:

```bash
marco-polo export-batch ./exports \
  --sync-file private/sync.json \
  --verify \
  --report ./exports/report.json
```

Get transcript/actionables:

```bash
marco-polo transcript VIDEO_ID \
  --sync-file private/sync.json \
  --json

marco-polo actionables VIDEO_ID \
  --sync-file private/sync.json
```

List participants:

```bash
marco-polo participants \
  --sync-file private/sync.json \
  --conversation CONVERSATION_ID
```

## Example Workflows

Find and export one video:

```bash
marco-polo sync --out private/sync.json
marco-polo search-videos --sync-file private/sync.json --query "dishwasher" --limit 5
marco-polo standard-mp4 VIDEO_ID exports/VIDEO_ID.mp4 \
  --sync-file private/sync.json \
  --verify
```

Use a separate phone number or account without touching the default token:

```bash
marco-polo auth login '+1 650 427 9166' --token-file tmp/test-account/token.json
marco-polo auth login '+1 650 427 9166' --code 123456 --token-file tmp/test-account/token.json
marco-polo auth check --token-file tmp/test-account/token.json --live
marco-polo sync --token-file tmp/test-account/token.json --out tmp/test-account/sync.json
```

Batch export with a machine-readable report:

```bash
marco-polo export-batch exports \
  --sync-file private/sync.json \
  --verify \
  --report exports/report.json
```

## API Notes

The reverse-engineered API contract lives in [docs/openapi.yaml](docs/openapi.yaml). Keep it updated whenever a new app endpoint is captured or confirmed.

Known working endpoints:

- `POST /api/v4/auth`
- `POST /api/v4/auth/send-phone-code`
- `POST /api/v4/auth/verify-phone-code`
- `GET /api/v4/conversations/sync`
- `GET /api/v4/videos/{video_id}/mp4/video`

## Security Notes

- Do not commit `.marco-polo-token`, HAR files, sync JSON exports, raw `/mp4/video` bodies, transcripts, or exported videos unless you are certain they are safe to publish.
- Prefer the repo-local ignored `private/` or `tmp/` directory for metadata and temporary capture artifacts.
- The CLI prints whether auth headers are present, but does not print token values.
- Share the Git repository or a clean archive, not a working directory containing ignored local files.
- Only use this with accounts and conversations you are authorized to access.

## Native Export

`marco_polo_cli.native_export` contains an older Android/Frida-based path. It is kept for reference and unusual cases; the API/token workflow is the preferred path.

## Development

Run the test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests generate temporary fake auth captures at runtime. No committed test fixture contains real tokens, phone numbers, transcripts, or Marco Polo media.

Run the live integration suite only with an ignored token file:

```bash
RUN_MARCO_POLO_INTEGRATION=1 \
MARCO_POLO_TOKEN_FILE=.marco-polo-token \
PYTHONPATH=src python3 -m unittest tests.test_integration_live -v
```
