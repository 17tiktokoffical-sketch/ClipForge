"""Stage 5 — titles & hashtags from each clip's transcript.

Simple TF-based keyword extraction (no external APIs, no models).
UI-agnostic: plain strings in, plain dicts out.
"""

import os
import re
from collections import Counter

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because
    been before being below between both but by can did do does doing down
    during each few for from further had has have having he her here hers
    herself him himself his how i if in into is it its itself just like me
    more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their
    theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why
    will with you your yours yourself yourselves s t d ll m re ve don doesn
    isn aren wasn weren couldn wouldn shouldn hasn haven gonna wanna got get
    just like really also one two first new make made many much even still
    """.split()
)


def top_keywords(text, n=8):
    """Most frequent content words (3+ letters, no stopwords)."""
    tokens = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    freq = Counter(t for t in tokens if t not in STOPWORDS)
    return [w for w, _ in freq.most_common(n)]


def _clean_tag(kw):
    return "#" + re.sub(r"[^a-z0-9]", "", kw.lower())


def clip_package(index, label, transcript_text):
    """Return {'title': str, 'hashtags': [5 tags]} for one clip."""
    kws = top_keywords(transcript_text, 8)
    title = (
        f"{label} \u2014 {', '.join(kws[:3])}" if kws else f"{label} \u2014 part {index + 1}"
    )
    tags = []
    for k in kws:
        t = _clean_tag(k)
        if len(t) > 1 and t not in tags:
            tags.append(t)
        if len(tags) >= 5:
            break
    for fb in ("#shorts", "#viral", "#clips", "#podcast"):
        if len(tags) >= 5:
            break
        if fb not in tags:
            tags.append(fb)
    return {"title": title, "hashtags": tags[:5]}


def write_titles_txt(packages, out_path):
    """Write titles + hashtags for all clips; returns out_path."""
    lines = []
    for i, p in enumerate(packages):
        lines.append(f"Clip {i + 1}: {p['title']}")
        lines.append(" ".join(p["hashtags"]))
        lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    return out_path
