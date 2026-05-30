# Photobooth

Kid-friendly photobooth for a Mac driving a tethered Canon EOS R6 against a physical green screen, with auto-upload to an Immich server.

Flow: attract → pick background → live composited preview → 3-2-1 → 3 shots → strip → upload → repeat.

---

## Setup

### 1. System dependencies

```bash
brew install libgphoto2
```

### 2. Python environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> If pip fails with SSL cert errors, add `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.

### 3. Generate sample backgrounds and sounds

```bash
python tools/make_samples.py   # → backgrounds/sample_*.jpg
python tools/make_sounds.py    # → assets/sounds/beep.wav, shutter.wav
```

Drop your own backgrounds (JPG or PNG, ideally 3:2 aspect, 6000×4000 for best quality) into `backgrounds/`. They'll show up automatically.

### 4. Configure Immich

In the Immich web UI:

1. Click your avatar → **Account Settings** → **API Keys** → **New API Key**.
2. Grant scopes: `asset.upload`, `asset.read`, `album.create`, `album.read`, `album.addAsset`.
3. Copy the key.

Then:

```bash
cp config.yaml.example config.yaml
# edit config.yaml — set immich.base_url and immich.api_key
```

Or use env vars: `PHOTOBOOTH_IMMICH_BASE_URL=https://immich.example.com PHOTOBOOTH_IMMICH_API_KEY=abc…`.

The first session creates a `Photobooth` album (or whatever you set `immich.album_name` to) and adds every subsequent shot to it.

---

## Run

```bash
./run.sh           # crash-recovery wrapper (auto-restarts on crash)
# or
python -m src.main # direct
```

### Keys

| Key | What it does |
|---|---|
| `Space` | Advance through ATTRACT → PICK → PREVIEW → CAPTURE → REVIEW → DONE |
| `← → ↑ ↓` | Move selection in the background picker |
| `Return` | Confirm background selection |
| `Esc Esc Esc` (within 1.5s) | Quit (adult chord — kid-resistant) |
| `Esc` (single, or slow) | **Intentionally does nothing** |
| `Cmd+,` | Toggle the on-site settings overlay (see below) |

---

## Configuration

Settings live in `config.yaml` (copy from `config.yaml.example`). Anything in the
file can be overridden by an environment variable named
`PHOTOBOOTH_<SECTION>_<KEY>` — e.g. `PHOTOBOOTH_IMMICH_API_KEY`,
`PHOTOBOOTH_SOUND_ENABLED=false`. Secrets (`smtp_password`, `ntfy_token`,
`immich.api_key`) are best set via the environment or a `.env` file rather than
committed to `config.yaml`.

| Section | Key knobs |
|---|---|
| `immich` | `base_url`, `api_key`, `album_name` |
| `camera` | capture target/format keys, `init_retries`, battery monitoring (`battery_poll_interval_s`, `battery_low_threshold_pct`) |
| `chroma` | green-key tuning: `hue_low/high`, `sat_min`, `val_min`, `feather_px_*`, `spill_suppress`, `guided_filter` |
| `strip` | `header_text` printed down the side of the strip |
| `ui` | `inactivity_timeout_s`, `countdown_seconds`, `capture_count`, `shutter_lead_ms` |
| `output` | `retain_count` — how many recent local files to keep per folder |
| `sound` | `enabled`, `volume` |
| `display` | `share_url` (+ `share_caption`) shown as text/QR on attract; `carousel_seconds` (strip carousel pace) |
| `alerts` | battery/status notifications — see below |

Changes made through the on-site overlay are written to `runtime_overrides.yaml`,
which is layered on top of `config.yaml` at launch. Load order:
defaults → `config.yaml` → `runtime_overrides.yaml` → environment.

---

## On-site tuning (settings overlay)

Press **`Cmd+,`** to open an adult-only overlay for live chroma-key tuning
without leaving the booth. The sliders (green hue range, saturation/value
thresholds, feathering, spill suppression, guided filter) modify the live
preview immediately.

- **Save** writes the current values to `runtime_overrides.yaml`, so they persist
  across the next launch.
- **Reset** clears those overrides and reverts to whatever `config.yaml` defines.

Overlay changes are session-local until you hit Save.

---

## Alerts (battery & status)

