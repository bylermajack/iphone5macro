"""
PC stats + "now playing" for the PhoneDeck dashboard.

Standard library only. If `psutil` is installed (pip install psutil) it is used
for CPU / RAM / disk / network / battery on every OS, which is the most accurate.
NVIDIA GPU stats come from `nvidia-smi` when it is on PATH.

A background thread samples every 2 seconds, so requests from the phone are instant.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time

OS = platform.system()

try:
    import psutil
except Exception:
    psutil = None

NO_WINDOW = 0x08000000 if OS == "Windows" else 0  # don't flash console windows


def _run(cmd, timeout=2):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             creationflags=NO_WINDOW)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------
# Raw readers per OS. Each returns plain numbers; the sampler turns them into rates.
# --------------------------------------------------------------------------
if OS == "Windows":
    import ctypes
    from ctypes import wintypes

    class _FT(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class _MEM(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _PWR(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    def _ft(f):
        return (f.hi << 32) | f.lo

    def cpu_times():
        idle, kern, user = _FT(), _FT(), _FT()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        total = _ft(kern) + _ft(user)  # kernel time already includes idle
        return _ft(idle), total

    def mem():
        m = _MEM()
        m.dwLength = ctypes.sizeof(_MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys - m.ullAvailPhys, m.ullTotalPhys

    def battery():
        p = _PWR()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)):
            return None
        if p.BatteryFlag & 128 or (p.BatteryLifePercent & 0xFF) == 255:
            return None  # no battery (desktop PC)
        return {"percent": p.BatteryLifePercent & 0xFF, "plugged": p.ACLineStatus == 1}

    def uptime():
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0

    def net_bytes():
        return None

    def temp():
        return None

    DISK_PATH = os.environ.get("SystemDrive", "C:") + "\\"

elif OS == "Linux":
    def cpu_times():
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return idle, sum(parts[:8])

    def mem():
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        total = info.get("MemTotal", 0)
        return total - info.get("MemAvailable", info.get("MemFree", 0)), total

    def battery():
        base = "/sys/class/power_supply"
        try:
            for name in sorted(os.listdir(base)):
                if name.startswith("BAT"):
                    with open(os.path.join(base, name, "capacity")) as f:
                        pct = int(f.read())
                    with open(os.path.join(base, name, "status")) as f:
                        st = f.read().strip()
                    return {"percent": pct, "plugged": st != "Discharging"}
        except Exception:
            pass
        return None

    def uptime():
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])

    def net_bytes():
        rx = tx = 0
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                iface, data = line.split(":", 1)
                if iface.strip() == "lo":
                    continue
                d = data.split()
                rx += int(d[0]); tx += int(d[8])
        return rx, tx

    def temp():
        best = None
        base = "/sys/class/thermal"
        try:
            for z in os.listdir(base):
                if not z.startswith("thermal_zone"):
                    continue
                try:
                    with open(os.path.join(base, z, "type")) as f:
                        kind = f.read().strip()
                    with open(os.path.join(base, z, "temp")) as f:
                        c = int(f.read()) / 1000.0
                except Exception:
                    continue
                if kind in ("x86_pkg_temp", "k10temp", "cpu-thermal", "coretemp"):
                    return round(c)
                if 0 < c < 120 and (best is None or c > best):
                    best = c
        except Exception:
            pass
        return round(best) if best is not None else None

    DISK_PATH = "/"

else:  # macOS and anything else: rough fallbacks (install psutil for accuracy)
    def cpu_times():
        return None

    def mem():
        total = int(_run(["sysctl", "-n", "hw.memsize"]) or 0)
        vm = _run(["vm_stat"])
        page = 4096
        m = re.search(r"page size of (\d+)", vm)
        if m:
            page = int(m.group(1))
        def pages(label):
            m = re.search(label + r":\s+(\d+)", vm)
            return int(m.group(1)) if m else 0
        free = (pages("Pages free") + pages("Pages inactive") + pages("Pages speculative")) * page
        return max(total - free, 0), total

    def battery():
        out = _run(["pmset", "-g", "batt"])
        m = re.search(r"(\d+)%", out)
        if not m:
            return None
        return {"percent": int(m.group(1)), "plugged": "AC Power" in out}

    def uptime():
        out = _run(["sysctl", "-n", "kern.boottime"])
        m = re.search(r"sec = (\d+)", out)
        return time.time() - int(m.group(1)) if m else None

    def net_bytes():
        return None

    def temp():
        return None

    DISK_PATH = "/"


def gpu():
    """NVIDIA only, via nvidia-smi (ships with the driver on Windows and Linux)."""
    if not shutil.which("nvidia-smi"):
        return None
    out = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"])
    if not out:
        return None
    try:
        name, util, t, used, total = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name.replace("NVIDIA ", "").replace("GeForce ", ""), "percent": int(util),
                "temp": int(t), "mem_used": int(used) * 1024 * 1024, "mem_total": int(total) * 1024 * 1024}
    except Exception:
        return None


# --------------------------------------------------------------------------
# Now playing
# --------------------------------------------------------------------------
_WIN_MEDIA_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
while ($true) {
  $o = @{ title = ''; artist = ''; status = 'none'; app = '' }
  $s = $mgr.GetCurrentSession()
  if ($s) {
    $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $o.title = [string]$p.Title; $o.artist = [string]$p.Artist
    $o.status = ([string]$s.GetPlaybackInfo().PlaybackStatus).ToLower()
    $o.app = [string]$s.SourceAppUserModelId
  }
  [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress)); [Console]::Out.Flush()
  Start-Sleep -Seconds 2
}
"""

