"""ClipForge web UI (Streamlit) — for Streamlit Community Cloud.

THIN LAYER ONLY — all logic lives in the pipeline modules
(download/transcribe/pick/captions/edit/package).
This file only:
  - collects inputs from the UI,
  - calls pipeline functions with plain arguments,
  - displays plain results.

Deploy: share.streamlit.io → New app → repo/branch/streamlit_app.py.
Login comes from Streamlit secrets (CF_USER / CF_PASS) or env vars.
"""

import os
import shutil
import traceback
import uuid

import streamlit as st

import captions as capmod
import download as dl
import edit as ed
import package as pkg
import pick as sel
import transcribe as tr

WORKDIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(WORKDIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Login gate
# ---------------------------------------------------------------------------

def _expected_login():
    try:
        secrets = st.secrets
        user = secrets.get("CF_USER") or os.environ.get("CF_USER",
                                                         "clipforge")
        pwd = secrets.get("CF_PASS") or os.environ.get("CF_PASS",
                                                       "forge2026!")
    except Exception:  # no secrets.toml (local run)
        user = os.environ.get("CF_USER", "clipforge")
        pwd = os.environ.get("CF_PASS", "forge2026!")
    return user, pwd


def login_gate():
    if st.session_state.get("authed"):
        return True
    user_exp, pwd_exp = _expected_login()
    st.title("✂️ ClipForge — Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        if u == user_exp and p == pwd_exp:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Ghalat username ya password.")
    return False


# ---------------------------------------------------------------------------
# Helpers (UI-side glue; no business logic)
# ---------------------------------------------------------------------------

def _words_in_window(segments, start, end):
    words = []
    for s in segments:
        if s["end"] <= start or s["start"] >= end:
            continue
        for w in s.get("words", []):
            if not w.get("w", "").strip():
                continue
            ws = max(w["start"], start) - start
            we = min(w["end"], end) - start
            if we > ws:
                words.append({"w": w["w"].strip(),
                              "start": round(ws, 3),
                              "end": round(we, 3)})
    words.sort(key=lambda x: x["start"])
    return words


def _window_text(segments, start, end):
    bits = [s["text"] for s in segments
            if s["end"] > start and s["start"] < end]
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Step 1 — fetch video + transcript
# ---------------------------------------------------------------------------

def do_prepare(url, src_upload, dur_min, n_clips, user_text, intro_upload,
               name_label, mode, bar, status):
    url = (url or "").strip()
    job_id = uuid.uuid4().hex[:8]
    jobdir = os.path.join(WORKDIR, f"job_{job_id}")
    os.makedirs(jobdir, exist_ok=True)

    intro_path = None
    if intro_upload is not None:
        intro_path = os.path.join(jobdir, "intro" + os.path.splitext(
            intro_upload.name)[1])
        with open(intro_path, "wb") as f:
            f.write(intro_upload.getbuffer())

    if src_upload is not None:
        status.text("Uploaded video istemal ho rahi hai…")
        bar.progress(15)
        video = os.path.join(jobdir, "source.mp4")
        with open(video, "wb") as f:
            f.write(src_upload.getbuffer())
        info = ed.probe(video)
        if not info["duration"]:
            raise RuntimeError("Could not read that video file. "
                               "Try another one.")
        dlres = {"video": video, "sub": None,
                 "title": os.path.splitext(src_upload.name)[0],
                 "duration": info["duration"]}
    elif url:
        status.text("Video + captions download ho rahe hain…")
        bar.progress(15)
        dlres = dl.download_video(url, os.path.join(jobdir, "src"))
    else:
        raise RuntimeError("Pehle YouTube URL dein ya video file upload "
                           "karein.")

    status.text("Transcript ban raha hai…")
    bar.progress(55)
    transcript = tr.build_transcript(dlres["video"], dlres.get("sub"),
                                     user_text, dlres["duration"])

    state = {
        "jobdir": jobdir,
        "video": dlres["video"],
        "title": dlres["title"],
        "duration": dlres["duration"],
        "transcript": transcript,
        "dur_min": float(dur_min),
        "n_clips": int(n_clips),
        "intro_img": intro_path,
        "name_label": (name_label or "").strip() or dlres["title"],
        "mode": mode,
        "ui_items": [],
    }
    if mode.startswith("Manual") and transcript["segments"]:
        state["ui_items"] = sel.segments_for_ui(transcript)

    dur_s = dlres["duration"]
    mins = f"{int(dur_s // 60)}:{int(dur_s % 60):02d}"
    bar.progress(100)
    status.text("Ready ✅")
    info_md = (f"✅ **Ready:** {dlres['title']} ({mins})\n\n"
               f"📝 Transcript: {transcript['message']}")
    return state, info_md


# ---------------------------------------------------------------------------
# Step 2 — select moments, render clips, package titles
# ---------------------------------------------------------------------------

def do_generate(state, picked_labels, bar, status):
    clip_sec = float(state["dur_min"]) * 60.0
    transcript = state["transcript"]
    segments = transcript.get("segments", [])

    status.text("Moments select ho rahe hain…")
    bar.progress(5)
    if state["mode"].startswith("Manual"):
        if not segments:
            raise RuntimeError("Manual mode ke liye timed captions "
                               "chahiye, jo maujood nahi.")
        idx_by_label = {it["label"]: it["index"]
                        for it in state["ui_items"]}
        indices = [idx_by_label[l] for l in (picked_labels or [])
                   if l in idx_by_label]
        if not indices:
            raise RuntimeError("Kam az kam ek moment select karein.")
        windows = sel.manual_windows(transcript, indices, clip_sec,
                                     state["duration"])
    else:
        windows = sel.auto_select(state["video"], clip_sec,
                                  state["n_clips"])
    if not windows:
        raise RuntimeError("Koi usable moment nahi mila. Clip duration "
                           "kam karke dekhein.")

    clips_dir = os.path.join(state["jobdir"], "clips")
    os.makedirs(clips_dir, exist_ok=True)
    mp4s, packages = [], []
    total = len(windows)
    for i, w in enumerate(windows):
        frac = (i + 0.5) / total * 0.9 + 0.05
        bar.progress(int(frac * 100))
        status.text(f"Clip {i + 1}/{total} render ho raha hai…")
        dur = w["end"] - w["start"]
        words = _words_in_window(segments, w["start"], w["end"])
        ass_path = os.path.join(clips_dir, f"clip_{i:02d}.ass")
        if words:
            capmod.build_ass(words, ass_path)
        else:
            ass_path = None
        out_path = os.path.join(clips_dir, f"clip_{i + 1:02d}.mp4")
        ed.render_clip(state["video"], w["start"], dur, out_path,
                       ass_path=ass_path,
                       intro_image_path=state["intro_img"],
                       name_label=state["name_label"])
        mp4s.append(out_path)
        clip_text = _window_text(segments, w["start"], w["end"]) \
            or transcript.get("raw_text", "")
        packages.append(pkg.clip_package(i, state["name_label"], clip_text))
        bar.progress(int(((i + 1) / total * 0.9 + 0.05) * 100))

    status.text("Titles likhe ja rahe hain…")
    bar.progress(97)
    titles_path = pkg.write_titles_txt(packages,
                                       os.path.join(clips_dir,
                                                    "titles.txt"))
    with open(titles_path, encoding="utf-8") as f:
        titles_text = f.read()
    bar.progress(100)
    status.text("Done ✅")
    return mp4s, titles_path, titles_text


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.set_page_config(page_title="ClipForge", page_icon="✂️",
                   layout="centered")

if not login_gate():
    st.stop()

st.title("✂️ ClipForge")
st.caption("Lambi YouTube video → vertical 9:16 shorts. "
           "Auto moment-picking, face-aware framing, captions, "
           "titles + hashtags. 100% free & open-source.")

st.subheader("Step 1 — Video layein")
url = st.text_input("YouTube URL",
                    placeholder="https://www.youtube.com/watch?v=…")
src_upload = st.file_uploader(
    "…ya video file upload karein (recommended — YouTube kabhi "
    "kabhi rate-limit hota hai)",
    type=["mp4", "mov", "mkv", "webm"])
col1, col2 = st.columns(2)
with col1:
    dur_min = st.select_slider("Clip duration (minutes)",
                               options=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
                               value=1.0)
with col2:
    n_clips = st.slider("Number of clips", 1, 10, 3)
name_label = st.text_input("Name label (lower-third)",
                           placeholder="Khaali = video title")
mode = st.radio("Mode", ["Auto (best moments)",
                         "Manual (I pick moments)"])
user_text = st.text_area(
    "Transcript (optional — titles/keywords guide ke liye paste karein)",
    height=120)
intro_upload = st.file_uploader(
    "Intro / host image (optional — har clip ke pehle 1.5 s)",
    type=["jpg", "jpeg", "png"])

if st.button("1 — Fetch video & transcript", type="primary"):
    bar = st.progress(0)
    status = st.empty()
    try:
        state, info_md = do_prepare(
            url, src_upload, dur_min, n_clips, user_text, intro_upload,
            name_label, mode, bar, status)
        st.session_state["job"] = state
        st.session_state["picks_labels"] = []
        st.success(info_md)
    except Exception:  # noqa: BLE001 - friendly error in UI
        st.error(f"❌ Prepare failed:\n\n```\n"
                 f"{traceback.format_exc(limit=3)}\n```")

job = st.session_state.get("job")
if job:
    st.divider()
    st.subheader("Step 2 — Clips banayein")
    picked_labels = []
    if job["mode"].startswith("Manual") and job["ui_items"]:
        picked_labels = st.multiselect(
            "Moments select karein",
            options=[it["label"] for it in job["ui_items"]],
            default=st.session_state.get("picks_labels", []))
        st.session_state["picks_labels"] = picked_labels

    if st.button("2 — Generate clips", type="primary"):
        bar = st.progress(0)
        status = st.empty()
        try:
            mp4s, titles_path, titles_text = do_generate(
                job, picked_labels, bar, status)
            st.session_state["mp4s"] = mp4s
            st.session_state["titles_path"] = titles_path
            st.session_state["titles_text"] = titles_text
            st.success(f"✅ **Done:** {len(mp4s)} vertical clip(s) ready "
                       f"(1080×1920, H.264 yuv420p).")
        except Exception:  # noqa: BLE001 - friendly error in UI
            st.error(f"❌ Generate failed:\n\n```\n"
                     f"{traceback.format_exc(limit=3)}\n```")

    mp4s = st.session_state.get("mp4s", [])
    for i, mp4 in enumerate(mp4s):
        st.video(mp4)
        with open(mp4, "rb") as f:
            st.download_button(f"⬇️ Download clip {i + 1}",
                               data=f.read(),
                               file_name=os.path.basename(mp4),
                               mime="video/mp4",
                               key=f"dl_{i}")
    titles_text = st.session_state.get("titles_text", "")
    if titles_text:
        st.text_area("Titles + hashtags", value=titles_text, height=200)
        with open(st.session_state["titles_path"], "rb") as f:
            st.download_button("⬇️ Download titles.txt",
                               data=f.read(),
                               file_name="titles.txt",
                               key="dl_titles")
