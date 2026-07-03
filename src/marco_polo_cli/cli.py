#!/usr/bin/env python3
import argparse
import json
import random
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from marco_polo_cli.client import MarcoPoloClient

HAR = Path("/tmp/marcopolo-conversations-flow.har")
SYNC = Path("/tmp/marcopolo-current-sync.json")
CONVERTER_MODULE = "marco_polo_cli.svp_to_mp4"


def load_auth(har_path=None):
    har_path = har_path or HAR
    har = json.loads(har_path.read_text())
    fallback = None
    for entry in har.get("log", {}).get("entries", []):
        req = entry.get("request", {})
        headers = {h["name"].lower(): h["value"] for h in req.get("headers", [])}
        auth = headers.get("authorization")
        xauth = headers.get("x-auth-token")
        if not auth:
            continue
        if "/api/v4/conversations/sync" in req.get("url", ""):
            return auth, xauth
        if fallback is None:
            fallback = (auth, xauth)
    if fallback:
        return fallback
    raise SystemExit(f"no auth headers found in {har_path}")


def request_json(url, auth_har=None):
    auth, xauth = load_auth(auth_har)
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth)
    if xauth:
        req.add_header("X-Auth-Token", xauth)
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def auth_check(args):
    auth, xauth = load_auth(args.auth_har)
    print(f"auth header: {'present' if auth else 'missing'}")
    print(f"x-auth-token header: {'present' if xauth else 'missing'}")
    if args.live:
        try:
            data = request_json("https://marcopolo.me/api/v4/conversations/sync", args.auth_har)
            count = sum(1 for _ in iter_video_records(data))
            print(f"live sync: ok ({count} videos visible)")
        except urllib.error.HTTPError as exc:
            print(f"live sync: HTTP {exc.code} {exc.reason}")
    if args.video_id:
        _, _, video = find_video(args.sync_file, args.video_id)
        try:
            check_video_download_auth(args.video_id, video, args.auth_har)
            print("video download: ok")
        except urllib.error.HTTPError as exc:
            print(f"video download: HTTP {exc.code} {exc.reason}")


def check_video_download_auth(video_id, video, auth_har=None):
    auth, xauth = load_auth(auth_har)
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
    data = request_json("https://marcopolo.me/api/v4/conversations/sync", args.auth_har)
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
    download_video_raw(args.video_id, video, args.out, args.auth_har)


def download_video_raw(video_id, video, out, auth_har=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    auth, xauth = load_auth(auth_har)
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
        auth_har=args.auth_har,
        raw=args.raw,
        raw_dir=args.raw_dir,
        info=args.info,
        verify=args.verify,
        keep_raw=args.keep_raw,
    )


def export_one(video_id, video, out, sync_file=SYNC, auth_har=HAR, raw=None, raw_dir=None, info=None, verify=False, keep_raw=False):
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
        download_video_raw(video_id, video, raw_path, auth_har)

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
                    auth_har=args.auth_har,
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
    client = MarcoPoloClient(sync_file=args.sync_file, auth_har=args.auth_har)
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
    client = MarcoPoloClient(sync_file=args.sync_file, auth_har=args.auth_har)
    result = client.actionables(args.video_id)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)


def search_videos(args):
    client = MarcoPoloClient(sync_file=args.sync_file, auth_har=args.auth_har)
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
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("auth-check")
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--live", action="store_true", help="call conversations/sync to prove auth is current")
    p.add_argument("--video-id", help="test auth against one video download endpoint")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=auth_check)

    p = sub.add_parser("sync")
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--out", type=Path, default=SYNC)
    p.set_defaults(func=sync)

    p = sub.add_parser("convos")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.set_defaults(func=convos)

    p = sub.add_parser("videos")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--conversation")
    p.set_defaults(func=videos)

    p = sub.add_parser("search-videos")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--conversation")
    p.add_argument("--query")
    p.add_argument("--limit", type=int)
    p.add_argument("--sample", action="store_true")
    p.add_argument("--seed", type=int, default=20260620)
    p.set_defaults(func=search_videos)

    p = sub.add_parser("download-raw")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=download_raw)

    p = sub.add_parser("standard-mp4")
    p.add_argument("video_id")
    p.add_argument("out", type=Path)
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--raw", type=Path, help="already-downloaded /mp4/video body")
    p.add_argument("--raw-dir", type=Path, help="directory containing cached raw video bodies")
    p.add_argument("--info", type=Path, help="write converter parse info JSON")
    p.add_argument("--verify", action="store_true", help="ffprobe and decode-check the output")
    p.add_argument("--keep-raw", action="store_true", help="save downloaded raw API body next to output")
    p.set_defaults(func=export_standard_mp4)

    p = sub.add_parser("export-batch")
    p.add_argument("out_dir", type=Path)
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
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

    p = sub.add_parser("verify")
    p.add_argument("paths", type=Path, nargs="+")
    p.add_argument("--report", type=Path)
    p.set_defaults(func=verify_outputs)

    p = sub.add_parser("transcript")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=print_transcript)

    p = sub.add_parser("actionables")
    p.add_argument("video_id")
    p.add_argument("--sync-file", type=Path, default=SYNC)
    p.add_argument("--auth-har", type=Path, default=HAR)
    p.add_argument("--out", type=Path)
    p.set_defaults(func=print_actionables)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
