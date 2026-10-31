#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["yt-dlp>=2025.1.1", "faster-whisper>=1.0", "av<16", "pillow>=10.1"]
# ///
"""ReelSight: watch a short-form video and print a compact digest for Claude.

Pipeline: fetch -> probe -> scan (visual cuts + audio hits) -> key frames
-> contact sheet -> transcript -> digest. Progress is streamed to a live
browser viewer; the digest goes to stdout so Claude gets it directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from array import array
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("REELSIGHT_HOME", Path.home() / ".cache" / "reelsight"))
LOGIN_HINTS = ("login", "log in", "cookies", "rate-limit", "rate limit", "private", "not available")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=kw.pop("text", True), **kw)


def fmt_t(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def human(n) -> str:
    if n is None:
        return "?"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)


# ---------------------------------------------------------------- state -----
class State:
    """Everything the viewer shows. Written atomically after every change."""

    STAGES = [("fetch", "Fetching video"), ("probe", "Reading the file"),
              ("scan", "Finding cuts & audio hits"), ("frames", "Capturing key moments"),
              ("transcribe", "Listening (transcript)"), ("digest", "Writing digest for Claude")]

    def __init__(self, folder: Path, source: str):
        self.path = folder / "state.json"
        self.d = {"id": folder.name, "source": source, "status": "running", "started": time.time(),
                  "stages": [{"key": k, "label": l, "status": "pending", "detail": ""} for k, l in self.STAGES],
                  "meta": {}, "video": None, "duration": 0, "width": 0, "height": 0,
                  "scenes": [], "hits": [], "loudness": [], "frames": [], "sheet": None,
                  "transcript": [], "transcript_source": "", "digest": "", "error": ""}

    def stage(self, key, status, detail=""):
        for s in self.d["stages"]:
            if s["key"] == key:
                s["status"], s["detail"] = status, detail
        self.save()

    def set(self, **kw):
        self.d.update(kw)
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d))
        tmp.replace(self.path)


# --------------------------------------------------------------- viewer -----
def ensure_viewer() -> str | None:
    info = HOME / "server.json"
    try:
        port = json.loads(info.read_text())["port"]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=0.4)
        return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    subprocess.Popen([sys.executable, str(HERE / "viewer.py"), str(HOME), str(port)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=0.3)
            info.write_text(json.dumps({"port": port}))
            return f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.1)
    return None


def open_window(url: str):
    """Prefer a chromeless app window; fall back to the default browser."""
    if os.environ.get("REELSIGHT_BROWSER") == "default":
        return webbrowser.open(url, new=1)
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium"):
            if any(Path(r, f"{app}.app").exists() for r in ("/Applications", Path.home() / "Applications")):
                subprocess.Popen(["open", "-na", app, "--args", f"--app={url}", "--window-size=1320,880"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        script = f'tell application "Safari"\nmake new document with properties {{URL:"{url}"}}\nactivate\nend tell'
        if sh(["osascript", "-e", script]).returncode == 0:
            return
        subprocess.Popen(["open", url])
        return
    else:
        for b in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            if shutil.which(b):
                subprocess.Popen([b, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(url, new=1)


# ---------------------------------------------------------------- fetch -----
def job_id(src: str) -> str:
    p = Path(src).expanduser()
    if p.exists():
        st = p.stat()
        return "local-" + hashlib.sha1(f"{p.resolve()}{st.st_size}{st.st_mtime}".encode()).hexdigest()[:10]
    u = urlparse(src)
    host = (u.hostname or "web").replace("www.", "").split(".")[0]
    key = u.netloc + u.path.rstrip("/")
    if "v" in parse_qs(u.query):  # youtube.com/watch?v=
        key += "?v=" + parse_qs(u.query)["v"][0]
    return f"{host}-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def ytdlp_cmd():
    """Prefer the uv-managed module (fresh); extractors for IG/TikTok break often."""
    import importlib.util
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    if shutil.which("yt-dlp"):
        return [shutil.which("yt-dlp")]
    raise RuntimeError("yt-dlp not found — run with uv, or pip install yt-dlp")


def fetch(src: str, d: Path, st: State, cookies: str | None) -> Path:
    p = Path(src).expanduser()
    if p.exists():
        dst = d / ("video" + p.suffix.lower())
        if not dst.exists():
            dst.symlink_to(p.resolve())
        st.set(meta={"platform": "local", "title": p.name})
        return dst
    base = ytdlp_cmd() + [
        "--no-playlist", "--newline", "--no-warnings", "-o", str(d / "video.%(ext)s"),
        "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b",
        "--merge-output-format", "mp4", "--write-info-json",
        "--progress-template", "download:RS %(progress._percent_str)s"]
    if cookies:
        base += ["--cookies-from-browser", cookies]
    if not shutil.which("deno") and shutil.which("node"):  # YouTube needs a JS runtime
        base += ["--js-runtimes", "node"]
    err = ""
    for extra in (["--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt"], []):
        proc = subprocess.Popen(base + extra + [src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if line.startswith("RS "):
                st.stage("fetch", "active", line[3:].strip())
        err = proc.stderr.read()
        proc.wait()
        vids = [f for f in d.glob("video.*") if f.suffix in (".mp4", ".webm", ".mkv", ".mov", ".m4v")]
        if proc.returncode == 0 and vids:
            return vids[0]
    msg = (err.strip().splitlines() or ["download failed"])[-1]
    if any(h in err.lower() for h in LOGIN_HINTS) and not cookies:
        raise LoginWall(msg)
    raise RuntimeError(msg)


class LoginWall(RuntimeError):
    pass


def read_meta(d: Path) -> dict:
    f = d / "video.info.json"
    if not f.exists():
        return {}
    i = json.loads(f.read_text())
    handle = next((h for h in (i.get("channel"), i.get("uploader_id"), i.get("uploader"))
                   if h and not str(h).isdigit() and " " not in str(h)), None)
    title = i.get("title") or ""
    return {"platform": (i.get("extractor_key") or "").lower(), "title": None if title.startswith("Video by") else title,
            "uploader": i.get("uploader") or i.get("channel"), "handle": handle,
            "url": i.get("webpage_url"), "date": i.get("upload_date"), "views": i.get("view_count"),
            "likes": i.get("like_count"), "comments": i.get("comment_count"),
            "caption": i.get("description") or "", "music": i.get("track") and f"{i.get('track')} — {i.get('artist')}"}


# ----------------------------------------------------------------- scan -----
def probe(v: Path) -> dict:
    j = json.loads(sh(["ffprobe", "-v", "error", "-show_entries",
                       "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(v)]).stdout)
    vs = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (vs.get("r_frame_rate") or "0/1").partition("/")
    return {"duration": float(j.get("format", {}).get("duration") or 0), "width": vs.get("width", 0),
            "height": vs.get("height", 0), "fps": round(float(num) / float(den or 1)) if float(den or 1) else 0,
            "has_audio": any(s.get("codec_type") == "audio" for s in j.get("streams", []))}


def scan_cuts(v: Path, thr: float) -> list[tuple[float, float]]:
    out = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", str(v), "-an", "-vf",
              f"scale=160:-2,select='gt(scene,{thr})',metadata=print:file=-", "-f", "null", "-"]).stdout
    cuts, t = [], None
    for line in out.splitlines():
        if m := re.search(r"pts_time:([\d.]+)", line):
            t = float(m.group(1))
        elif (m := re.search(r"scene_score=([\d.]+)", line)) and t is not None:
            cuts.append((round(t, 2), float(m.group(1))))
    return cuts


def scan_audio(v: Path, win_s=0.25):
    """Loudness curve (dBFS per window) and sudden 'hits' (beat drops, punchlines, bangs)."""
    raw = sh(["ffmpeg", "-v", "error", "-i", str(v), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
             text=False).stdout
    a = array("h")
    a.frombytes(raw[: len(raw) - len(raw) % 2])
    w = int(8000 * win_s)
    db = []
    for i in range(0, len(a) - w + 1, w):
        seg = a[i:i + w]
        rms = math.sqrt(sum(x * x for x in seg) / w)
        db.append(20 * math.log10(rms / 32768 + 1e-9))
    hits, back = [], int(2 / win_s)
    for i in range(back, len(db) - 1):
        base = sum(db[i - back:i]) / back
        jump = db[i] - base
        if jump > 6 and db[i] >= db[i + 1] and db[i] > -35:
            hits.append((round(i * win_s, 2), jump))
    hits.sort(key=lambda h: -h[1])
    picked = []
    for t, j in hits:
        if all(abs(t - p) >= 2 for p, _ in picked):
            picked.append((t, j))
    return db, sorted(picked[:5])


# --------------------------------------------------------------- frames -----
def choose_frames(dur, cuts, hits, n):
    end = max(0.0, dur - 0.35)
    picked = [(min(0.5, dur / 2), "hook")]
    if end - picked[0][0] > 1:
        picked.append((end, "end"))
    gap = dur / (n * 2)
    # land just after a cut, once animated text has settled, but before the next cut
    nxt = [t for t, _ in cuts[1:]] + [dur]
    cands = [(min(t + 0.6, (t + n2) / 2), s, "cut") for (t, s), n2 in zip(cuts, nxt)] + [(t, j / 20, "audio hit") for t, j in hits]
    for t, _, why in sorted(cands, key=lambda c: -c[1]):
        if len(picked) >= n:
            break
        if 0 < t < dur and all(abs(t - p) >= gap for p, _ in picked):
            picked.append((round(t, 2), why))
    while len(picked) < n and dur > 1:
        ts = sorted(p for p, _ in picked)
        a, b = max(zip(ts, ts[1:]), key=lambda ab: ab[1] - ab[0])
        if b - a < 0.6:
            break
        picked.append((round((a + b) / 2, 2), "coverage"))
    return sorted(picked)


def grab(v: Path, t: float, out: Path, long_edge: int, portrait: bool):
    scale = f"scale=-2:{long_edge}" if portrait else f"scale={long_edge}:-2"
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(v), "-frames:v", "1", "-vf", scale,
        "-q:v", "4", str(out)])


def dedupe(frames, d: Path):
    """Drop frames that look like the one before (static shots, end cards) and renumber."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return frames
    keep, prev = [], None
    for f in frames:
        im = Image.open(d / f["file"]).convert("L").resize((24, 24))
        if prev is not None and ImageStat.Stat(ImageChops.difference(im, prev)).mean[0] < 5:
            (d / f["file"]).unlink()
            continue
        keep.append(f)
        prev = im
    for i, f in enumerate(keep, 1):
        name = f"frames/f{i:02d}.jpg"
        if f["file"] != name:
            (d / f["file"]).rename(d / name)
            f["file"] = name
    return keep