The booth can notify a phone when the camera battery runs low (and sends a
"booth is up, battery is X%" confirmation on startup). Configure under `alerts:`
in `config.yaml`. Two independent channels — each fires when configured, and both
fire if both are set:

- **ntfy push** (recommended) — set `ntfy_topic` to a hard-to-guess string and
  subscribe to it in the [ntfy](https://ntfy.sh) phone app (or open
  `https://ntfy.sh/<topic>`). No account needed on the public server. Point
  `ntfy_server` at your own instance if you self-host; for a protected topic set
  `PHOTOBOOTH_ALERTS_NTFY_TOKEN` in the environment.
- **Email-to-SMS gateway** — set `sms_to` to your carrier's gateway address
  (T-Mobile: `<10-digit-number>@tmomail.net`) and point the `smtp_*` fields at a
  relay you can send through (a Gmail account with an app password works). Put the
  password in `PHOTOBOOTH_ALERTS_SMTP_PASSWORD`, not the file. Free but flaky —
  hence the ntfy option.

Battery thresholds live under `camera:` (`battery_low_threshold_pct`,
`battery_poll_interval_s`; set the interval to `0` to disable polling).

Test your setup without launching the booth:

```bash
python tools/test_alert.py   # exercises every configured channel, prints results
```

---

## Hardware checklist (every session)

