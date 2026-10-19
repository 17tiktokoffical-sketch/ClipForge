"""Stage 2 — build a timed transcript for the video.

Priority: user-pasted text (used for titles/keywords) > downloaded vtt
captions > faster-whisper (tiny) > openai-whisper. If no whisper backend is
installed, we degrade gracefully: timed captions are simply skipped with a
clear message instead of crashing.

Normalized transcript format (plain dicts, UI-agnostic):
{
  "source":  "user" | "captions" | "whisper" | "none",
  "message": str,          # human-readable note about what happened
  "raw_text": str,         # best text available for keyword/title extraction
  "segments": [            # timed segments (may be empty)
      {"start": float, "end": float, "text": str,
       "words": [{"w": str, "start": float, "end": float}, ...]},
      ...],
}
"""

import os
import re

_TS = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})"
)
_TAG = re.compile(r"<[^>]+>")


def _to_sec(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _even_words(text, start, end):
    """Split a cue's text into words spread evenly across its time span."""
    words = [w for w in text.split() if w]
    if not words:
        return []
    span = max(0.01, end - start)
    return [
        {
            "w": w,
            "start": round(start + span * i / len(words), 3),
            "end": round(start + span * (i + 1) / len(words), 3),
        }
        for i, w in enumerate(words)
    ]


def parse_vtt(path):
    """Parse a .vtt caption file into timed segments (word timings estimated)."""
    segments = []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        m = _TS.search(lines[i])
        if m:
            start = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
            end = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
            i += 1
            text_bits = []
            while i < len(lines) and lines[i].strip():
                text_bits.append(_TAG.sub("", lines[i]).replace("&nbsp;", " "))
                i += 1
            text = " ".join(" ".join(text_bits).split())
            if text and end > start:
                segments.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "text": text,
                        "words": _even_words(text, start, end),
                    }
                )
        else:
            i += 1
    if not segments:
        raise RuntimeError("no usable cues found in caption file")
    return segments


def _whisper_transcribe(video_path, model_size="tiny"):
    """Transcribe with faster-whisper, falling back to openai-whisper.

    Returns (segments|None, message). Never raises for a missing backend —
    that is a supported graceful-degradation path.
    """
    # --- faster-whisper (lightweight, no torch needed) ---
    try:
        from faster_whisper import WhisperModel

        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            raw, _info = model.transcribe(
                video_path, word_timestamps=True, beam_size=5
            )
            segments = []
            for s in raw:
                words = [
                    {
                        "w": w.word.strip(),
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                    }
                    for w in (s.words or [])
                    if w.word.strip()
                ]
                if not words:
                    words = _even_words(s.text, s.start, s.end)
                segments.append(
                    {
                        "start": round(float(s.start), 3),
                        "end": round(float(s.end), 3),
                        "text": s.text.strip(),
                        "words": words,
                    }
                )
            if not segments:
                return None, "faster-whisper returned no segments"
            return segments, f"transcribed with faster-whisper ({model_size})"
        except Exception as e:  # noqa: BLE001 - report, don't crash
            return None, f"faster-whisper failed: {e}"
    except ImportError:
        pass

    # --- openai-whisper (heavier: pulls torch) ---
    try:
        import whisper  # noqa: F401
    except ImportError:
        return (
            None,
            "no whisper backend installed (pip install faster-whisper); "
            "timed captions unavailable",
        )
    try:
        import whisper as _w

        model = _w.load_model(model_size)
        out = model.transcribe(video_path, word_timestamps=True)
        segments = []
        for s in out.get("segments", []):
            words = [
                {
                    "w": w["word"].strip(),
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                }
                for w in s.get("words", [])
                if w["word"].strip()
            ]
            if not words:
                words = _even_words(s["text"], s["start"], s["end"])
            segments.append(
                {
                    "start": round(float(s["start"]), 3),
                    "end": round(float(s["end"]), 3),
                    "text": s["text"].strip(),
                    "words": words,
                }
            )
        if not segments:
            return None, "openai-whisper returned no segments"
        return segments, f"transcribed with openai-whisper ({model_size})"
    except Exception as e:  # noqa: BLE001 - report, don't crash
        return None, f"openai-whisper failed: {e}"


def build_transcript(video_path, vtt_path=None, user_text="", video_duration=0.0):
    """Assemble the transcript following the priority order.

    user_text (no timings) is used for titles/keywords; timed segments for
    captions and manual selection still come from captions/whisper when
    available. Returns the normalized transcript dict (never raises).
    """
    user_text = (user_text or "").strip()
    timed, tsource, tmsg = None, None, ""

    if vtt_path and os.path.isfile(vtt_path):
        try:
            timed = parse_vtt(vtt_path)
            tsource = "captions"
            tmsg = f"using downloaded captions ({len(timed)} cues)"
        except Exception as e:  # noqa: BLE001
            tmsg = f"caption parse failed ({e})"

    if not timed:
        timed, wmsg = _whisper_transcribe(video_path)
        if timed:
            tsource, tmsg = "whisper", wmsg
        else:
            tmsg = (tmsg + "; " if tmsg else "") + wmsg

    raw_text = user_text or " ".join(s["text"] for s in (timed or []))

    if user_text and timed:
        source = "user"
        message = f"using your pasted transcript for titles/keywords; {tmsg}."
    elif user_text:
        source = "user"
        message = (
            "using your pasted transcript for titles/keywords, but no timed "
            "captions are available — clips will render without burned-in "
            "captions and manual timestamp-picking is disabled."
        )
    elif timed:
        source, message = tsource, tmsg + "."
    else:
        source = "none"
        message = "no transcript available — " + tmsg + "."

    return {
        "source": source,
        "message": message,
        "raw_text": raw_text,
        "segments": timed or [],
    }
