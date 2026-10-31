---
name: reelsight
description: ReelSight lets Claude watch short-form video — Instagram Reels, TikTok, YouTube Shorts, X/Twitter, Facebook, Reddit, Snapchat, Twitch clips, or a local video file. Downloads it, transcribes the speech, captures frames at the important moments (cuts, audio hits, hook, ending), shows a live viewer window, and returns a compact digest plus one labeled contact-sheet image. Use whenever the user pastes a reel/short/TikTok/video link or file, even with no instructions, or asks to watch, summarise, explain, transcribe, fact-check, critique, or turn a video into notes, scripts, hooks or recipes.
---

# ReelSight

Watch a video by running one command, then understand it from the text it prints plus **one** image.

## 1. Run

```bash
bash "<skill-dir>/scripts/reelsight" "<url-or-path>"
```

- Use Bash timeout `600000`. The command sets itself up: on the very first run it installs everything it needs (about 1–2 min, so tell the user "setting up, first run only"). After that a reel takes about 30 seconds, and repeat links are instant from the cache.
- A live viewer window opens for the user automatically. Mention it; don't drive it yourself.
- For several links, run the command once per link.

## 2. Understand

The command prints a digest: platform, creator, stats, caption, audio summary, editing pace, and a **timeline** where every key frame `#n` carries the speech heard until the next frame. The last line is `SHEET: <path>`.

1. Read the SHEET image **once**. Its tiles are labeled `#n time`, matching the timeline. Take on-screen text, people, products and setting from it.
2. Read a single `frames/fNN.jpg` only when a detail is too small to read on the sheet. Use `--at 12.4,15` to grab an exact moment at full resolution.
3. Don't read `state.json`, the video or `transcript.txt` unless the digest says the transcript was truncated.

## 3. Answer

If the user asked something specific, answer that. If they only pasted a link, give: **the hook** (first 2 seconds), **what happens** (beat by beat, with timestamps), **the message or purpose**, **on-screen text**, **audio** (speech, music, voiceover), and **craft notes** (pacing, cuts, CTA, why it works). Keep it tight, then offer a useful next step (for example hooks in this style, a script, notes, a fact-check).

## Flags

| Flag | Use |
|---|---|
| `--cookies chrome` | Exit code 3 / `LOGIN_REQUIRED` (private or login-walled post). **Ask the user first.** Other values: `safari`, `firefox`, `edge`, `brave` |
| `--model small` | Better transcription for accents or noisy audio (default `base`) |
| `--lang en` | Force the transcription language |
| `--frames N` | Frame budget (auto: 6–16 by length) |
| `--fresh` | Ignore the cache |
| `--no-view` | No viewer window (or set `REELSIGHT_NO_VIEW=1`) |
| `--max-minutes N` | Allow longer videos (default 15) |

## If something fails

- A download error on a public post usually means yt-dlp is out of date. Run `uv run --upgrade-package yt-dlp --script "<skill-dir>/scripts/watch.py" --help` once, then retry.
- If `curl` is missing or has no network access, `uv` can't be installed. Tell the user to install it from https://docs.astral.sh/uv/.
