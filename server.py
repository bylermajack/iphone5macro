#!/usr/bin/env python3
"""
PhoneDeck - turn an old iPhone (iOS 10 / iPhone 5) into a macro pad for your PC.

Run this on the PC:   python server.py
Then open the printed URL in Safari on the phone (same Wi-Fi).

Pure standard library. Works on Windows, Linux (X11 via xdotool) and macOS.
If `pyautogui` is installed it is used for keys/typing on every OS.
"""
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
TOKEN_PATH = os.path.join(HERE, "token.txt")
STATIC_DIR = os.path.join(HERE, "static")
PHOTO_DIR = os.path.join(HERE, "photos")
PORT = int(os.environ.get("PHONEDECK_PORT", "8765"))
OS = platform.system()  # "Windows", "Linux", "Darwin"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stats as stats_mod

STATS = None  # started in main()

try:
    import pyautogui  # optional
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None


# --------------------------------------------------------------------------
# Config + token
# --------------------------------------------------------------------------
_config_cache = {"mtime": None, "data": None}


def load_config():
    """Reload config.json whenever it changes, so edits apply without restart."""
    mtime = os.path.getmtime(CONFIG_PATH)
    if _config_cache["mtime"] != mtime:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache["data"] = json.load(f)
        _config_cache["mtime"] = mtime
    return _config_cache["data"]


def load_token():
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            tok = f.read().strip()
            if tok:
                return tok
    tok = secrets.token_hex(4)
    with open(TOKEN_PATH, "w") as f:
        f.write(tok)
    return tok


TOKEN = load_token()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is actually sent
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------
# Key sending, per OS
# --------------------------------------------------------------------------
WIN_VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "super": 0x5B, "cmd": 0x5B, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "printscreen": 0x2C, "capslock": 0x14,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2,
}
WIN_EXTENDED = {0x2E, 0x2D, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22,
                0x5B, 0xAF, 0xAE, 0xAD, 0xB3, 0xB0, 0xB1, 0xB2}

X_KEYS = {
    "ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt",
    "win": "super", "super": "super", "cmd": "super", "enter": "Return",
    "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "del": "Delete", "insert": "Insert", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "printscreen": "Print",
    "capslock": "Caps_Lock",
    "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume",
    "volumemute": "XF86AudioMute", "playpause": "XF86AudioPlay",
    "nexttrack": "XF86AudioNext", "prevtrack": "XF86AudioPrev",
    "stop": "XF86AudioStop",
}

MAC_MODS = {"ctrl": "control down", "control": "control down",
            "shift": "shift down", "alt": "option down", "option": "option down",
            "cmd": "command down", "win": "command down", "super": "command down"}
MAC_CODES = {"enter": 36, "return": 36, "esc": 53, "escape": 53, "tab": 48,
             "space": 49, "backspace": 51, "delete": 117, "del": 117,
             "up": 126, "down": 125, "left": 123, "right": 124, "home": 115,
             "end": 119, "pageup": 116, "pagedown": 121,
             "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
             "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}


def _win_vk(k):
    k = k.lower()
    if k in WIN_VK:
        return WIN_VK[k]
    if len(k) == 1 and (k.isalpha() or k.isdigit()):
        return ord(k.upper())
    if k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x6F + int(k[1:])
    raise ValueError("Unknown key: %s" % k)


def _x_key(k):
    lk = k.lower()
    if lk in X_KEYS:
        return X_KEYS[lk]
    if lk.startswith("f") and lk[1:].isdigit():
        return "F" + lk[1:]
    return k


def press_keys(keys):
    """Press a chord like ["ctrl", "shift", "m"]: hold all, release in reverse."""
    keys = [str(k) for k in keys]
    if OS == "Windows":
        import ctypes
        user32 = ctypes.windll.user32
        vks = [_win_vk(k) for k in keys]
        for vk in vks:
            user32.keybd_event(vk, 0, 0x1 if vk in WIN_EXTENDED else 0, 0)
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, (0x1 if vk in WIN_EXTENDED else 0) | 0x2, 0)
        return
    if OS == "Linux":
        if shutil.which("xdotool"):
            subprocess.run(["xdotool", "key", "+".join(_x_key(k) for k in keys)], check=True)
            return
        if pyautogui:
            pyautogui.hotkey(*keys)
            return
        raise RuntimeError("Install xdotool:  sudo apt install xdotool")
    if OS == "Darwin":
        mods = [MAC_MODS[k.lower()] for k in keys if k.lower() in MAC_MODS]
        rest = [k for k in keys if k.lower() not in MAC_MODS]
        if len(rest) != 1:
            raise ValueError("macOS hotkey needs exactly one non-modifier key")
        key = rest[0].lower()
        using = (" using {%s}" % ", ".join(mods)) if mods else ""
        if key in MAC_CODES:
            script = 'tell application "System Events" to key code %d%s' % (MAC_CODES[key], using)
        else:
            script = 'tell application "System Events" to keystroke "%s"%s' % (key, using)
        subprocess.run(["osascript", "-e", script], check=True)
        return
    if pyautogui:
        pyautogui.hotkey(*keys)
        return
    raise RuntimeError("Unsupported OS for hotkeys: " + OS)