def build_sheet(frames, d: Path, portrait: bool) -> tuple[Path, int]:
    n = len(frames)
    cols = min(n, 4 if portrait else 3)
    rows = math.ceil(n / cols)
    tw, th = (270, 480) if portrait else (400, 225)
    k = min(1, 1560 / (cols * tw), 1560 / (rows * th))
    tw, th = int(tw * k), int(th * k)
    out = d / "sheet.jpg"
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError:  # unlabeled fallback; the digest still maps tiles to times
        sh(["ffmpeg", "-v", "error", "-y", "-i", str(d / "frames" / "f%02d.jpg"), "-vf",
            f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:-1:-1,tile={cols}x{rows}",
            "-frames:v", "1", str(out)])
        return out, cols * tw * rows * th // 750
    sheet = Image.new("RGB", (cols * tw, rows * th), (12, 12, 14))
    try:
        font = ImageFont.load_default(size=max(14, th // 18))
    except TypeError:
        font = ImageFont.load_default()
    for i, f in enumerate(frames):
        im = ImageOps.pad(Image.open(d / f["file"]).convert("RGB"), (tw, th), color=(12, 12, 14))
        dr = ImageDraw.Draw(im)
        label = f"#{i + 1} {f['t']:.1f}s"
        x0, y0, x1, y1 = dr.textbbox((8, 6), label, font=font)
        dr.rectangle((x0 - 5, y0 - 4, x1 + 5, y1 + 4), fill=(0, 0, 0))
        dr.text((8, 6), label, fill=(255, 214, 10), font=font)
        sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
    sheet.save(out, quality=72)
    return out, sheet.width * sheet.height // 750


# ----------------------------------------------------------- transcript -----
def parse_vtt(f: Path):
    def sec(x):
        h, m, rest = ([0] * 2 + x.split(":"))[-3:]
        return round(int(h) * 3600 + int(m) * 60 + float(rest), 2)
    segs, recent = [], []
    for block in re.split(r"\n\s*\n", f.read_text(errors="ignore")):
        lines = block.strip().splitlines()
        i = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if i is None:
            continue
        s, e = (sec(x) for x in re.findall(r"[\d:]+\.\d+", lines[i])[:2])
        for ln in lines[i + 1:]:
            ln = re.sub(r"<[^>]+>", "", ln).strip()
            if ln and ln not in recent:  # auto-captions repeat rolling lines
                recent = (recent + [ln])[-4:]
                segs.append({"s": s, "e": e, "text": ln})
    return segs


def transcribe(v: Path, d: Path, st: State, model: str, lang: str | None, has_audio: bool):
    subs = sorted(d.glob("video*.vtt"), key=lambda f: ("auto" in f.name or "orig" in f.name, f.name))
    if subs and (segs := parse_vtt(subs[0])):
        st.set(transcript=segs, transcript_source="platform captions")
        return segs, "platform captions"
    if not has_audio:
        return [], "no audio track"
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return [], "unavailable (install faster-whisper or run with uv)"
    st.stage("transcribe", "active", f"loading whisper {model}")
    wm = WhisperModel(model, device="auto", compute_type="int8")
    gen, info = wm.transcribe(str(v), language=lang, vad_filter=True, beam_size=1)
    segs = []
    for s in gen:
        segs.append({"s": round(s.start, 2), "e": round(s.end, 2), "text": s.text.strip()})
        st.set(transcript=segs)
        st.stage("transcribe", "active", f"{fmt_t(s.end)} heard")
    src = f"whisper {model}, {info.language}"
    st.set(transcript_source=src)
    return segs, src


# --------------------------------------------------------------- digest -----
def make_digest(st: State, d: Path, segs, tsrc, pr, cuts, hits, sheet, sheet_tok, viewer, max_chars):
    m, fr = st.d["meta"], st.d["frames"]
    who = f"@{m['handle']}" if m.get("handle") else (m.get("uploader") or "")
    L = [f"# REELSIGHT · {m.get('platform') or 'video'} · {who} · {fmt_t(pr['duration'])} · "
         f"{pr['width']}x{pr['height']} · {pr['fps']}fps"]
    if m.get("title") and m["title"] != m.get("caption", "")[:len(m["title"])]:
        L.append(f"Title: {m['title']}")
    if m.get("url"):
        L.append(f"URL: {m['url']}")
    stats = [f"{human(m.get(k))} {k}" for k in ("views", "likes", "comments") if m.get(k) is not None]
    if m.get("date"):
        stats.insert(0, f"posted {m['date'][:4]}-{m['date'][4:6]}-{m['date'][6:]}")
    if stats:
        L.append(" · ".join(stats))
    if cap := re.sub(r"\s+", " ", m.get("caption") or "").strip():
        L.append("Caption: " + (cap[:500] + "…" if len(cap) > 500 else cap))
    if m.get("music"):
        L.append(f"Music: {m['music']}")
    words = sum(len(s["text"].split()) for s in segs)
    speech = sum(s["e"] - s["s"] for s in segs)
    audio = (f"speech ({tsrc}, {words} words, {speech / max(pr['duration'], 1):.0%} of runtime)" if segs
             else f"no speech detected ({tsrc})" if pr["has_audio"] else "silent / no audio track")
    L.append(f"Audio: {audio}")
    shots = len(cuts) + 1
    L.append(f"Editing: {len(cuts)} cuts, avg shot {pr['duration'] / shots:.1f}s"
             + (f" · audio hits at {', '.join(f'{t:.1f}s' for t, _ in hits)}" if hits else ""))
    L.append("\n## Timeline  (#frame time [why] — speech until next frame)")
    groups = [[] for _ in fr]
    for s in segs:
        idx = max([i for i, f in enumerate(fr) if f["t"] <= s["s"] + 0.3] or [0])
        groups[idx].append(s["text"])
    body, used, truncated = [], 0, False
    for i, (f, g) in enumerate(zip(fr, groups)):
        text = " ".join(g)
        if used + len(text) > max_chars:
            text, truncated = text[: max(0, max_chars - used)] + "…", True
        used += len(text)
        body.append(f"#{i + 1} {f['t']:.1f}s [{f['why']}]" + (f' — "{text}"' if text else ""))
    L += body
    full = "\n".join(f"[{fmt_t(s['s'])}] {s['text']}" for s in segs)
    (d / "transcript.txt").write_text(full)
    if truncated:
        L.append(f"(transcript truncated — full text: {d / 'transcript.txt'})")
    L.append(f"\n## Visuals\nContact sheet: {len(fr)} labeled frames (~{sheet_tok} tokens). "
             f"Full frames: {d / 'frames'}/fNN.jpg · exact moment: watch.py <src> --at SECONDS")
    if viewer:
        L.append(f"Live viewer: {viewer}")
    L.append(f"SHEET: {sheet}")
    return "\n".join(L)


# ----------------------------------------------------------------- main -----
def main():
    ap = argparse.ArgumentParser(description="Watch a short-form video and print a digest for Claude.")
    ap.add_argument("src", help="URL (Instagram, TikTok, YouTube Shorts, X, …) or local video path")
    ap.add_argument("--frames", type=int, default=0, help="key-frame budget (default: auto 6–16)")
    ap.add_argument("--model", default=os.environ.get("REELSIGHT_MODEL", "base"), help="whisper model")
    ap.add_argument("--lang", default=None, help="force transcription language, e.g. en")
    ap.add_argument("--cookies", default=None, help="browser to borrow login cookies from (chrome, safari, …)")
    ap.add_argument("--threshold", type=float, default=0.27, help="scene-cut sensitivity (lower = more cuts)")
    ap.add_argument("--max-chars", type=int, default=6000, help="transcript chars in digest")
    ap.add_argument("--max-minutes", type=float, default=15, help="refuse videos longer than this")
    ap.add_argument("--at", default=None, help="comma-separated seconds: export full-res frames from cache")
    ap.add_argument("--fresh", action="store_true", help="ignore cached results")
    ap.add_argument("--no-view", action="store_true", help="don't open the live browser viewer")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"reelsight: {tool} not found — install it (brew install ffmpeg / apt install ffmpeg)")
    d = HOME / job_id(a.src)
    if a.fresh and d.exists():
        shutil.rmtree(d)
    (d / "frames").mkdir(parents=True, exist_ok=True)

    if a.at:
        v = next((f for f in d.glob("video.*") if f.suffix not in (".json", ".vtt")), None)
        if not v:
            sys.exit("reelsight: run without --at first so the video is cached")
        pr = probe(v)
        for t in (float(x) for x in a.at.split(",")):
            out = d / "frames" / f"at_{t:.2f}.jpg"
            grab(v, t, out, 1080, pr["height"] >= pr["width"])
            print(out)
        return

    view = None if (a.no_view or os.environ.get("REELSIGHT_NO_VIEW")) else ensure_viewer()
    url = f"{view}/v/{d.name}" if view else None
    done = d / "digest.md"
    if done.exists() and not a.fresh:
        if url:
            open_window(url)
        text = re.sub(r"^Live viewer: .*\n", "", done.read_text(), flags=re.M)
        print(text.replace("SHEET: ", f"Live viewer: {url}\nSHEET: ", 1) if url else text)
        return

    st = State(d, a.src)
    st.save()
    if url:
        open_window(url)
    try:
        st.stage("fetch", "active")
        v = fetch(a.src, d, st, a.cookies)
        meta = read_meta(d) or st.d["meta"]
        st.set(meta=meta, video=v.name)
        st.stage("fetch", "done", meta.get("platform", ""))

        st.stage("probe", "active")
        pr = probe(v)
        if not pr["duration"]:
            raise RuntimeError("could not read video duration")
        if pr["duration"] > a.max_minutes * 60:
            raise RuntimeError(f"video is {fmt_t(pr['duration'])} long; ReelSight is tuned for short-form "
                               f"(limit {a.max_minutes} min, raise with --max-minutes)")
        st.set(duration=pr["duration"], width=pr["width"], height=pr["height"])
        st.stage("probe", "done", f"{fmt_t(pr['duration'])} · {pr['width']}x{pr['height']}")
        portrait = pr["height"] >= pr["width"]

        st.stage("scan", "active")
        cuts = scan_cuts(v, a.threshold)
        st.set(scenes=[t for t, _ in cuts])
        db, hits = scan_audio(v) if pr["has_audio"] else ([], [])
        step = max(1, len(db) // 240)
        st.set(hits=[t for t, _ in hits],
               loudness=[round(max(0, min(1, (x + 60) / 60)), 3) for x in db[::step]])
        st.stage("scan", "done", f"{len(cuts)} cuts · {len(hits)} audio hits")

        n = a.frames or max(6, min(16, round(pr["duration"] / 4) + 4))
        picks = choose_frames(pr["duration"], cuts, hits, n)
        st.stage("frames", "active", f"0/{len(picks)}")
        frames = []
        for i, (t, why) in enumerate(picks, 1):
            name = f"frames/f{i:02d}.jpg"
            grab(v, t, d / name, 720, portrait)
            frames.append({"t": t, "why": why, "file": name})
            st.set(frames=frames)
            st.stage("frames", "active", f"{i}/{len(picks)}")
        frames = dedupe(frames, d)
        st.set(frames=frames)
        sheet, sheet_tok = build_sheet(frames, d, portrait)
        st.set(sheet="sheet.jpg")
        st.stage("frames", "done", f"{len(frames)} frames")

        st.stage("transcribe", "active")
        segs, tsrc = transcribe(v, d, st, a.model, a.lang, pr["has_audio"])
        st.stage("transcribe", "done", f"{len(segs)} lines · {tsrc}")

        st.stage("digest", "active")
        digest = make_digest(st, d, segs, tsrc, pr, cuts, hits, sheet, sheet_tok, url, a.max_chars)
        done.write_text(digest)
        st.set(digest=digest, status="done", finished=time.time())
        st.stage("digest", "done", f"~{len(digest) // 4 + sheet_tok} tokens handed to Claude")
        print(digest)
    except LoginWall as e:
        st.set(status="error", error=f"Login wall: {e}")
        print(f"reelsight: LOGIN_REQUIRED — {e}\nRetry with --cookies chrome (or safari/firefox/edge) "
              "after asking the user; yt-dlp reads their local browser session.", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        st.set(status="error", error=str(e))
        sys.exit(f"reelsight: {e}")


if __name__ == "__main__":
    main()
