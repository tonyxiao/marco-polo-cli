#!/usr/bin/env python3
import argparse
import base64
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from marco_polo_cli.client import (
    DEFAULT_TOKEN_FILE,
    MarcoPoloClient,
    load_auth_from_har,
    load_token_file,
    save_token_file,
)

PRIVATE_DIR = Path("private")
EXPORTS_DIR = Path("exports")
TOKEN_FILE = DEFAULT_TOKEN_FILE
SYNC = Path(os.environ.get("MARCO_POLO_SYNC_FILE", PRIVATE_DIR / "sync.json"))
CONVERTER_MODULE = "marco_polo_cli.svp_to_mp4"
DEFAULT_COUNTRY_CODE = "US"
DEFAULT_FEATURE_FLAGS = []
DEFAULT_APP_BUILD = "27407632439"
DEFAULT_APP_VERSION = "0.579.0"
DEFAULT_PLATFORM_VERSION = "36"


def load_auth(token_file=None, profile="sync"):
    auth = load_token_file(token_file or TOKEN_FILE, profile)
    return auth.authorization, auth.x_auth_token


def request_json(url, token_file=None, profile="sync"):
    auth, xauth = load_auth(token_file, profile)
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth)
    if xauth:
        req.add_header("X-Auth-Token", xauth)
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def post_json(url, body, auth=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    if auth:
        req.add_header("Authorization", auth.authorization)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def random_urlsafe_token(num_bytes):
    return base64.urlsafe_b64encode(secrets.token_bytes(num_bytes)).decode().rstrip("=")


def generated_device_id():
    return "11." + random_urlsafe_token(16)


def anonymous_auth_body():
    return {
        "app_build": DEFAULT_APP_BUILD,
        "app_type": "mp",
        "app_version": DEFAULT_APP_VERSION,
        "carrier": "",
        "device_id": generated_device_id(),
        "feature_flags": DEFAULT_FEATURE_FLAGS,
        "flavor": "release",
        "locale": "en_US",
        "manufacturer": "Python",
        "model_name": "marco-polo-cli",
        "platform_type": "android",
        "platform_user_token": random_urlsafe_token(6),
        "platform_version": DEFAULT_PLATFORM_VERSION,
        "secret": random_urlsafe_token(16),
        "timezone": os.environ.get("TZ", "America/Los_Angeles"),
    }


def bootstrap_auth_token(token_file):
    from marco_polo_cli.client import AuthHeaders

    data = post_json("https://marcopolo.me/api/v4/auth", anonymous_auth_body())
    api_token = data.get("api_token")
    if not api_token:
        raise SystemExit("anonymous auth response did not contain api_token")
    auth = AuthHeaders(f"Bearer {api_token}", data.get("video_auth_token"))
    save_token_file(token_file, "sync", auth)
    if auth.x_auth_token:
        save_token_file(token_file, "video", auth)
    return auth


def get_bootstrap_auth(token_file):
    if token_file.exists():
        try:
            return load_token_file(token_file, "sync")
        except Exception:
            pass
    return bootstrap_auth_token(token_file)


def normalize_phone(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def print_status(label, ok, detail=""):
    state = "ok" if ok else "missing"
    suffix = f" - {detail}" if detail else ""
    print(f"{label}: {state}{suffix}")


def init_workspace(args):
    root = args.dir
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("*\n!.gitignore\n!README.md\n")
    readme = root / "README.md"
    if not readme.exists() or args.force:
        readme.write_text(
            "# Marco Polo private files\n\n"
            "Put sync metadata and temporary capture artifacts here. These files can contain "
            "private message metadata and should stay out of Git.\n\n"
            "Recommended names:\n\n"
            "- `sync.json` from `marco-polo sync`\n"
            "- temporary HAR captures only long enough to run `marco-polo auth import-har`\n"
        )
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"initialized {root}/, {EXPORTS_DIR}/, and token path {TOKEN_FILE}")
    print("next: create a token with `marco-polo auth login` or `marco-polo auth import-har`")


def doctor(args):
    print_status("python", True, sys.version.split()[0])
    print_status("ffmpeg", shutil.which("ffmpeg") is not None, shutil.which("ffmpeg") or "install for export/verify")
    print_status("ffprobe", shutil.which("ffprobe") is not None, shutil.which("ffprobe") or "install for --verify")
    print_status("token file", args.token_file.exists(), str(args.token_file))
    print_status("sync metadata", args.sync_file.exists(), str(args.sync_file))
    if args.token_file.exists():
        for profile in ("sync", "video"):
            try:
                auth, xauth = load_auth(args.token_file, profile)
                print(f"{profile} token: Authorization={'present' if auth else 'missing'} X-Auth-Token={'present' if xauth else 'missing'}")
            except Exception as exc:
                print(f"{profile} token: error - {type(exc).__name__}: {exc}")


def auth_import_har(args):
    auth = load_auth_from_har(args.har)
    save_token_file(args.token_file, args.profile, auth)
    print(f"wrote {args.profile} token to {args.token_file}")


def auth_bootstrap(args):
    bootstrap_auth_token(args.token_file)
    print(f"wrote anonymous bootstrap token to {args.token_file}")


def auth_login(args):
    phone = normalize_phone(args.phone)
    bootstrap_auth = get_bootstrap_auth(args.token_file)
    if not args.code:
        data = post_json(
            "https://marcopolo.me/api/v4/auth/send-phone-code",
            {
                "country_code": args.country_code,
                "delivery": args.delivery,
                "delivery_method": args.delivery,
                "existing_user_only": args.existing_user_only,
                "phone": phone,
            },
            bootstrap_auth,
        )
        code_length = data.get("code_length")
        detail = f" ({code_length} digits)" if code_length else ""
        print(f"verification code requested{detail}")
        return

    data = post_json(
        "https://marcopolo.me/api/v4/auth/verify-phone-code",
        {
            "collision_decision_input": True,
            "country_code": args.country_code,
            "feature_flags": DEFAULT_FEATURE_FLAGS,
            "phone": phone,
            "supports_email_verification": True,
            "verification_code": args.code,
        },
        bootstrap_auth,
    )
    api_token = data.get("api_token")
    if not api_token:
        raise SystemExit("verification succeeded but response did not contain api_token")
    auth_header = f"Bearer {api_token}"
    xauth = data.get("video_auth_token")
    from marco_polo_cli.client import AuthHeaders

    save_token_file(args.token_file, "sync", AuthHeaders(auth_header, xauth))
    save_token_file(args.token_file, "video", AuthHeaders(auth_header, xauth))
    suffix = " with video auth" if xauth else ""
    print(f"wrote sync and video tokens to {args.token_file}{suffix}")


def auth_check(args):
    auth, xauth = load_auth(args.token_file, args.profile)
    print(f"{args.profile} auth header: {'present' if auth else 'missing'}")
    print(f"{args.profile} x-auth-token header: {'present' if xauth else 'missing'}")
    if args.live:
        try:
            data = request_json("https://marcopolo.me/api/v4/conversations/sync", args.token_file, "sync")
            count = sum(1 for _ in iter_video_records(data))
            print(f"live sync: ok ({count} videos visible)")
        except urllib.error.HTTPError as exc:
            print(f"live sync: HTTP {exc.code} {exc.reason}")
    if args.video_id:
        _, _, video = find_video(args.sync_file, args.video_id)
        try:
            check_video_download_auth(args.video_id, video, args.token_file)
            print("video download: ok")
        except urllib.error.HTTPError as exc:
            print(f"video download: HTTP {exc.code} {exc.reason}")


def check_video_download_auth(video_id, video, token_file=None):
    auth, xauth = load_auth(token_file, "video")
    url = f"https://video-2-redirect.marcopolo.me/api/v4/videos/{video_id}/mp4/video"
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth)
    if xauth:
        req.add_header("X-Auth-Token", xauth)
    req.add_header("X-Read-Token", video["read_token"])
    req.add_header("Range", "bytes=0-0")
    req.add_header("X-Wait", "true")
    req.add_header("X-Wait-Seconds", "20")
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read(1)


