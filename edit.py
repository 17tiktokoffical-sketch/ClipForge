"""Stage 4b — per-clip edit with ffmpeg.

Each clip becomes a vertical 1080x1920 MP4:
  - segment extracted, cropped to 9:16 with face-aware framing
    (OpenCV Haar cascade; two far-apart faces -> stacked top/bottom split)
  - karaoke captions burned in from an ASS file
  - optional intro/host image overlaid for the first ~1.5 s
  - name label as a lower-third
  - H.264 + yuv420p (mandatory: phones can't play yuv444p) + 30fps +
    AAC + faststart

UI-agnostic: plain paths/numbers in, file path out. No web imports.

v1 B-roll note: only user-uploaded images are overlaid. Automatic B-roll
fetching is a STUB (see fetch_broll) reserved for v2 — not half-built.
"""

import json
import os
import subprocess

import cv2
import numpy as np

OUT_W, OUT_H = 1080, 1920

_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not os.path.isfile(_FONT):
    _FONT = None  # drawtext then uses ffmpeg's default font


# ---------------------------------------------------------------------------
# B-roll (v2 stub)
# ---------------------------------------------------------------------------

def fetch_broll(query):
    """STUB for v2 — automatically fetch B-roll for a topic.

    Planned sources: Wikimedia Commons API and/or Pexels API (both free,
    with license attribution). Returns None for now. v1 only overlays
    images the user uploads (intro/host image).

    TODO(v2): implement Wikimedia/Pexels fetch + attribution line.
    """
    return None


# ---------------------------------------------------------------------------
# Probing / face detection
# ---------------------------------------------------------------------------

def probe(path):
    """Return {'width', 'height', 'duration'} via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    info = json.loads(out)
    st = (info.get("streams") or [{}])[0]
    dur = st.get("duration") or (info.get("format") or {}).get("duration") or 0
    return {
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
        "duration": float(dur),
    }


_cascade = None
_cascade_failed = False


def _get_cascade():
    """Load the Haar face cascade, or None when unavailable.

    Some minimal OpenCV builds ship without the objdetect module or the
    XML data files — framing then falls back to a centered crop instead
    of crashing.
    """
    global _cascade, _cascade_failed
    if _cascade is None and not _cascade_failed:
        try:
            xml = os.path.join(
                cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
            )
            if os.path.isfile(xml):
                cc = cv2.CascadeClassifier(xml)
                if not cc.empty():
                    _cascade = cc
                    return _cascade
        except (AttributeError, cv2.error):
            pass
        _cascade_failed = True
    return _cascade


def extract_frame_bgr(video_path, t_sec, width, height):
    """Grab one frame as a BGR numpy array (or None on failure)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t_sec:.2f}", "-i", video_path,
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, timeout=60,
    )
    n = width * height * 3
    if len(proc.stdout) < n:
        return None
    return np.frombuffer(proc.stdout[:n], dtype=np.uint8).reshape(
        height, width, 3
    )


def detect_faces(bgr):
    """Return [(x, y, w, h)] face boxes in pixel coords ([] if unavailable)."""
    cc = _get_cascade()
    if cc is None:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    faces = cc.detectMultiScale(
        gray, scaleFactor=1.15, minNeighbors=4, minSize=(60, 60)
    )
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