def type_text(text):
    if pyautogui and OS != "Windows" and text.isascii():
        pyautogui.write(text, interval=0.01)
        return
    if OS == "Windows":
        # Put text on the clipboard (unicode-safe) and paste it.
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        time.sleep(0.05)
        press_keys(["ctrl", "v"])
        return
    if OS == "Linux":
        if shutil.which("xdotool"):
            subprocess.run(["xdotool", "type", "--delay", "5", "--", text], check=True)
            return
        raise RuntimeError("Install xdotool:  sudo apt install xdotool")
    if OS == "Darwin":
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e",
                        'tell application "System Events" to keystroke "%s"' % safe], check=True)
        return
    raise RuntimeError("Unsupported OS for typing: " + OS)


MEDIA_KEYS = ("playpause", "nexttrack", "prevtrack", "stop",
              "volumeup", "volumedown", "volumemute")


def _mac_player():
    for app in ("Spotify", "Music"):
        if subprocess.run(["pgrep", "-x", app], capture_output=True).returncode == 0:
            return app
    return None


def media(key):
    """Media / volume control that works on each OS (used by the dashboard too)."""
    if key not in MEDIA_KEYS:
        raise ValueError("not a media key: " + key)
    if OS == "Darwin":
        vol = {"volumeup": "set volume output volume ((output volume of (get volume settings)) + 6)",
               "volumedown": "set volume output volume ((output volume of (get volume settings)) - 6)",
               "volumemute": "set volume output muted (not (output muted of (get volume settings)))"}
        if key in vol:
            script = vol[key]
        else:
            app = _mac_player()
            if not app:
                raise RuntimeError("Open Spotify or Music first")
            cmd = {"playpause": "playpause", "nexttrack": "next track",
                   "prevtrack": "previous track", "stop": "pause"}[key]
            script = 'tell application "%s" to %s' % (app, cmd)
        subprocess.run(["osascript", "-e", script], check=True)
        return
    if OS == "Linux":
        pc = {"playpause": "play-pause", "nexttrack": "next", "prevtrack": "previous", "stop": "stop"}
        if key in pc and shutil.which("playerctl"):
            subprocess.run(["playerctl", pc[key]], check=False)
            return
        pa = {"volumeup": ["set-sink-volume", "@DEFAULT_SINK@", "+5%"],
              "volumedown": ["set-sink-volume", "@DEFAULT_SINK@", "-5%"],
              "volumemute": ["set-sink-mute", "@DEFAULT_SINK@", "toggle"]}
        if key in pa and shutil.which("pactl"):
            subprocess.run(["pactl"] + pa[key], check=True)
            return
    press_keys([key])


def open_target(target):
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
    elif OS == "Windows":
        os.startfile(target)  # noqa: works for files, folders and apps
    elif OS == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