def sync(args):
    data = request_json("https://marcopolo.me/api/v4/conversations/sync", args.token_file, "sync")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


def iter_conversations(data):
    if isinstance(data, list):
        return
    for section in ("conversations", "sync"):
        value = data.get(section)
        if isinstance(value, list):
            yield from value
        elif isinstance(value, dict):
            convs = value.get("conversations")
            if isinstance(convs, list):
                yield from convs


def message_entries(conv):
    messages = conv.get("messages", {})
    if isinstance(messages, dict):
        return messages.get("entries") or []
    return messages or []


def iter_video_records(data):
    if isinstance(data, list):
        for item in data:
            if item.get("video_id"):
                yield {
                    "conversation_id": item.get("conversation_id"),
                    "message_id": item.get("message_id"),
                    "video": item,
                    "transcript": item.get("transcript_text") or item.get("text_transcript") or "",
                }
        return
    for conv in iter_conversations(data):
        for msg in message_entries(conv):
            video = msg.get("video") or {}
            if video.get("video_id"):
                yield {
                    "conversation_id": conv.get("conversation_id"),
                    "message_id": msg.get("message_id"),
                    "video": video,
                    "transcript": video.get("text_transcript") or "",
                }


def convos(args):
    data = json.loads(args.sync_file.read_text())
    for conv in iter_conversations(data):
        members = ", ".join(
            " ".join(filter(None, [m.get("first_name"), m.get("last_name")])).strip()
            or m.get("user_id", "")
            for m in conv.get("members", [])
        )
        title = conv.get("title") or members
        print(f"{conv.get('conversation_id')}\t{title}")


