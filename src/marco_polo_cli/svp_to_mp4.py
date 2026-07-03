#!/usr/bin/env python3
import argparse
import json
import statistics
import subprocess
import tempfile
from pathlib import Path


VIDEO_PACKET_KIND = 0x01
AUDIO_PACKET_KIND = 0x02
EXTRADATA_PACKET_KIND = 0x03
AAC_SAMPLE_RATE = 48000
AAC_CHANNELS = 1
AAC_PROFILE_LC = 2
AAC_SAMPLE_RATES = {
    96000: 0,
    88200: 1,
    64000: 2,
    48000: 3,
    44100: 4,
    32000: 5,
    24000: 6,
    22050: 7,
    16000: 8,
    12000: 9,
    11025: 10,
    8000: 11,
    7350: 12,
}


def find_avcc(data):
    if not data.startswith(b"\x00\x00\x00 ftyp"):
        raise ValueError(
            "input does not start with a complete Marco Polo /mp4/video body; "
            "use Range: bytes=0- instead of a resumed mid-file byte range"
        )
    for off in range(48, min(len(data) - 16, 256)):
        if data[off] == 1 and data[off + 1] in (0x42, 0x4D, 0x58, 0x64):
            if data[off + 4] & 0xFC == 0xFC and data[off + 5] & 0xE0 == 0xE0:
                return off
    raise ValueError("could not find AVCDecoderConfigurationRecord")


def parse_avcc(data, off):
    pos = off
    configuration_version = data[pos]
    if configuration_version != 1:
        raise ValueError(f"unexpected avcC version at {off}: {configuration_version}")
    nal_length_size = (data[pos + 4] & 3) + 1
    pos += 5
    sps_count = data[pos] & 0x1F
    pos += 1
    sps = []
    for _ in range(sps_count):
        n = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
        sps.append(data[pos : pos + n])
        pos += n
    pps_count = data[pos]
    pos += 1
    pps = []
    for _ in range(pps_count):
        n = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
        pps.append(data[pos : pos + n])
        pos += n
    return {"offset": off, "end": pos, "nal_length_size": nal_length_size, "sps": sps, "pps": pps}


def find_first_chunk(data, start):
    candidates = []
    for marker in (b"\x8a\x03", b"\x8a\x02", b"\x8a\x01", b"\x8a\x11"):
        idx = data.find(marker, start)
        if idx >= 0:
            candidates.append(idx)
    if not candidates:
        raise ValueError("could not find first media chunk")
    return min(candidates)


def iter_chunks(data, start):
    pos = start
    while pos < len(data):
        if pos + 10 > len(data):
            break
        if data[pos] != 0x8A:
            raise ValueError(f"bad chunk marker at 0x{pos:x}: {data[pos:pos+16].hex()}")
        chunk_type = data[pos + 1]
        packet_kind = chunk_type & 0x0F
        length = int.from_bytes(data[pos + 2 : pos + 6], "little")
        duration_us = int.from_bytes(data[pos + 6 : pos + 10], "little", signed=True)
        payload_start = pos + 10
        payload_end = payload_start + length
        if payload_end > len(data):
            raise ValueError(f"chunk at 0x{pos:x} overruns file")
        yield {
            "offset": pos,
            "type": chunk_type,
            "kind": packet_kind,
            "duration_us": duration_us,
            "payload": data[payload_start:payload_end],
        }
        pos = payload_end


def extract_h264_annexb(data):
    avcc = parse_avcc(data, find_avcc(data))
    start = find_first_chunk(data, avcc["end"])
    chunks = list(iter_chunks(data, start))

    out = bytearray()
    for nalu in avcc["sps"] + avcc["pps"]:
        out += b"\x00\x00\x00\x01" + nalu

    video_nalus = 0
    access_units = 0
    for chunk in chunks:
        if chunk["kind"] != VIDEO_PACKET_KIND:
            continue
        payload = chunk["payload"]
        if len(payload) < 5:
            continue
        declared = int.from_bytes(payload[:4], "big")
        nalu = payload[4:]
        if declared and declared <= len(nalu):
            nalu = nalu[:declared]
        nal_type = nalu[0] & 0x1F
        if nal_type not in {1, 5, 6, 7, 8}:
            continue
        out += b"\x00\x00\x00\x01" + nalu
        video_nalus += 1
        access_units += 1

    return bytes(out), {
        "chunk_count": len(chunks),
        "video_nalu_count": video_nalus,
        "video_frame_count": access_units,
        "avcc_offset": avcc["offset"],
        "first_chunk_offset": start,
        "sps_count": len(avcc["sps"]),
        "pps_count": len(avcc["pps"]),
    }


