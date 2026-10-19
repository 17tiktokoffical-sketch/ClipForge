"""Stage 3 — moment selection.

Two modes, both UI-agnostic (plain dicts in/out):

AUTO   — detect high-energy windows from the audio track. ffmpeg decodes
         the audio (and runs silencedetect); we score candidate windows by
         mean RMS energy weighted by speech fraction, then greedily pick
         the top-N non-overlapping windows.
MANUAL — turn timed transcript segments into labeled choices for the UI;
         the user's picks become duration-length windows centered on each
         chosen segment.
"""

import json
import subprocess

import numpy as np


def ffprobe_duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return float(json.loads(proc.stdout)["format"]["duration"])


def energy_curve(video_path, win_sec=1.0):
    """Decode mono 16kHz audio with ffmpeg; return (times, rms) arrays."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video_path,
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, timeout=900,
    )
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    sr = 16000
    n = int(sr * win_sec)
    if len(audio) < n:
        return np.array([0.0]), np.array([0.0])
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return np.arange(len(rms)) * win_sec, rms


def silence_spans(video_path, noise_db=-35, min_dur=0.4):
    """Parse ffmpeg silencedetect output into [(silence_start, silence_end)]."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", video_path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=900,
    )
    spans, cur = [], None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            try:
                cur = float(line.split("silence_start:")[1].split()[0])
            except (ValueError, IndexError):
                cur = None
        elif "silence_end:" in line and cur is not None:
            try:
                end = float(line.split("silence_end:")[1].split()[0])
                spans.append((cur, end))
            except (ValueError, IndexError):
                pass
            cur = None
    return spans


def _speech_fraction(start, end, spans):
    silent = sum(
        max(0.0, min(end, s0_) - max(start, s_)) for s_, s0_ in spans
    )
    return max(0.0, 1.0 - silent / max(1e-6, end - start))


def _mmss(sec):
    sec = int(max(0, sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def auto_select(video_path, clip_seconds, n_clips, min_speech=0.4,
                step_sec=None):
    """Pick top-N non-overlapping high-energy windows.

    Returns a list of {"start", "end", "score"} sorted by start time.
    May return fewer than n_clips when the video is short or mostly silent.
    """
    duration = ffprobe_duration(video_path)
    if duration <= 0:
        raise RuntimeError("could not read video duration")
    clip_seconds = min(float(clip_seconds), duration)
    step = step_sec or max(10.0, clip_seconds / 6.0)

    _times, rms = energy_curve(video_path)
    spans = silence_spans(video_path)

    def mean_rms(a, b):
        seg = rms[int(a):int(b)]
        return float(seg.mean()) if len(seg) else 0.0

    cands = []
    t = 0.0
    while t + clip_seconds <= duration + 1e-6:
        e = t + clip_seconds
        speech = _speech_fraction(t, e, spans)
        if speech >= min_speech:
            cands.append(
                {
                    "start": round(t, 2),
                    "end": round(e, 2),
                    "score": round(mean_rms(t, e) * speech, 6),
                }
            )
        t += step

    cands.sort(key=lambda c: c["score"], reverse=True)
    picked = []
    for c in cands:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"]
               for p in picked):
            picked.append(c)
            if len(picked) >= n_clips:
                break
    picked.sort(key=lambda c: c["start"])
    return picked


def segments_for_ui(transcript, max_items=200):
    """Turn timed transcript segments into labeled choices for the UI.

    Returns [{"index", "label", "start", "end"}]. The UI shows the labels;
    the caller maps chosen labels back via "index".
    """
    items = []
    for i, s in enumerate(transcript.get("segments", [])[:max_items]):
        preview = " ".join(s["text"].split()[:9])
        label = f"[#{i}] {_mmss(s['start'])}\u2013{_mmss(s['end'])} | {preview}"
        items.append(
            {"index": i, "label": label, "start": s["start"], "end": s["end"]}
        )
    return items


def manual_windows(transcript, indices, clip_seconds, video_duration):
    """Turn user-picked segment indices into duration-length windows.

    Each window is centered on its segment's midpoint, clamped to the video.
    Returns [{"start", "end", "score"}] sorted by start time.
    """
    segs = transcript.get("segments", [])
    out = []
    for i in sorted(set(indices)):
        if 0 <= i < len(segs):
            s = segs[i]
            mid = (s["start"] + s["end"]) / 2
            a = max(0.0, mid - clip_seconds / 2)
            b = min(float(video_duration), a + clip_seconds)
            a = max(0.0, b - clip_seconds)  # re-clamp the start
            out.append({"start": round(a, 2), "end": round(b, 2), "score": 1.0})
    out.sort(key=lambda w: w["start"])
    return out
