#!/usr/bin/env python3
"""Small API-only Marco Polo client wrapper.

The client intentionally keeps authentication simple: pass a HAR file captured
from a successful Marco Polo request and the client reuses its auth headers.
Do not commit HAR files or token values.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SYNC = Path(os.environ.get("MARCO_POLO_SYNC_FILE", "private/sync.json"))
DEFAULT_AUTH_HAR = Path(os.environ.get("MARCO_POLO_AUTH_HAR", "private/sync-auth.har"))
DEFAULT_VIDEO_AUTH_HAR = Path(os.environ.get("MARCO_POLO_VIDEO_AUTH_HAR", "private/video-auth.har"))
DEFAULT_CONVERTER = Path(__file__).with_name("svp_to_mp4.py")
DEFAULT_CONVERTER_MODULE = "marco_polo_cli.svp_to_mp4"


@dataclass(frozen=True)
class AuthHeaders:
    authorization: str
    x_auth_token: str | None = None


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    title: str
    members: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    message_id: str | None
    conversation_id: str | None
    duration_ms: int | None
    read_token: str | None
    transcript: str
    conversation_title: str = ""
    raw: dict[str, Any] | None = None


def load_auth_from_har(path: Path) -> AuthHeaders:
    har = json.loads(path.read_text())
    fallback = None
    for entry in har.get("log", {}).get("entries", []):
        req = entry.get("request", {})
        headers = {h["name"].lower(): h["value"] for h in req.get("headers", [])}
        auth = headers.get("authorization")
        xauth = headers.get("x-auth-token")
        if not auth:
            continue
        found = AuthHeaders(auth, xauth)
        if "/api/v4/conversations/sync" in req.get("url", ""):
            return found
        if fallback is None:
            fallback = found
    if fallback:
        return fallback
    raise ValueError(f"no Authorization header found in {path}")


def _request_json(url: str, auth: AuthHeaders) -> Any:
    req = urllib.request.Request(url)
    req.add_header("Authorization", auth.authorization)
    if auth.x_auth_token:
        req.add_header("X-Auth-Token", auth.x_auth_token)
    req.add_header("User-Agent", "okhttp/5.3.2")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _iter_conversation_dicts(data: Any) -> Iterable[dict[str, Any]]:
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


def _message_entries(conv: dict[str, Any]) -> list[dict[str, Any]]:
    messages = conv.get("messages", {})
    if isinstance(messages, dict):
        return messages.get("entries") or []
    return messages or []


def _member_name(member: dict[str, Any]) -> str:
    return " ".join(filter(None, [member.get("first_name"), member.get("last_name")])).strip() or member.get("user_id", "")


def _conversation_title(conv: dict[str, Any]) -> str:
    members = [_member_name(m) for m in conv.get("members", [])]
    return conv.get("title") or ", ".join(m for m in members if m)


def _video_filename(video_id: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in video_id) + ".mp4"


class MarcoPoloClient:
    def __init__(
        self,
        sync_file: Path = DEFAULT_SYNC,
        auth_har: Path | None = DEFAULT_AUTH_HAR,
        video_auth_har: Path | None = DEFAULT_VIDEO_AUTH_HAR,
        converter: Path = DEFAULT_CONVERTER,
    ) -> None:
        self.sync_file = Path(sync_file)
        self.auth_har = Path(auth_har) if auth_har else None
        self.video_auth_har = Path(video_auth_har) if video_auth_har else None
        self.converter = Path(converter)
        self._sync_data: Any | None = None
        self._auth: AuthHeaders | None = None
        self._video_auth: AuthHeaders | None = None

    @property
    def auth(self) -> AuthHeaders:
        if self._auth is None:
            if not self.auth_har:
                raise ValueError("auth_har is required for live API calls")
            self._auth = load_auth_from_har(self.auth_har)
        return self._auth

    @property
    def video_auth(self) -> AuthHeaders:
        if self._video_auth is None:
            if self.video_auth_har and self.video_auth_har.exists():
                self._video_auth = load_auth_from_har(self.video_auth_har)
            else:
                self._video_auth = self.auth
        return self._video_auth

    def sync(self, out: Path | None = None) -> Any:
        data = _request_json("https://marcopolo.me/api/v4/conversations/sync", self.auth)
        self._sync_data = data
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.sync_file = out
        return data

    def load_sync(self) -> Any:
        if self._sync_data is None:
            self._sync_data = json.loads(self.sync_file.read_text())
        return self._sync_data

    def conversations(self) -> list[Conversation]:
        data = self.load_sync()
        out = []
        for conv in _iter_conversation_dicts(data):
            members = tuple(_member_name(m) for m in conv.get("members", []))
            out.append(Conversation(conv.get("conversation_id", ""), _conversation_title(conv), members, conv))
        return out

    def videos(self, conversation_id: str | None = None, query: str | None = None) -> list[VideoRecord]:
        data = self.load_sync()
        conv_titles = {c.conversation_id: c.title for c in self.conversations()}
        records: list[VideoRecord] = []
        if isinstance(data, list):
            for item in data:
                if item.get("video_id"):
                    records.append(
                        VideoRecord(
                            item["video_id"],
                            item.get("message_id"),
                            item.get("conversation_id"),
                            item.get("duration_ms"),
                            item.get("read_token"),
                            item.get("transcript_text") or item.get("text_transcript") or "",
                            raw=item,
                        )
                    )
        else:
            for conv in _iter_conversation_dicts(data):
                cid = conv.get("conversation_id")
                if conversation_id and cid != conversation_id:
                    continue
                title = _conversation_title(conv)
                for msg in _message_entries(conv):
                    video = msg.get("video") or {}
                    vid = video.get("video_id")
                    if not vid:
                        continue
                    records.append(
                        VideoRecord(
                            vid,
                            msg.get("message_id"),
                            cid,
                            video.get("duration_ms"),
                            video.get("read_token"),
                            video.get("text_transcript") or "",
                            title,
                            video,
                        )
                    )
        deduped = []
        seen = set()
        for rec in records:
            if rec.video_id in seen:
                continue
            seen.add(rec.video_id)
            if query:
                haystack = " ".join([rec.video_id, rec.conversation_title, rec.transcript]).lower()
                if query.lower() not in haystack:
                    continue
            deduped.append(rec if rec.conversation_title else VideoRecord(**{**rec.__dict__, "conversation_title": conv_titles.get(rec.conversation_id or "", "")}))
        return deduped

    def sample_videos(self, limit: int, query: str | None = None, seed: int = 20260620) -> list[VideoRecord]:
        records = self.videos(query=query)
        rng = random.Random(seed)
        rng.shuffle(records)
        return records[:limit]

    def find_video(self, video_id: str) -> VideoRecord:
        for record in self.videos():
            if record.video_id == video_id:
                return record
        raise KeyError(f"video not found: {video_id}")

    def transcript(self, video_id: str) -> str:
        return self.find_video(video_id).transcript

    def download_raw_video(self, video: VideoRecord | str, out: Path) -> Path:
        record = self.find_video(video) if isinstance(video, str) else video
        if not record.read_token:
            raise ValueError(f"video has no read_token: {record.video_id}")
        out.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://video-2-redirect.marcopolo.me/api/v4/videos/{record.video_id}/mp4/video"
        req = urllib.request.Request(url)
        req.add_header("Authorization", self.video_auth.authorization)
        if self.video_auth.x_auth_token:
            req.add_header("X-Auth-Token", self.video_auth.x_auth_token)
        req.add_header("X-Read-Token", record.read_token)
        req.add_header("Range", "bytes=0-")
        req.add_header("X-Wait", "true")
        req.add_header("X-Wait-Seconds", "20")
        req.add_header("User-Agent", "okhttp/5.3.2")
        with urllib.request.urlopen(req, timeout=120) as resp, out.open("wb") as f:
            f.write(resp.read())
        return out

    def export_mp4(
        self,
        video: VideoRecord | str,
        out: Path,
        raw: Path | None = None,
        keep_raw: bool = False,
        verify: bool = False,
        info: Path | None = None,
    ) -> Path:
        record = self.find_video(video) if isinstance(video, str) else video
        temp_dir = None
        if raw is None:
            if keep_raw:
                raw = out.parent / "raw" / f"{record.video_id}.mp4"
            else:
                temp_dir = tempfile.TemporaryDirectory(prefix="marcopolo-raw-")
                raw = Path(temp_dir.name) / f"{record.video_id}.mp4"
            self.download_raw_video(record, raw)
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.converter == DEFAULT_CONVERTER:
            cmd = [sys.executable, "-m", DEFAULT_CONVERTER_MODULE, str(raw), str(out), "--with-audio"]
        else:
            cmd = [str(self.converter), str(raw), str(out), "--with-audio"]
        if record.duration_ms:
            cmd += ["--duration", str(float(record.duration_ms) / 1000)]
        if info:
            info.parent.mkdir(parents=True, exist_ok=True)
            cmd += ["--info", str(info)]
        subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if verify:
            self.verify_mp4(out)
        if temp_dir:
            temp_dir.cleanup()
        return out

    def verify_mp4(self, path: Path) -> dict[str, Any]:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,sample_rate,channels,duration,nb_frames",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        data = json.loads(probe.stdout)
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams)
        if not has_video or not has_audio:
            raise RuntimeError(f"missing expected streams in {path}: h264={has_video} aac={has_audio}")
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], check=True)
        return data

    def actionables(self, video_id: str) -> dict[str, Any]:
        record = self.find_video(video_id)
        return extract_actionables(record.transcript, video_id=record.video_id, conversation=record.conversation_title)


def extract_actionables(transcript: str, video_id: str | None = None, conversation: str | None = None) -> dict[str, Any]:
    text = " ".join(transcript.split())
    sentences = re.split(r"(?<=[.!?])\s+|(?<=\bso)\s+(?=[A-Z])", text)
    action_keywords = (
        "need",
        "needs",
        "should",
        "can we",
        "make sure",
        "please",
        "fix",
        "check",
        "contact",
        "get ",
        "put ",
        "open ",
        "return",
    )
    items = []
    low_text = text.lower()
    if "pressure" in low_text and ("sink" in low_text or "water" in low_text):
        items.append(
            {
                "text": "Check the sink/water pressure and determine whether a plumber or pressure adjustment is needed.",
                "source": "property_issue_pattern",
            }
        )
    if "dishwasher" in low_text and any(word in low_text for word in ("hard to open", "jamming", "handle", "dirt")):
        items.append(
            {
                "text": "Inspect and clean or repair the dishwasher handle/door area that is jamming.",
                "source": "property_issue_pattern",
            }
        )
    if "leak" in low_text:
        items.append(
            {
                "text": "Confirm whether there is an active leak and document the affected fixture or area.",
                "source": "property_issue_pattern",
            }
        )
    for sentence in sentences:
        clean = sentence.strip(" ,.;")
        if not clean:
            continue
        low = clean.lower()
        if any(k in low for k in action_keywords):
            candidate = {"text": clean, "source": "transcript_keyword"}
            if candidate not in items:
                items.append(candidate)
    if not items and text:
        items.append({"text": text[:240], "source": "summary_fallback"})
    return {
        "video_id": video_id,
        "conversation": conversation,
        "transcript": transcript,
        "actionables": items[:8],
    }


def filename_for_video(video_id: str) -> str:
    return _video_filename(video_id)
