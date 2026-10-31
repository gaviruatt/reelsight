---
name: reelsight
description: Watch and understand short-form video — Instagram Reels, TikTok, YouTube Shorts, X/Twitter, Facebook, Reddit, Snapchat, Twitch clips, or a local video file. Downloads it, transcribes the speech, captures frames at the important moments (cuts, audio hits, hook, ending), streams progress to a live browser viewer, and returns a compact digest plus one labeled contact-sheet image. Use whenever the user shares a reel/short/TikTok/video link or file and wants it watched, summarised, explained, transcribed, fact-checked, critiqued, or turned into notes, scripts, hooks or recipes.
---

# ReelSight

Watch a video by running one script, then understand it from the text it prints plus **one** image.

## 1. Run

```bash
uv run "<skill-dir>/scripts/watch.py" "<url-or-path>"
```

- Use Bash timeout `600000`. First run installs dependencies and a speech model (about 1 min); later runs take seconds, and repeat links come from the cache instantly.
- A live viewer window opens for the user automatically. Tell them it's there; don't drive it yourself.
- For several links, run the script once per link.

## 2. Understand

The script prints a digest: platform, creator, stats, caption, audio summary, editing pace, and a **timeline** where every key frame `#n` carries the speech heard until the next frame. The last line is `SHEET: <path>`.

1. Read the SHEET image **once**. Its tiles are labeled `#n time`, matching the timeline. Take on-screen text, people, products and setting from it.
2. Read a single `frames/fNN.jpg` only when a detail is too small to read on the sheet. Use `--at 12.4,15` to grab an exact moment at full resolution.
3. Don't read `state.json`, the video or `transcript.txt` unless the digest says the transcript was truncated. Everything else is already in the digest.

## 3. Answer

If the user asked something specific, answer that. Otherwise give: **the hook** (first 2 seconds), **what happens** (beat by beat, with timestamps), **the message or purpose**, **on-screen text**, **audio** (speech, music, voiceover), and **craft notes** (pacing, cuts, CTA, why it works). Keep it tight. The user can watch along in the viewer.

## Flags

| Flag | Use |
|---|---|
| `--cookies chrome` | Exit code 3 / `LOGIN_REQUIRED` (private or login-walled post). **Ask the user first.** Other values: `safari`, `firefox`, `edge`, `brave` |
| `--model small` | Better transcription for accents or noisy audio (default `base`) |
| `--lang en` | Force the transcription language |
| `--frames N` | Frame budget (auto: 6–16 by length) |
| `--fresh` | Ignore the cache |
| `--no-view` | No browser window (or set `REELSIGHT_NO_VIEW=1`) |
| `--max-minutes N` | Allow longer videos (default 15) |

## Troubleshooting

- `uv: command not found`: install it (`brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`), or run `python3 watch.py` after `pip install yt-dlp faster-whisper "av<16" pillow`.
- `ffmpeg not found`: `brew install ffmpeg` or `sudo apt install ffmpeg`.
- Download errors on a public post usually mean yt-dlp is out of date. Retry with `uv run --upgrade-package yt-dlp "<skill-dir>/scripts/watch.py" …`.
