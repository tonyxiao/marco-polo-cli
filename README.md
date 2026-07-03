# marco-polo-cli

Command-line tools for exporting Marco Polo metadata, transcripts, and videos from captured Marco Polo app API authentication.

The workflow is intentionally capture-based: use a local HTTPS proxy such as Proxyman to capture successful Marco Polo Android/iOS app requests, export selected flows as HAR, and point this CLI at those HAR files. HAR files can contain live bearer/session tokens, so keep them local and never commit them.

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
marco-polo doctor
```

`init` creates ignored `private/` and `exports/` directories. Put captured HAR files in `private/` using these names unless you override paths with flags or environment variables:

- `private/sync-auth.har`
- `private/video-auth.har`
- `private/sync.json`

Supported environment variables:

- `MARCO_POLO_AUTH_HAR`
- `MARCO_POLO_VIDEO_AUTH_HAR`
- `MARCO_POLO_SYNC_FILE`

The first useful validation after placing HARs is:

```bash
marco-polo doctor
marco-polo auth-check --auth-har private/sync-auth.har --live
```

## Auth Model

Marco Polo currently needs two kinds of captured auth in practice:

- A sync/auth HAR from a successful `https://marcopolo.me/api/v4/conversations/sync` request.
- A video-auth HAR from a successful `https://video-2-redirect.marcopolo.me/api/v4/videos/.../mp4/video` request.

The sync endpoint may work with only `Authorization`. Video downloads can also require `X-Auth-Token`, so keep the video HAR separate when needed.

## Common Commands

Check sync auth:

```bash
marco-polo auth-check \
  --auth-har private/sync-auth.har \
  --live
```

Refresh sync metadata:

```bash
marco-polo sync \
  --auth-har private/sync-auth.har \
  --out private/sync.json
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
  --auth-har private/video-auth.har \
  --verify
```

Export a batch:

```bash
marco-polo export-batch ./exports \
  --sync-file private/sync.json \
  --auth-har private/video-auth.har \
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

## Security Notes

- Do not commit HAR files, sync JSON exports, raw `/mp4/video` bodies, transcripts, or exported videos unless you are certain they are safe to publish.
- Prefer a repo-local ignored `private/` or `tmp/` directory for captured auth and metadata.
- The CLI prints whether auth headers are present, but does not print token values.

## Native Export

`marco_polo_cli.native_export` contains an older Android/Frida-based path. It is kept for reference and unusual cases; the API/HAR workflow is the preferred path.

## Development

Run the fixture-backed test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The test fixtures are fake and intentionally contain no real tokens, transcripts, phone numbers, or Marco Polo media.
