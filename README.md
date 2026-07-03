# marco-polo-cli

Command-line tools for exporting Marco Polo metadata, transcripts, and videos through the reverse-engineered Marco Polo app API.

The CLI should operate from its own token file, not from saved proxy captures. By default it reads and writes `.marco-polo-token` in the current directory. That file can contain live credentials and is ignored by Git.

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

Login flow:

```bash
marco-polo auth login 5550101234
marco-polo auth login 5550101234 --code 123456
marco-polo doctor
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

## API Notes

The reverse-engineered API contract lives in [docs/openapi.yaml](docs/openapi.yaml). Keep it updated whenever a new app endpoint is captured or confirmed.

Known working endpoints:

- `POST /api/v4/auth/send-phone-code`
- `POST /api/v4/auth/verify-phone-code`
- `GET /api/v4/conversations/sync`
- `GET /api/v4/videos/{video_id}/mp4/video`

## Security Notes

- Do not commit `.marco-polo-token`, HAR files, sync JSON exports, raw `/mp4/video` bodies, transcripts, or exported videos unless you are certain they are safe to publish.
- Prefer the repo-local ignored `private/` or `tmp/` directory for metadata and temporary capture artifacts.
- The CLI prints whether auth headers are present, but does not print token values.

## Native Export

`marco_polo_cli.native_export` contains an older Android/Frida-based path. It is kept for reference and unusual cases; the API/token workflow is the preferred path.

## Development

Run the test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests generate temporary fake auth captures at runtime. No committed test fixture contains real tokens, phone numbers, transcripts, or Marco Polo media.