- [ ] R6 powered on, USB-C cable rated for **data** (not just charging) plugged in
- [ ] R6 **Wi-Fi off** (USB and Wi-Fi are mutually exclusive on R-series)
- [ ] R6 mode: **PHOTO** (not Movie)
- [ ] R6 AF mode: **Servo AF** (or AI Servo) so live view tracks focus continuously — capture will then be instant. With One-Shot AF the camera won't refocus before the shutter fires.
- [ ] R6 **AF beep: Off** (Menu → Set-up → Beep → Off). Servo AF chirps every time focus shifts; it sounds like a faint beep coming from the booth at random times.
- [ ] R6 **auto-poweroff: Disable** (Menu → Wrench → 2 → "Auto power off"). The app also writes this via `gphoto2`, but the camera ignores it sometimes; setting it on the body is reliable.
- [ ] **EOS Utility**, **EOS Webcam Utility**, **Image Capture**, **Photos** all quit before launching (the app refuses to start if they're running and prints which one)
- [ ] Green screen evenly lit, no wrinkles or shadows on the screen
- [ ] Subject lit from the front, away from the screen so they don't catch green spill

---

## Immich API key (one-time)

Immich web UI → avatar → **Account Settings** → **API Keys** → **New API Key**.

Required scopes:

- `asset.upload` — upload photos
- `asset.read` — needed by some album endpoints
- `album.create` — create the photobooth album on first run
- `album.read` — find the photobooth album on subsequent runs
- `album.addAsset` — add uploaded assets to it

Paste the resulting key into `config.yaml` under `immich.api_key` (or set `PHOTOBOOTH_IMMICH_API_KEY` in your environment).

---

## Troubleshooting

### "No camera detected"
- USB cable issue? Many "USB-C" cables are charge-only — try a known-good data cable.
- R6 Wi-Fi on? Toggle it off.
- `ptpcamerad` is the macOS daemon that claims PTP devices and breaks `gphoto2`. The app kills it on start, but if it still wins the race:
  - In Terminal: `killall -9 ptpcamerad` (then quickly launch the app).
  - Or run the app via `sudo` once: `sudo ./run.sh`.

### "Conflicting app running: …"
- Quit EOS Utility, EOS Webcam Utility, Image Capture, or Photos. macOS launches Image Capture or Photos automatically when a camera is plugged in — turn that off in **System Settings → Image Capture**.

### Live preview is choppy (<15 fps)
- Check the cable (USB 2.0 vs USB 3 vs charge-only) — it matters.
- Quit other USB-heavy processes on the Mac.
- If you ran `gphoto2 --list-all-config` against the R6 ever, the live view can get stuck on the body's "PC connected" icon. Unplug → power-cycle the camera → plug back in.

### Green edges / "halo" around the subject
- Open the settings overlay (`Cmd+,`) and tune the chroma sliders.
- Move the subject **further from** the green screen — proximity causes spill.
- Add front fill light. Even, soft light beats bright spots.
- If hair detail is bad, try toggling `chroma.guided_filter: true` in config (default on; requires `opencv-contrib-python`).

### Uploads aren't appearing in Immich
- Check the API key has the scopes listed above.
- Check `immich.base_url` is reachable from the Mac (try `curl -H "x-api-key: …" $URL/api/server/version`).
- If a session ended with "queued for retry", files are in `output/pending_upload/`. The app drains that folder on startup; just relaunch.

### The app crashed
- The wrapper script (`run.sh`) auto-restarts on a non-clean exit. Check the log at `~/.photobooth/log.txt` (also echoed to stderr).
- If `ptpcamerad` SIGABRT'd libgphoto2, that's a known macOS issue — usually resolves with `killall -9 ptpcamerad` + replug.

### Not getting battery / low-battery alerts
- Run `python tools/test_alert.py` — it prints the resolved config and the result of a send on each configured channel.
- ntfy: confirm the phone app is subscribed to the exact `ntfy_topic`, and that the Mac can reach `ntfy_server`.
- Email-to-SMS: gateways are unreliable and slow; if it's flaky, prefer the ntfy channel.

---

## Project layout

```
photobooth/
├── README.md
├── requirements.txt
├── config.yaml.example   # checked in
├── config.yaml           # gitignored, your secrets live here
├── run.sh                # crash-recovery wrapper
├── assets/
│   ├── fonts/            # (drop a TTF here for the strip header; system fallback works too)
│   └── sounds/           # beep.wav, shutter.wav (generate via tools/make_sounds.py)
├── backgrounds/          # samples shipped; drop your own JPG/PNG here
├── output/
│   ├── raw/              # unedited camera JPEGs, retained locally
│   ├── composite/        # green-screened versions
│   ├── strips/           # the photobooth strips
│   └── pending_upload/   # retry queue if Immich was unreachable
├── src/                  # app code (camera, chroma, compositor, immich, notify, ui/…)
├── tests/
└── tools/
    ├── make_samples.py       # generate sample backgrounds
    ├── make_sounds.py        # generate beep.wav / shutter.wav
    ├── test_alert.py         # test the configured alert channels
    ├── test_composite.py     # test green-screen compositing on a still
    ├── probe_*.py            # one-off gphoto2 diagnostics (camera, battery, AF, …)
    ├── upscale.py            # AI-art → 6000×4000 background upscaler (see tools/README.md)
    └── requirements-upscale.txt
```

The upscaler runs in its own `.venv-upscale` (a large PyTorch dependency the
booth itself doesn't need) — see [`tools/README.md`](tools/README.md).

---

## Displays & UI

The fullscreen UI adapts to whatever display it runs on — any size, and either
orientation (a portrait kiosk monitor or a landscape laptop/TV). There is no
fixed target resolution.

If you're working on the UI, keep it that way:

- **Scale type and spacing off the *short side* of the screen** (`min(width,
  height)`), via `src/ui/scale.py` (`scale_px`, `short_side`) — baseline 1080.
  Never derive sizes from one fixed axis; that silently bakes in an
  orientation (it's how the original portrait-only layout went wrong).
- **Size images/frames to their own content aspect and center them** — never
  stretch a container or border to fill the screen (see `_FramedImage` in
  `attract_widget.py`, whose frame hugs the photo strip).
- **Reflow multi-pane layouts along the long axis**: the attract screen flips
  side-by-side ↔ stacked, the review screen lays the three photos in a row
  (landscape) or column (portrait), and the picker's column count follows the
  width.
- A plain `QWidget` ignores its own `background-color` stylesheet unless it has
  `WA_StyledBackground` set (otherwise it shows the window's background), and a
  custom `paintEvent` leaves unpainted areas black — fill the full rect first.

`tests/test_responsive_layout.py` pins all of this in both orientations. When
eyeballing a change, render offscreen at e.g. `1728×1117` and `1117×1728`.

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest
```

State-machine + chroma-key + compositor + Immich (mocked) + retention +
responsive-layout (both orientations) tests all run headless. Camera-worker
tests cover the non-hardware paths (conflict detection, fallback when
`gphoto2` isn't importable). Real-hardware verification is manual — see the
**Hardware checklist** above.