def run_action(action):
    t = action.get("type")
    if t == "hotkey":
        keys = action["keys"]
        if len(keys) == 1 and str(keys[0]).lower() in MEDIA_KEYS:
            media(str(keys[0]).lower())
        else:
            press_keys(keys)
    elif t == "type":
        type_text(action["text"])
    elif t == "open":
        open_target(action["target"])
    elif t == "run":
        # Runs a shell command you wrote in config.json (never anything from the phone).
        subprocess.Popen(action["cmd"], shell=True, cwd=action.get("cwd") or None)
    elif t == "wait":
        time.sleep(float(action.get("seconds", 0.2)))
    elif t == "multi":
        for step in action["steps"]:
            run_action(step)
            time.sleep(float(action.get("delay", 0.05)))
    else:
        raise ValueError("Unknown action type: %r" % t)


def public_config(cfg):
    """What the phone gets: labels/looks only, never the commands."""
    pages = []
    for p in cfg.get("pages", []):
        pages.append({
            "name": p.get("name", "Page"),
            "buttons": [{"label": b.get("label", ""), "icon": b.get("icon", ""),
                         "color": b.get("color", "")} for b in p.get("buttons", [])],
        })
    return {"title": cfg.get("title", "PhoneDeck"), "columns": cfg.get("columns", 3),
            "pages": pages, "host": socket.gethostname(), "os": OS,
            "dashboard": cfg.get("dashboard", True)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "PhoneDeck/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the console clean; we log presses ourselves

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, qs):
        tok = self.headers.get("X-Token") or (qs.get("k") or [""])[0]
        return secrets.compare_digest(tok, TOKEN)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/config":
            if not self._authed(qs):
                return self._send(401, {"error": "bad key"})
            try:
                return self._send(200, public_config(load_config()))
            except Exception as e:
                return self._send(500, {"error": "config.json: %s" % e})
        if u.path == "/api/stats":
            if not self._authed(qs):
                return self._send(401, {"error": "bad key"})
            return self._send(200, STATS.get())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self._authed(qs):
            return self._send(401, {"error": "bad key"})
        length = int(self.headers.get("Content-Length") or 0)

        if u.path == "/api/press":
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
                page = load_config()["pages"][int(req["page"])]
                btn = page["buttons"][int(req["button"])]
            except Exception:
                return self._send(400, {"error": "unknown button"})
            print("[%s] %s / %s" % (datetime.now().strftime("%H:%M:%S"),
                                    page.get("name"), btn.get("label")))
            try:
                run_action(btn["action"])
                return self._send(200, {"ok": True})
            except Exception as e:
                print("   ! failed:", e)
                return self._send(500, {"error": str(e)})

        if u.path == "/api/media":
            try:
                key = json.loads(self.rfile.read(length) or b"{}").get("key", "")
                media(key)
                return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if u.path == "/api/photo":
            if length <= 0 or length > 30 * 1024 * 1024:
                return self._send(400, {"error": "bad size"})
            data = self.rfile.read(length)
            os.makedirs(PHOTO_DIR, exist_ok=True)
            ext = ".png" if "png" in (self.headers.get("Content-Type") or "") else ".jpg"
            name = datetime.now().strftime("phone_%Y%m%d_%H%M%S") + ext
            path = os.path.join(PHOTO_DIR, name)
            with open(path, "wb") as f:
                f.write(data)
            print("[photo] saved", path)
            if load_config().get("open_photos", True):
                try:
                    open_target(path)
                except Exception:
                    pass
            return self._send(200, {"ok": True, "file": name})

        return self._send(404, {"error": "not found"})


def main():
    global STATS
    load_config()  # fail fast on a broken config
    STATS = stats_mod.Stats()
    ip = lan_ip()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    url = "http://%s:%d/?k=%s" % (ip, PORT, TOKEN)
    print("=" * 56)
    print(" PhoneDeck running on", OS)
    print(" On the iPhone (same Wi-Fi) open in Safari:")
    print("\n   ", url, "\n")
    print(" Then Share -> Add to Home Screen for full-screen mode.")
    if OS == "Linux" and not shutil.which("xdotool") and not pyautogui:
        print(" ! xdotool not found: sudo apt install xdotool")
    if OS == "Linux" and not shutil.which("playerctl"):
        print(" ! for song info on the dashboard: sudo apt install playerctl")
    if not stats_mod.psutil:
        print(" tip: pip install psutil  -> more accurate PC stats")
    print(" Edit config.json any time; changes apply on refresh.")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