def infer_aac_sample_rate(durations_us):
    usable = [d for d in durations_us if d > 1000]
    if not usable:
        return AAC_SAMPLE_RATE
    median_duration = statistics.median(usable)
    estimated = round(1024 * 1_000_000 / median_duration)
    return min(AAC_SAMPLE_RATES, key=lambda rate: abs(rate - estimated))


def adts_header(frame_size, sample_rate=AAC_SAMPLE_RATE, channels=AAC_CHANNELS):
    sample_rate_index = AAC_SAMPLE_RATES[sample_rate]
    full_size = frame_size + 7
    profile_minus_one = AAC_PROFILE_LC - 1
    return bytes(
        [
            0xFF,
            0xF1,
            (profile_minus_one << 6) | (sample_rate_index << 2) | ((channels >> 2) & 1),
            ((channels & 3) << 6) | ((full_size >> 11) & 3),
            (full_size >> 3) & 0xFF,
            ((full_size & 7) << 5) | 0x1F,
            0xFC,
        ]
    )


def extract_aac_adts(data):
    avcc = parse_avcc(data, find_avcc(data))
    start = find_first_chunk(data, avcc["end"])
    chunks = list(iter_chunks(data, start))
    durations = []
    frames = []
    for chunk in chunks:
        if chunk["kind"] != AUDIO_PACKET_KIND:
            continue
        payload = chunk["payload"]
        if not payload:
            continue
        frames.append(payload)
        durations.append(chunk["duration_us"])
    sample_rate = infer_aac_sample_rate(durations)
    out = bytearray()
    for frame in frames:
        out += adts_header(len(frame), sample_rate=sample_rate) + frame
    return bytes(out), {
        "audio_frame_count": len(frames),
        "audio_duration_us": sum(d for d in durations if d > 0),
        "audio_sample_rate": sample_rate,
    }


def convert(input_path, output_path, duration=None, fps=None, with_audio=False):
    data = input_path.read_bytes()
    annexb, info = extract_h264_annexb(data)
    adts, audio_info = extract_aac_adts(data)
    info.update(audio_info)
    if info["video_frame_count"] == 0:
        raise ValueError("no video frames extracted")
    if fps is None:
        fps = info["video_frame_count"] / duration if duration else 10.0

    with tempfile.TemporaryDirectory(prefix="marcopolo-svp-") as td:
        h264 = Path(td) / "video.h264"
        h264.write_bytes(annexb)
        aac = Path(td) / "audio.aac"
        if adts:
            aac.write_bytes(adts)
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-r",
            f"{fps:.6f}",
            "-i",
            str(h264),
        ]
        if adts and with_audio:
            cmd += ["-i", str(aac)]
        cmd += [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ]
        if adts and with_audio:
            cmd += ["-c:a", "aac", "-ar", str(info["audio_sample_rate"]), "-ac", str(AAC_CHANNELS), "-shortest"]
        cmd += [
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.check_call(cmd)
    return info | {"fps": fps}


def main():
    parser = argparse.ArgumentParser(description="Marco Polo raw /mp4/video body to standard MP4 converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, help="known duration in seconds; used to derive frame rate")
    parser.add_argument("--fps", type=float, help="override frame rate")
    parser.add_argument("--with-audio", action="store_true", help="mux extracted AAC audio")
    parser.add_argument("--info", type=Path, help="write parse info JSON")
    args = parser.parse_args()

    info = convert(args.input, args.output, duration=args.duration, fps=args.fps, with_audio=args.with_audio)
    if args.info:
        args.info.write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
