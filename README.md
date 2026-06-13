# 🎬 AI Edit Videos — Admin-only Telegram Video Repurposing Bot

A lightweight, **admin-only** Telegram bot that downloads Instagram Reels /
TikTok links (or your direct uploads), applies **minimal surgical FFmpeg edits**
to alter the perceptual/audio fingerprint used by duplicate-content detectors,
and sends the edited clip back **only to you** with a clean inline control panel.

Built to run **24/7 on a Raspberry Pi 5 (8GB)** — async, queued, and frugal
with memory and CPU.

> ⚠️ **Use responsibly.** This tool is intended for *your own* content and for
> legitimate repurposing/format-conversion workflows. Respect each platform's
> Terms of Service and applicable copyright law.

---

## 1. Recommended tech stack (Raspberry Pi 5 8GB)

| Layer            | Choice                          | Why on a Pi 5 |
|------------------|---------------------------------|---------------|
| OS               | Raspberry Pi OS (64-bit, Bookworm) | Best driver + FFmpeg support |
| Language         | Python 3.11+                    | Ships with Bookworm, great async |
| Bot framework    | **aiogram 3** (async)           | Modern, fast, low overhead |
| Downloader       | **yt-dlp**                      | Robust Reels/TikTok extraction |
| Video engine     | **FFmpeg** (`libx264` encode, `v4l2m2m` HW decode) | Stable & efficient for short clips |
| Job queue        | `asyncio.Queue` + worker pool   | Zero extra services, caps concurrency |
| Research (opt.)  | **Tavily** API                  | Lightweight current-info lookups |
| Process manager  | **systemd**                     | Auto-restart, resource limits |

---

## 2. Project structure

```
Ai-Edit-videos/
├── bot/
│   ├── __init__.py
│   ├── main.py              # entrypoint: wires queue + dispatcher, polls
│   ├── config.py            # .env-backed settings (frozen dataclass)
│   ├── filters.py           # IsAdmin gate (single user ID)
│   ├── keyboards.py         # the inline keyboard layouts
│   ├── handlers/
│   │   ├── __init__.py      # build_router() aggregates all routers
│   │   ├── commands.py      # /start /status /research
│   │   ├── media.py         # link + video-upload handlers
│   │   └── callbacks.py     # all inline-button handlers
│   ├── services/
│   │   ├── downloader.py    # yt-dlp wrapper (threaded)
│   │   ├── editor.py        # FFmpeg edit recipes (light/medium/strong)
│   │   ├── processor.py     # render + send-with-keyboard glue
│   │   ├── queue.py         # bounded async job queue
│   │   ├── storage.py       # in-memory job registry
│   │   └── research.py      # Tavily client
│   └── utils/
│       ├── ffmpeg.py        # HW-decode detection + ffprobe
│       └── cleanup.py       # per-job + periodic temp cleanup
├── systemd/ai-edit-bot.service
├── scripts/install_pi.sh
├── requirements.txt
├── .env.example
└── README.md
```

---

## 3. Inline keyboard

Sent under **every** finished video:

```
Row 1 – Edit Intensity
[🟢 Light Edit] [🟡 Medium Edit] [🔴 Strong Edit]

Row 2 – Variants
[🎲 Generate New Variant] [⚙️ Try Different Settings]

Row 3 – Actions
[💾 Save to Folder] [📤 Forward to Channel] [🗑 Delete this version]

Row 4 – Quick Options
[⬇️ Download Original] [ℹ️ Show Processing Info]
```

**What each button does** (`bot/handlers/callbacks.py`):

| Button | Action |
|--------|--------|
| Light / Medium / Strong | Set intensity and **re-render** the source through the queue |
| Generate New Variant | Re-render at the same settings — internal randomness makes a unique file |
| Try Different Settings | Open a sub-menu to toggle `flip / zoom / color / pitch`, then re-render |
| Save to Folder | Copy the current render into `SAVE_DIR` with a timestamped name |
| Forward to Channel | Send the render to `FORWARD_CHANNEL_ID` |
| Delete this version | Delete the message + wipe the job's temp files |
| Download Original | Send back the unedited source as a document |
| Show Processing Info | Resolution, duration, codec, render time, HW-accel status |

---

## 4. FFmpeg edit recipes (minimal, surgical)

Each intensity nudges the things a fingerprint relies on, within a randomized
band so no two renders are identical (`bot/services/editor.py`):

| Intensity | Crop | Color/contrast/sat | Hue | Speed jitter | Notes |
|-----------|------|--------------------|-----|--------------|-------|
| 🟢 Light  | ±2 px | ±1% | ±0.4° | ±0.5% | barely-there |
| 🟡 Medium | ±6 px | ±3% | ±1.2° | ±1.2% | default |
| 🔴 Strong | ±12 px | ±6% | ±2.5° | ±2.0% | + optional flip/zoom/pitch |

Always applied: re-encode (`libx264 -crf 23 -preset veryfast`),
`-map_metadata -1` (strip all metadata), fresh `comment` tag, `+faststart`.
Example generated command:

```bash
ffmpeg -y -threads 3 -hwaccel v4l2m2m -i source.mp4 \
  -vf "crop=iw-12:ih-12:6:6,scale=trunc(iw/2)*2:trunc(ih/2)*2,\
eq=brightness=0.012:contrast=1.008:saturation=0.991,hue=h=0.7,setpts=0.994*PTS" \
  -af "atempo=1.006" \
  -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -movflags +faststart -map_metadata -1 \
  edited_medium.mp4
```

> The bot auto-detects `h264_v4l2m2m` and falls back to software decode if a
> hardware attempt fails — so it just works on any Pi.

---

## 5. Job queue

A bounded `asyncio.Queue` with a fixed worker pool (`bot/services/queue.py`).
`MAX_CONCURRENT_JOBS` (default **1**) caps how many FFmpeg renders run at once —
the single most important setting for keeping a Pi responsive. Extra requests
queue FIFO and the user is told their position.

---

## 6. Setup on Raspberry Pi 5

```bash
git clone <your-repo-url> Ai-Edit-videos
cd Ai-Edit-videos
bash scripts/install_pi.sh        # installs ffmpeg + venv + deps
nano .env                         # set BOT_TOKEN and ADMIN_ID
source .venv/bin/activate
python -m bot.main                # test run
```

Install as a 24/7 service:

```bash
sudo cp systemd/ai-edit-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-edit-bot
journalctl -u ai-edit-bot -f
```

### `.env`

See [`.env.example`](.env.example). Minimum required:

```ini
BOT_TOKEN=123456789:AA...      # from @BotFather
ADMIN_ID=123456789             # from @userinfobot
```

---

## 7. Pi 5 optimization tips

- **Keep `MAX_CONCURRENT_JOBS=1`.** One render at a time keeps RAM/heat sane;
  go to 2 only if your clips are short and you have active cooling.
- **Cap input resolution** — the downloader already limits to ≤1080p.
- **Use `-preset veryfast`** (already set): the speed/quality sweet spot on ARM.
- **Active cooling** (the official Pi 5 fan/case) prevents thermal throttling
  during back-to-back renders.
- **Temp on tmpfs:** the default `WORK_DIR=/tmp/...` lives in RAM on most Pi
  setups — fast and self-clearing. With 8GB this is fine for short clips.
- **systemd `MemoryMax=3G`** guards the OS if a pathological file slips through.
- **Auto-cleanup**: per-job temp dirs are deleted on completion, plus a sweeper
  removes anything older than 2h every 30 min.
