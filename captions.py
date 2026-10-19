"""Stage 4a — clean styled ASS subtitles (word timings, no karaoke sweep).

Takes word-level timings (seconds, relative to the clip start) and writes a
1080x1920 ASS file in a simple bold style: big readable words, steady
highlight color. Kept deliberately simple per user spec (2026-10-19) —
no animated/flowing karaoke lines; TikTok prefers clean captions.
UI-agnostic: plain arguments in, file path out.
"""

import os

# ASS style fields:
# Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,
# BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing,
# Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV,
# Encoding. Colours are &HAABBGGRR. Alignment 2 = bottom-center.
_STYLE = (
    "Style: ClipCap,DejaVu Sans,88,&H00FFFFFF,&H0000E5FF,&H00000000,&H99000000,"
    "-1,0,0,0,100,100,0,0,1,4,1,2,60,60,430,1"
)


def _ass_time(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"  # H:MM:SS.cc


def _esc(text):
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def chunk_words(words, max_words=6, max_gap=0.5, max_dur=4.0):
    """Group word dicts into caption events.

    words: [{"w", "start", "end"}] with times relative to clip start.
    A new event starts after a pause, too many words, or too long a span.
    """
    chunks, cur = [], []
    for w in words:
        if cur and (
            len(cur) >= max_words
            or w["start"] - cur[-1]["end"] > max_gap
            or w["end"] - cur[0]["start"] > max_dur
        ):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words, out_path, max_words=6):
    """Write the ASS file; returns out_path. words may be empty (writes
    a valid file with no events, so the render step stays simple)."""
    words = [w for w in words if w.get("w", "").strip()]
    words.sort(key=lambda w: w["start"])

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _STYLE,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    events = []
    for ch in chunk_words(words, max_words):
        # Plain clean text per chunk — no karaoke sweep tags (user spec:
        # keep captions simple, no flowing animated lines).
        text = " ".join(_esc(w["w"]) for w in ch)
        start = _ass_time(ch[0]["start"])
        end = _ass_time(min(ch[-1]["end"] + 0.25, ch[0]["start"] + 6.0))
        events.append(
            f"Dialogue: 0,{start},{end},ClipCap,,0,0,0,,{text}"
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + events) + "\n")
    return out_path