def plan_framing(video_path, start_sec, end_sec, n_samples=5):
    """Decide the 9:16 crop from detected faces.

    Returns {'mode': 'single', 'x': int} or
            {'mode': 'split', 'x1': int, 'x2': int},
    where x values are crop offsets in the 1920-tall scaled frame.
    Two faces far apart horizontally -> stacked top/bottom split (v1 basic).
    No faces -> centered crop.
    """
    info = probe(video_path)
    sw, sh = info["width"], info["height"]
    if not sw or not sh:
        return {"mode": "single", "x": 0}
    # After scale-to-cover, height is exactly OUT_H; width overflows.
    scaled_w = int(round(sw * OUT_H / sh))
    max_x = max(0, scaled_w - OUT_W)

    def to_crop_x(frac):
        return int(max(0, min(max_x, round(frac * scaled_w - OUT_W / 2))))

    centers = []  # face x-centers as fractions of frame width
    span = max(0.5, end_sec - start_sec)
    for i in range(n_samples):
        t = start_sec + span * (i + 0.5) / n_samples
        frame = extract_frame_bgr(video_path, t, sw, sh)
        if frame is None:
            continue
        for (x, y, w, h) in detect_faces(frame):
            if w * h >= (sw * sh) * 0.005:  # ignore tiny false positives
                centers.append((x + w / 2) / sw)

    if len(centers) >= 2:
        centers.sort()
        gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
        cut = gaps.index(max(gaps)) + 1
        c1, c2 = centers[:cut], centers[cut:]
        med1 = sorted(c1)[len(c1) // 2]
        med2 = sorted(c2)[len(c2) // 2]
        if med2 - med1 > 0.30:  # faces genuinely far apart
            x1, x2 = to_crop_x(med1), to_crop_x(med2)
            if abs(x1 - x2) > OUT_W * 0.35:  # windows truly differ
                return {"mode": "split", "x1": x1, "x2": x2}

    if centers:
        med = sorted(centers)[len(centers) // 2]
        return {"mode": "single", "x": to_crop_x(med)}
    return {"mode": "single", "x": max_x // 2}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _dt_escape(s):
    """Escape text for ffmpeg drawtext."""
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(";", "\\;")
        .replace("=", "\\=")
        .replace("\n", " ")
        .strip()
    )


def _ass_escape(path):
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_clip(video_path, start_sec, duration_sec, out_path,
                ass_path=None, intro_image_path=None, name_label="",
                framing=None):
    """Render one vertical clip. Returns out_path. Raises RuntimeError."""
    if framing is None:
        framing = plan_framing(video_path, start_sec, start_sec + duration_sec)

    inputs = ["-ss", f"{start_sec:.2f}", "-t", f"{duration_sec:.2f}",
              "-i", video_path]
    fc = []
    if framing["mode"] == "split":
        x1, x2 = framing["x1"], framing["x2"]
        fc.append(
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"split=2[sv1][sv2];"
            f"[sv1]crop={OUT_W}:{OUT_H // 2}:{x1}:0,setsar=1[up];"
            f"[sv2]crop={OUT_W}:{OUT_H // 2}:{x2}:{OUT_H // 2},setsar=1[dn];"
            f"[up][dn]vstack,setsar=1,fps=30[vbase]"
        )
    else:
        fc.append(
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}:{framing['x']}:0,setsar=1,fps=30[vbase]"
        )

    last = "[vbase]"
    # User spec (2026-10-19): mirror effect FIRST, then quality enhance.
    # Keep visuals clean — no decorative borders or animated lines.
    fc.append(
        f"{last}hflip,eq=saturation=1.12:contrast=1.06,"
        f"unsharp=5:5:0.5[vq]"
    )
    last = "[vq]"
    if intro_image_path and os.path.isfile(intro_image_path):
        inputs += ["-loop", "1", "-framerate", "30",
                   "-t", f"{duration_sec:.2f}", "-i", intro_image_path]
        fc.append(
            f"[1:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}:0:0[intro]"
        )
        fc.append(f"{last}[intro]overlay=0:0:enable='lt(t,1.5)'[v1]")
        last = "[v1]"

    if name_label:
        font = f"fontfile='{_FONT}':" if _FONT else ""
        fc.append(
            f"{last}drawtext={font}text='{_dt_escape(name_label)}':"
            f"fontsize=46:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-170[v2]"
        )
        last = "[v2]"

    if ass_path and os.path.isfile(ass_path):
        fc.append(
            f"{last}subtitles='{_ass_escape(os.path.abspath(ass_path))}'[v3]"
        )
        last = "[v3]"

    # yuv420p + limited range MUST be the last video filters.
    # (The subtitles filter can flip the range flag to full, which
    #  ffprobe then reports as yuvj420p; phones want plain yuv420p.)
    fc.append(f"{last}scale=out_range=limited,format=yuv420p[vout]")

    cmd = (
        ["ffmpeg", "-v", "error", "-y"] + inputs
        + ["-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-movflags", "+faststart", out_path]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError(f"ffmpeg render failed: {proc.stderr[-3000:]}")
    return out_path