def participants(args):
    data = json.loads(args.sync_file.read_text())
    for conv in iter_conversations(data):
        if args.conversation and conv.get("conversation_id") != args.conversation:
            continue
        members = conv.get("members", [])
        for member in members:
            name = " ".join(filter(None, [member.get("first_name"), member.get("last_name")])).strip()
            user_id = member.get("user_id") or member.get("external_id") or ""
            print(f"{conv.get('conversation_id')}\t{user_id}\t{name}")


def videos(args):
    data = json.loads(args.sync_file.read_text())
    for rec in iter_video_records(data):
        if args.conversation and rec["conversation_id"] != args.conversation:
            continue
        video = rec["video"]
        transcript = rec["transcript"].replace("\n", " ")
        print(
            f"{rec['conversation_id']}\t{rec['message_id']}\t{video.get('video_id')}\t"
            f"{video.get('duration_ms')}ms\t{transcript}"
        )


def find_video(sync_file, video_id):
    data = json.loads(sync_file.read_text())
    for rec in iter_video_records(data):
        video = rec["video"]
        if video.get("video_id") == video_id:
            return rec.get("conversation_id"), rec, video
    raise SystemExit(f"video not found in {sync_file}: {video_id}")


def download_raw(args):
    _, _, video = find_video(args.sync_file, args.video_id)
    download_video_raw(args.video_id, video, args.out, args.token_file)


def download_video_raw(video_id, video, out, token_file=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    auth, xauth = load_auth(token_file, "video")
    url = f"https://video-2-redirect.marcopolo.me/api/v4/videos/{video_id}/mp4/video"
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth)
    if xauth:
        req.add_header("X-Auth-Token", xauth)
    req.add_header("X-Read-Token", video["read_token"])
    req.add_header("Range", "bytes=0-")
    req.add_header("X-Wait", "true")
    req.add_header("X-Wait-Seconds", "20")
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=120) as resp, out.open("wb") as f:
        f.write(resp.read())
    print(f"wrote raw Marco Polo stream/package to {out}")


def find_cached_raw(video_id, raw_dir):
    exact = raw_dir / f"{video_id}.mp4"
    if exact.exists():
        return exact
    matches = sorted(raw_dir.glob(f"*-{video_id}.mp4"))
    if matches:
        return matches[0]
    return None


def export_standard_mp4(args):
    _, _, video = find_video(args.sync_file, args.video_id)
    export_one(
        args.video_id,
        video,
        args.out,
        sync_file=args.sync_file,
        token_file=args.token_file,
        raw=args.raw,
        raw_dir=args.raw_dir,
        info=args.info,
        verify=args.verify,
        keep_raw=args.keep_raw,
    )


def export_one(video_id, video, out, sync_file=SYNC, token_file=TOKEN_FILE, raw=None, raw_dir=None, info=None, verify=False, keep_raw=False):
    duration_ms = video.get("duration_ms")
    raw_path = raw
    temp_dir = None
    if raw_path is None and raw_dir:
        raw_path = find_cached_raw(video_id, raw_dir)
    if raw_path is None:
        if keep_raw:
            raw_dir = out.parent / "raw"
            raw_path = raw_dir / f"{video_id}.mp4"
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="marcopolo-raw-")
            raw_path = Path(temp_dir.name) / f"{video_id}.mp4"
        download_video_raw(video_id, video, raw_path, token_file)

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", CONVERTER_MODULE, str(raw_path), str(out), "--with-audio"]
    if duration_ms:
        cmd += ["--duration", str(float(duration_ms) / 1000)]
    if info:
        info.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--info", str(info)]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd)
    if verify:
        verify_mp4(out)
    if temp_dir:
        temp_dir.cleanup()
    return out


def video_filename(video_id):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in video_id) + ".mp4"


