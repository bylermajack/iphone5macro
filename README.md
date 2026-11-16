# iPhone 5 Macro Deck (PhoneDeck)

Turn an old **iPhone 5** (or any old phone) into a **macro pad / stream deck** and a
**second screen** for your PC — over Wi-Fi, with nothing to install on the phone.

- 🎛 **Macro buttons** — hotkeys, typing text, opening apps/URLs, running commands, multi-step macros
- 📊 **Second screen** — big clock, now-playing music controls, live PC stats (CPU, RAM, GPU, disk, network, battery)
- 📷 **Photo to PC** — snap a photo on the phone, it lands in a folder on the PC
- 🖥 Works with **Windows, Linux and macOS**
- 🐍 Pure Python standard library — no `pip install` required
- 📱 Phone side is a web page built for **Safari on iOS 10** (the newest an iPhone 5 can run)

```
 iPhone 5 (Safari)  ──── same Wi-Fi ────►  PC running server.py
   tap a button                              presses the keys / runs the action
```

---

## Quick start

### 1. Run the server on your computer

Needs Python 3.8+.

**Windows**
```
python server.py
```
Allow it through Windows Firewall when asked (tick **Private networks**).

**Linux**
```
sudo apt install xdotool playerctl
python3 server.py
```
`xdotool` presses keys and types (X11). `playerctl` shows the current song.
On Wayland, xdotool only reaches XWayland apps — log into an "Xorg" session for full support.
If you use ufw: `sudo ufw allow 8765/tcp`.

**macOS**
```
caffeinate -i python3 server.py
```
Run it from the **Terminal** app, then give Terminal permission to press keys:
**System Settings → Privacy & Security → Accessibility → + → Terminal → on**, and restart the server.
(`caffeinate` stops the Mac from sleeping while the server runs.)

The server prints a link like:
```
http://192.168.1.20:8765/?k=1a2b3c4d
```

### 2. Open it on the iPhone

1. Connect the iPhone to the **same Wi-Fi** as the computer.
2. Type the full link (including `?k=…`) into **Safari**.
3. Tap **Share → Add to Home Screen** — it now opens full-screen like an app.
4. Settings → Display & Brightness → **Auto-Lock → Never**, and keep it on a stand with a charger.

The `?k=…` key is remembered by the phone. Requests without it are refused, so other people
on your Wi-Fi can't press your buttons. Delete `token.txt` to generate a new key.

### Optional

```
pip install psutil
```
Gives more accurate stats on every OS, and network speed on Windows/macOS.

---

## The 📊 Screen tab (second screen)

- **Clock** — big time and date. Tap for a full-screen clock; double-tap to switch 12h / 24h.
- **Now playing** — song, artist, ⏮ ⏯ ⏭ and volume buttons.
  - Windows: any app using Windows media controls (Spotify, browsers/YouTube, …)
  - Linux: any MPRIS player via `playerctl`; volume via `pactl`
  - macOS: Spotify and Apple Music; volume works system-wide
- **PC stats** every 2 s — CPU %, RAM, disk, network ↓↑, battery, uptime,
  CPU temperature (Linux) and NVIDIA GPU load/temp/VRAM (when `nvidia-smi` is available).

Turn it off with `"dashboard": false` in `config.json`.

---

## Customising buttons — `config.json`

Two ready-made button sets are included:

| File                  | For             |
|-----------------------|-----------------|
| `config.json`         | macOS shortcuts (⌘) |
| `config.windows.json` | Windows / Linux shortcuts (Ctrl / Win) |

On Windows or Linux, copy `config.windows.json` over `config.json`.
Edits apply as soon as you tap **⟳** on the phone — no restart needed.

```json
{ "label": "Mute", "icon": "🔇", "color": "#7a2330",
  "action": { "type": "hotkey", "keys": ["ctrl", "shift", "m"] } }
```

### Action types

| type     | fields                  | what it does                                |
|----------|-------------------------|---------------------------------------------|
| `hotkey` | `keys: [...]`           | Presses a key combination                   |
| `type`   | `text`                  | Types text                                  |
| `open`   | `target`                | Opens a URL, file, folder or app            |
| `run`    | `cmd`, optional `cwd`   | Runs a shell command                        |
| `wait`   | `seconds`               | Pauses (use inside `multi`)                 |
| `multi`  | `steps: [...]`, `delay` | Runs several actions in order — real macros |

### Key names

`ctrl shift alt cmd win enter esc tab space backspace delete up down left right
home end pageup pagedown printscreen f1…f24 a…z 0…9`

Media: `playpause nexttrack prevtrack stop volumeup volumedown volumemute`

On macOS, `cmd` is ⌘ and `alt` is ⌥. On Windows/Linux, `win` / `cmd` is the Windows/Super key.

### Other settings

| key           | default | meaning                                    |
|---------------|---------|--------------------------------------------|
| `title`       | PhoneDeck | Name shown at the top                    |
| `columns`     | 3       | Buttons per row (3 suits the iPhone 5)     |
| `dashboard`   | true    | Show the 📊 Screen tab                     |
| `open_photos` | true    | Open photos on the PC when they arrive     |

### Example commands for `run`

- Windows: `start steam://rungameid/570` · `shutdown /s /t 60`
- Linux: `playerctl play-pause` · `systemctl suspend`
- macOS: `open -a Spotify` · `pmset displaysleepnow`

**OBS:** in OBS → Settings → Hotkeys, set scene/record/stream hotkeys to the combos used on
the OBS page (Ctrl+Alt+F1…F11), or change them in `config.json`.

---

## 📷 Photo to PC

Tap 📷 at the top, take or pick a photo, and it's saved to `photos/` on the computer.

**Live webcam isn't possible from Safari on iOS 10** (web pages only got camera access in iOS 11).
On a Mac you can still use the iPhone as a webcam over the Lightning cable:
QuickTime Player → File → New Movie Recording → choose the iPhone → capture that window
in OBS → **Start Virtual Camera**.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Phone says "PC offline" | Same Wi-Fi? Server running? Allow port 8765 in the firewall. |
| "Key rejected" | Re-open the full link printed by the server (with `?k=…`). |
| Buttons flash red on macOS, log says *not allowed to send keystrokes* | Give **Terminal** Accessibility permission (see macOS above) and restart the server. |
| Nothing happens on Linux | `sudo apt install xdotool`; use an Xorg session. |
| No song shown | Linux: install `playerctl`. macOS: only Spotify / Music are supported. |

---

## Files

```
server.py             web server + actions (keys, typing, apps, media)
stats.py              PC stats + now-playing
static/index.html     the phone app (ES5, works on iOS 10 Safari)
config.json           macOS buttons
config.windows.json   Windows / Linux buttons
```

Change the port with the `PHONEDECK_PORT` environment variable (default 8765).

⚠️ Anyone with the link + key can trigger your buttons, including `run` commands — only use it on a network you trust.