_MAC_MEDIA_AS = '''
tell application "%s"
  if player state is playing then
    set st to "playing"
  else if player state is paused then
    set st to "paused"
  else
    return ""
  end if
  return st & "|" & (artist of current track) & "|" & (name of current track)
end tell
'''


class NowPlaying:
    def __init__(self):
        self.data = {"title": "", "artist": "", "status": "none", "app": ""}
        self._proc = None
        if OS == "Windows":
            threading.Thread(target=self._windows_loop, daemon=True).start()

    def _windows_loop(self):
        while True:
            try:
                self._proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _WIN_MEDIA_PS],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                    creationflags=NO_WINDOW)
                for line in self._proc.stdout:
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            d = json.loads(line)
                            app = d.get("app", "")
                            d["app"] = app.split("!")[-1].split(".")[0] if app else ""
                            self.data = d
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(10)  # PowerShell missing / crashed: retry later

    def poll(self):
        """Called by the sampler thread (Linux/macOS)."""
        if OS == "Linux" and shutil.which("playerctl"):
            out = _run(["playerctl", "metadata", "--format",
                        "{{status}}\t{{artist}}\t{{title}}\t{{playerName}}"])
            if out:
                st, artist, title, app = (out.split("\t") + ["", "", "", ""])[:4]
                self.data = {"status": st.lower(), "artist": artist, "title": title, "app": app}
            else:
                self.data = {"title": "", "artist": "", "status": "none", "app": ""}
        elif OS == "Darwin":
            # Only talk to apps that are running (otherwise macOS may pop up dialogs).
            self.data = {"title": "", "artist": "", "status": "none", "app": ""}
            for app in ("Spotify", "Music"):
                if not _run(["pgrep", "-x", app]):
                    continue
                out = _run(["osascript", "-e", _MAC_MEDIA_AS % app])
                if out:
                    st, artist, title = (out.split("|") + ["", "", ""])[:3]
                    self.data = {"status": st, "artist": artist, "title": title, "app": app}
                    if st == "playing":
                        break


# --------------------------------------------------------------------------
# Sampler
# --------------------------------------------------------------------------
class Stats:
    INTERVAL = 2.0

    def __init__(self):
        self.now_playing = NowPlaying()
        self.snapshot = {}
        self._prev_cpu = None
        self._prev_net = None
        self._lock = threading.Lock()
        if psutil:
            psutil.cpu_percent(None)
        self._sample()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(self.INTERVAL)
            try:
                self._sample()
            except Exception as e:
                print("stats error:", e)

    def _cpu(self):
        if psutil:
            return psutil.cpu_percent(None)
        t = cpu_times()
        if t is None:  # macOS fallback: load average as a rough %
            try:
                return min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100)
            except Exception:
                return None
        prev, self._prev_cpu = self._prev_cpu, t
        if not prev or t[1] == prev[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (t[0] - prev[0]) / float(t[1] - prev[1]))))

    def _net(self, now):
        if psutil:
            c = psutil.net_io_counters()
            cur = (c.bytes_recv, c.bytes_sent)
        else:
            cur = net_bytes()
        if cur is None:
            return None
        prev, self._prev_net = self._prev_net, (now, cur)
        if not prev:
            return {"down": 0, "up": 0}
        dt = max(now - prev[0], 0.001)
        return {"down": max(0, (cur[0] - prev[1][0]) / dt), "up": max(0, (cur[1] - prev[1][1]) / dt)}

    def _sample(self):
        now = time.time()
        if psutil:
            vm = psutil.virtual_memory()
            m = (vm.total - vm.available, vm.total)
            b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            bat = {"percent": round(b.percent), "plugged": bool(b.power_plugged)} if b else None
            up = now - psutil.boot_time()
        else:
            m, bat, up = mem(), battery(), uptime()
        try:
            du = shutil.disk_usage(DISK_PATH)
            disk = {"used": du.used, "total": du.total}
        except Exception:
            disk = None
        self.now_playing.poll()
        snap = {
            "cpu": self._cpu(),
            "cores": os.cpu_count(),
            "mem": {"used": m[0], "total": m[1]} if m and m[1] else None,
            "disk": disk,
            "net": self._net(now),
            "battery": bat,
            "uptime": up,
            "temp": temp(),
            "gpu": gpu(),
        }
        with self._lock:
            self.snapshot = snap

    def get(self):
        with self._lock:
            d = dict(self.snapshot)
        d["media"] = self.now_playing.data
        d["time"] = time.time()
        return d