def select_records(sync_file, conversation=None, limit=None, sample=False, seed=20260620):
    data = json.loads(sync_file.read_text())
    records = [
        rec for rec in iter_video_records(data)
        if rec["video"].get("video_id") and (not conversation or rec["conversation_id"] == conversation)
    ]
    deduped = []
    seen = set()
    for rec in records:
        vid = rec["video"]["video_id"]
        if vid in seen:
            continue
        seen.add(vid)
        deduped.append(rec)
    if sample:
        rng = random.Random(seed)
        rng.shuffle(deduped)
    if limit:
        deduped = deduped[:limit]
    return deduped


def export_batch(args):
    records = select_records(args.sync_file, args.conversation, args.limit, args.sample, args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.raw_dir or (args.out_dir / "raw" if args.keep_raw else None)
    info_dir = args.out_dir / "info"
    results = []
    for rec in records:
        video = rec["video"]
        vid = video["video_id"]
        out = args.out_dir / video_filename(vid)
        info = info_dir / f"{vid}.json"
        item = {
            "video_id": vid,
            "conversation_id": rec["conversation_id"],
            "message_id": rec["message_id"],
            "duration_ms": video.get("duration_ms"),
            "output_path": str(out),
        }
        try:
            if out.exists() and not args.force:
                item["status"] = "skipped_exists"
            else:
                export_one(
                    vid,
                    video,
                    out,
                    sync_file=args.sync_file,
                    token_file=args.token_file,
                    raw_dir=raw_dir,
                    info=info,
                    verify=args.verify,
                    keep_raw=args.keep_raw,
                )
                item["status"] = "ok"
                item["info_path"] = str(info)
        except urllib.error.HTTPError as exc:
            item["status"] = f"http_{exc.code}"
            item["error"] = exc.reason
        except Exception as exc:
            item["status"] = "error"
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
        print(f"{vid}\t{item['status']}\t{out}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"wrote {args.report}")


def verify_mp4(path):
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,sample_rate,channels,duration,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    probe = subprocess.run(probe_cmd, check=True, text=True, stdout=subprocess.PIPE)
    data = json.loads(probe.stdout)
    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams)
    if not has_video or not has_audio:
        raise RuntimeError(f"missing expected streams in {path}: h264={has_video} aac={has_audio}")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], check=True)
    return data


def verify_outputs(args):
    paths = []
    for item in args.paths:
        if item.is_dir():
            paths.extend(sorted(item.glob("*.mp4")))
        else:
            paths.append(item)
    results = []
    for path in paths:
        item = {"path": str(path)}
        try:
            probe = verify_mp4(path)
            item["status"] = "ok"
            item["streams"] = probe.get("streams", [])
            item["duration"] = probe.get("format", {}).get("duration")
        except Exception as exc:
            item["status"] = "error"
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
        print(f"{path}\t{item['status']}")
    if args.report:
        args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"wrote {args.report}")


def print_transcript(args):
    client = MarcoPoloClient(sync_file=args.sync_file, token_file=args.token_file)
    record = client.find_video(args.video_id)
    data = {
        "video_id": record.video_id,
        "conversation_id": record.conversation_id,
        "message_id": record.message_id,
        "conversation_title": record.conversation_title,
        "duration_ms": record.duration_ms,
        "transcript": record.transcript,
    }
    text = json.dumps(data, indent=2, ensure_ascii=False) if args.json else record.transcript
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)


def print_actionables(args):
    client = MarcoPoloClient(sync_file=args.sync_file, token_file=args.token_file)
    result = client.actionables(args.video_id)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)


def search_videos(args):
    client = MarcoPoloClient(sync_file=args.sync_file, token_file=args.token_file)
    records = client.videos(conversation_id=args.conversation, query=args.query)
    if args.sample:
        records = client.sample_videos(args.limit or len(records), query=args.query, seed=args.seed)
    elif args.limit:
        records = records[: args.limit]
    for rec in records:
        print(
            f"{rec.conversation_id}\t{rec.message_id}\t{rec.video_id}\t"
            f"{rec.duration_ms}ms\t{rec.conversation_title}\t{rec.transcript[:220]}"
        )


