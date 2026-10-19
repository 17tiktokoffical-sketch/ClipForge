"""Stage 1 — download the source video and any available captions via yt-dlp.

Uses the installed yt-dlp binary exactly as-is (never self-updates it).
UI-agnostic: plain arguments in, plain dicts out.
"""

import glob
import json
import os
import shutil
import subprocess

# Prefer the pinned workspace binary, fall back to PATH, then the module.
_CANDIDATES = [
    os.path.expanduser("~/workspace/bin/yt-dlp"),
    shutil.which("yt-dlp"),
]


def _ytdlp_cmd():
    for c in _CANDIDATES:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return [c]
    return ["python3", "-m", "yt_dlp"]  # last resort


def _run_once(args, timeout):
    proc = subprocess.run(
        _ytdlp_cmd() + args, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown error")[-2000:]
        raise RuntimeError(f"yt-dlp failed: {detail}")
    return proc.stdout.strip()


def _run(args, timeout=600):
    """Run yt-dlp, raise RuntimeError with a readable tail on failure.

    Verifies TLS by default; on machines with an intercepting egress proxy
    (self-signed chain), retries once with --no-check-certificates.
    """
    try:
        return _run_once(args, timeout)
    except RuntimeError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            return _run_once(["--no-check-certificates"] + args, timeout)
        raise


def fetch_metadata(url):
    """Return {'title': str, 'duration': float} without downloading anything."""
    out = _run(["--no-playlist", "--skip-download", "--dump-json", url])
    info = json.loads(out)
    return {
        "title": info.get("title") or "video",
        "duration": float(info.get("duration") or 0),
    }


def fetch_subtitles(url, outdir):
    """Try downloading English vtt subtitles/auto-captions.

    Returns the .vtt path, or None when the video has no captions.
    """
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, "subs")
    try:
        _run(
            [
                "--no-playlist", "--skip-download",
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "en.*",
                "--sub-format", "vtt/best",
                "-o", base + ".%(ext)s",
                url,
            ],
            timeout=300,
        )
    except RuntimeError:
        return None
    cands = sorted(glob.glob(base + "*.vtt"))
    if not cands:
        return None
    # Prefer human-made English subs over auto-generated ones.
    for c in cands:
        if ".en.vtt" in c:
            return c
    return cands[0]


def download_video(url, outdir):
    """Download best mp4 at <=720p (keeps files light).

    Returns {'video': path, 'sub': path|None, 'title': str, 'duration': float}.
    Raises RuntimeError on failure.
    """
    os.makedirs(outdir, exist_ok=True)
    meta = fetch_metadata(url)
    sub = fetch_subtitles(url, outdir)
    _run(
        [
            "--no-playlist",
            "-f",
            "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba"
            "/b[height<=720]/b",
            "--merge-output-format", "mp4",
            "-o", os.path.join(outdir, "source.%(ext)s"),
            url,
        ],
        timeout=3600,
    )
    video = os.path.join(outdir, "source.mp4")
    if not os.path.isfile(video):
        got = [
            g for g in glob.glob(os.path.join(outdir, "source.*"))
            if not g.endswith(".vtt")
        ]
        if not got:
            raise RuntimeError("yt-dlp finished but no video file was found.")
        video = got[0]
    return {
        "video": video,
        "sub": sub,
        "title": meta["title"],
        "duration": meta["duration"],
    }