def main():
    parser = argparse.ArgumentParser(
        prog="marco-polo",
        description="Export Marco Polo metadata, transcripts, and videos through the reverse-engineered app API.",
    )
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("init", help="create ignored private/export directories for a new checkout")
    p.add_argument("--dir", type=Path, default=PRIVATE_DIR, help="private directory for sync metadata and temporary captures")
    p.add_argument("--force", action="store_true", help="rewrite the private README")
    p.set_defaults(func=init_workspace)

    p = sub.add_parser("doctor", help="check local tools, token file, and sync metadata")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=doctor)

    auth = sub.add_parser("auth", help="manage Marco Polo API tokens")
    auth_sub = auth.add_subparsers(required=True)

    p = auth_sub.add_parser("login", help="request and verify a phone login code")
    p.add_argument("phone")
    p.add_argument("--code")
    p.add_argument("--country-code", default=DEFAULT_COUNTRY_CODE)
    p.add_argument("--delivery", default="sms", choices=["sms", "whatsapp"])
    p.add_argument("--existing-user-only", action="store_true")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.set_defaults(func=auth_login)

    p = auth_sub.add_parser("bootstrap", help="create an anonymous bootstrap token without requesting a phone code")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.set_defaults(func=auth_bootstrap)

    p = auth_sub.add_parser("import-har", help="extract auth headers from one captured HAR into the token file")
    p.add_argument("har", type=Path)
    p.add_argument("--profile", choices=["sync", "video"], default="sync")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.set_defaults(func=auth_import_har)

    p = auth_sub.add_parser("check", help="verify saved token headers, optionally against live API calls")
    p.add_argument("--profile", choices=["sync", "video"], default="sync")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--live", action="store_true", help="call conversations/sync to prove auth is current")
    p.add_argument("--video-id", help="test auth against one video download endpoint")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=auth_check)

    p = sub.add_parser("auth-check", help="deprecated alias for `auth check`")
    p.add_argument("--profile", choices=["sync", "video"], default="sync")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--live", action="store_true", help="call conversations/sync to prove auth is current")
    p.add_argument("--video-id", help="test auth against one video download endpoint")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=auth_check)

    p = sub.add_parser("sync", help="refresh conversations/sync metadata using the saved token")
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--out", type=Path, default=SYNC)
    p.set_defaults(func=sync)

    p = sub.add_parser("convos", aliases=["conversations"], help="list conversations from sync metadata")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=convos)

    p = sub.add_parser("participants", help="list conversation participants from sync metadata")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--conversation")
    p.set_defaults(func=participants)

    p = sub.add_parser("videos", help="list videos from sync metadata")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--conversation")
    p.set_defaults(func=videos)

    p = sub.add_parser("search-videos", help="search videos by id, conversation title, or transcript text")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--conversation")
    p.add_argument("--query")
    p.add_argument("--limit", type=int)
    p.add_argument("--sample", action="store_true")
    p.add_argument("--seed", type=int, default=20260620)
    p.set_defaults(func=search_videos)

    p = sub.add_parser("download-raw", help="download the raw /mp4/video response body")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=download_raw)

    p = sub.add_parser("standard-mp4", help="export one Marco Polo video as standard MP4")
    p.add_argument("video_id")
    p.add_argument("out", type=Path)
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--raw", type=Path, help="already-downloaded /mp4/video body")
    p.add_argument("--raw-dir", type=Path, help="directory containing cached raw video bodies")
    p.add_argument("--info", type=Path, help="write converter parse info JSON")
    p.add_argument("--verify", action="store_true", help="ffprobe and decode-check the output")
    p.add_argument("--keep-raw", action="store_true", help="save downloaded raw API body next to output")
    p.set_defaults(func=export_standard_mp4)

    p = sub.add_parser("export-batch", help="export multiple videos as standard MP4 files")
    p.add_argument("out_dir", type=Path)
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--conversation")
    p.add_argument("--limit", type=int)
    p.add_argument("--sample", action="store_true", help="shuffle before applying --limit")
    p.add_argument("--seed", type=int, default=20260620)
    p.add_argument("--raw-dir", type=Path, help="reuse or write raw API bodies in this directory")
    p.add_argument("--keep-raw", action="store_true", help="save downloaded raw API bodies under out_dir/raw")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--report", type=Path)
    p.set_defaults(func=export_batch)

    p = sub.add_parser("verify", help="verify exported MP4 files with ffprobe/ffmpeg")
    p.add_argument("paths", type=Path, nargs="+")
    p.add_argument("--report", type=Path)
    p.set_defaults(func=verify_outputs)

    p = sub.add_parser("transcript", help="print or save a transcript from sync metadata")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=print_transcript)

    p = sub.add_parser("actionables", help="extract simple action items from a transcript")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--out", type=Path)
    p.set_defaults(func=print_actionables)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
