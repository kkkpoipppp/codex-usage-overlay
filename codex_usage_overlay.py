import ctypes
import datetime as dt
import json
import math
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is used on this PC.
    tomllib = None


CODEX_HOME = pathlib.Path(
    os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex"
)
SESSIONS_DIR = CODEX_HOME / "sessions"
CONFIG_PATH = CODEX_HOME / "config.toml"
GLOBAL_STATE_PATH = CODEX_HOME / ".codex-global-state.json"

REFRESH_MS = 5_000
LIVE_REFRESH_SECONDS = 30
LIVE_RETRY_SECONDS = 15
TRACK_MS = 8
DISCOVERY_MS = 500
SIDEBAR_WIDTH = 274
TRANSPARENT_COLOR = "#ff00fe"
OVERLAY_HEIGHT = 102
COLLAPSED_HEIGHT = 24
AUTO_COLLAPSE_SECONDS = 4
LEFT_OFFSET = 8
BOTTOM_OFFSET = 153
PROFILE_WIDTH = 285
PROFILE_HEIGHT = 52
MENU_WIDTH = 300
MENU_HEIGHT = 370
MUTEX_NAME = r"Local\CodexUsageOverlay"


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
if hasattr(user32, "GetDpiForWindow"):
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD
winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
winmm.timeBeginPeriod.restype = wintypes.UINT
winmm.timeEndPeriod.argtypes = [wintypes.UINT]
winmm.timeEndPeriod.restype = wintypes.UINT

if ctypes.sizeof(ctypes.c_void_p) == 8:
    SetWindowLongPtr = user32.SetWindowLongPtrW
else:
    SetWindowLongPtr = user32.SetWindowLongW
SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
SetWindowLongPtr.restype = ctypes.c_void_p

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWLP_HWNDPARENT = -8
GW_OWNER = 4
ERROR_ALREADY_EXISTS = 183
VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_SHOWWINDOW = 0x0040


def enable_dpi_awareness():
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def acquire_singleton():
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None, False
    return handle, kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def process_image_path(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(4096)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def format_reset_date(epoch):
    if not epoch:
        return ""
    reset = dt.datetime.fromtimestamp(float(epoch))
    now = dt.datetime.now()
    if reset.year == now.year:
        return f"{reset.month}月{reset.day}日"
    return f"{reset.year}-{reset.month:02d}-{reset.day:02d}"


def format_window(minutes):
    if not minutes:
        return ""
    minutes = int(minutes)
    if minutes % 10080 == 0:
        return f"{minutes // 10080}周"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}天"
    if minutes % 60 == 0:
        return f"{minutes // 60}小时"
    return f"{minutes}分钟"


def _window_value(window, key):
    if not window:
        return None
    aliases = {
        "used_percent": ("used_percent", "usedPercent"),
        "resets_at": ("resets_at", "resetsAt"),
        "window_minutes": ("window_minutes", "windowDurationMins"),
    }
    for alias in aliases[key]:
        if alias in window:
            return window[alias]
    return None


def _normalize_window(window):
    used_percent = _window_value(window, "used_percent")
    resets_at = _window_value(window, "resets_at")
    window_minutes = _window_value(window, "window_minutes")
    if used_percent is None or resets_at is None:
        return None
    return {
        "used_percent": float(used_percent),
        "resets_at": float(resets_at),
        "window_minutes": int(window_minutes or 0),
    }


def _normalize_codex_bucket(rate_limits, timestamp="", source=""):
    if not rate_limits:
        return None
    limit_id = rate_limits.get("limit_id", rate_limits.get("limitId"))
    if limit_id not in (None, "", "codex"):
        return None

    windows = [
        normalized
        for normalized in (
            _normalize_window(rate_limits.get("primary")),
            _normalize_window(rate_limits.get("secondary")),
        )
        if normalized
    ]
    if not windows:
        return None

    five_hour = next(
        (window for window in windows if window["window_minutes"] == 300), None
    )
    weekly = next(
        (window for window in windows if window["window_minutes"] == 10080), None
    )
    if five_hour is None and len(windows) >= 2:
        five_hour = min(windows, key=lambda window: window["window_minutes"] or 10**9)
    if weekly is None:
        weekly_candidates = [window for window in windows if window is not five_hour]
        if weekly_candidates:
            weekly = max(weekly_candidates, key=lambda window: window["window_minutes"])
        elif windows[0]["window_minutes"] >= 1440:
            weekly = windows[0]

    return {
        "timestamp": timestamp,
        "limit_id": "codex",
        "five_hour": five_hour,
        "weekly": weekly,
        "source": source,
    }


def normalize_live_rate_limits(result):
    if not result:
        return None
    buckets = result.get("rateLimitsByLimitId") or {}
    rate_limits = buckets.get("codex")
    if rate_limits is None:
        candidate = result.get("rateLimits") or {}
        if candidate.get("limitId") in (None, "", "codex"):
            rate_limits = candidate
    data = _normalize_codex_bucket(
        rate_limits,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
        source="account/rateLimits/read",
    )
    if data:
        reserve = buckets.get("base_model_inference") or {}
        data["reserve"] = _normalize_window(reserve.get("primary"))
    return data


def parse_rate_limit_line(line):
    if '"rate_limits"' not in line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    rate_limits = obj.get("rate_limits") or (obj.get("payload") or {}).get("rate_limits")
    return _normalize_codex_bucket(
        rate_limits,
        timestamp=obj.get("timestamp") or "",
        source="session-log",
    )


def iter_tail_lines(path, max_bytes=2_000_000, block_size=64_000):
    size = path.stat().st_size
    read = 0
    leftover = b""
    with path.open("rb") as fh:
        while read < min(size, max_bytes):
            chunk_size = min(block_size, size - read)
            read += chunk_size
            fh.seek(size - read)
            chunk = fh.read(chunk_size) + leftover
            lines = chunk.splitlines()
            if read < size and lines:
                leftover = lines[0]
                lines = lines[1:]
            else:
                leftover = b""
            for raw in reversed(lines):
                yield raw.decode("utf-8", errors="replace")
    if leftover:
        yield leftover.decode("utf-8", errors="replace")


def latest_rate_limit():
    if not SESSIONS_DIR.exists():
        return None
    files = sorted(
        SESSIONS_DIR.rglob("rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    best = None
    # A newly active task can append to an older rollout file.
    for path in files[:24]:
        try:
            for line in iter_tail_lines(path):
                found = parse_rate_limit_line(line)
                if not found:
                    continue
                found["source"] = str(path)
                if best is None or found["timestamp"] > best["timestamp"]:
                    best = found
                break
        except OSError:
            continue
    return best


def remaining_percent(window):
    if not window:
        return None
    return max(0, 100 - int(round(window["used_percent"])))


def rate_limit_snapshot(data):
    if not data:
        return None
    five_hour = data.get("five_hour") or {}
    weekly = data.get("weekly") or {}
    return (
        data.get("timestamp") or "",
        float(five_hour.get("used_percent") or 0),
        float(five_hour.get("resets_at") or 0),
        float(weekly.get("used_percent") or 0),
        float(weekly.get("resets_at") or 0),
    )


def usage_fields(data):
    if not data:
        return "5小时 --", "本周 --"
    if exhausted_window(data):
        remaining = remaining_percent(data.get("reserve"))
        return ("备用 --" if remaining is None else f"备用 {remaining}%", "常规恢复")
    five_hour = remaining_percent(data.get("five_hour"))
    weekly = remaining_percent(data.get("weekly"))
    left = "5小时 --" if five_hour is None else f"5小时 {five_hour}%"
    right = "本周 --" if weekly is None else f"本周 {weekly}%"
    return left, right


def exhausted_window(data):
    # Actual advertised windows determine priority, rather than plan-name guesses.
    for name in ("five_hour", "weekly"):
        window = (data or {}).get(name)
        if window and window["used_percent"] >= 100:
            return window
    return None


def usage_text(data):
    return "\n".join(
        f"{quota}  {reset}"
        for quota, reset in zip(usage_fields(data), usage_reset_fields(data))
    )


def usage_reset_fields(data):
    fields = []
    exhausted = exhausted_window(data)
    windows = [None, exhausted] if exhausted else [(data or {}).get(name) for name in ("five_hour", "weekly")]
    for index, window in enumerate(windows):
        if exhausted and index == 0:
            fields.append("剩余额度")
            continue
        window = window or {}
        epoch = window.get("resets_at")
        if epoch is None:
            fields.append("--")
            continue
        try:
            reset = dt.datetime.fromtimestamp(float(epoch))
            fields.append(reset.strftime("%m/%d %H:%M"))
        except (ValueError, TypeError, OSError, OverflowError):
            fields.append("--")
    return tuple(fields)


def _codex_executable():
    configured = os.environ.get("CODEX_EXECUTABLE")
    candidates = [
        configured,
        shutil.which("codex.exe"),
        shutil.which("codex"),
        str(CODEX_HOME / ".sandbox-bin" / "codex.exe"),
    ]
    return next((candidate for candidate in candidates if candidate and pathlib.Path(candidate).exists()), None)


class LiveRateLimitClient:
    def __init__(self):
        self._data = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._process = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._data.copy() if self._data else None

    def wait_for_data(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.snapshot()
            if data:
                return data
            if self._stop.wait(0.05):
                break
        return None

    def close(self):
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            process.terminate()

    def _send(self, process, message):
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _run(self):
        while not self._stop.is_set():
            executable = _codex_executable()
            if not executable:
                self._stop.wait(LIVE_RETRY_SECONDS)
                continue
            try:
                self._run_session(executable)
            except (OSError, ValueError, BrokenPipeError):
                pass
            finally:
                process = self._process
                self._process = None
                if process and process.poll() is None:
                    process.terminate()
            self._stop.wait(LIVE_RETRY_SECONDS)

    def _run_session(self, executable):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(CODEX_HOME)
        process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=environment,
        )
        self._process = process
        messages = queue.Queue()

        def read_messages():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            messages.put(None)

        threading.Thread(target=read_messages, daemon=True).start()
        self._send(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex-usage-overlay", "version": "2.0"},
                    "capabilities": {"experimentalApi": True},
                },
            },
        )

        initialized = False
        request_id = 1
        next_refresh = 0.0
        while not self._stop.is_set() and process.poll() is None:
            now = time.monotonic()
            if initialized and now >= next_refresh:
                request_id += 1
                self._send(
                    process,
                    {"id": request_id, "method": "account/rateLimits/read"},
                )
                next_refresh = now + LIVE_REFRESH_SECONDS
            try:
                message = messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if message is None:
                break
            if message.get("id") == 1 and "result" in message:
                self._send(process, {"method": "initialized"})
                initialized = True
                next_refresh = 0.0
                continue
            result = message.get("result")
            if result and "rateLimits" in result:
                data = normalize_live_rate_limits(result)
                if data:
                    with self._lock:
                        self._data = data
                continue
            if message.get("method") == "account/rateLimits/updated":
                # Update notifications can be sparse. Refetch the complete pair
                # instead of briefly clearing the window omitted by the event.
                next_refresh = 0.0


def load_theme():
    fallback = {
        "mode": "light",
        "background": "#f7f7f7",
        "foreground": "#202124",
        "secondary": "#777777",
        "accent": "#0169cc",
    }
    if tomllib is None or not CONFIG_PATH.exists():
        return fallback
    try:
        with CONFIG_PATH.open("rb") as fh:
            config = tomllib.load(fh)
        desktop = config.get("desktop") or {}
        mode = str(desktop.get("appearanceTheme") or "light").lower()
        if mode == "system":
            mode = "dark" if _windows_uses_dark_apps() else "light"
        if mode == "dark":
            theme = desktop.get("appearanceDarkChromeTheme") or {}
            return {
                "mode": "dark",
                "background": _blend(theme.get("surface", "#1c1c1c"), "#000000", 0.14),
                "foreground": theme.get("ink", "#fcfcfc"),
                "secondary": _blend(
                    theme.get("ink", "#fcfcfc"),
                    theme.get("surface", "#1c1c1c"),
                    0.48,
                ),
                "accent": theme.get("accent", "#0169cc"),
            }
        theme = desktop.get("appearanceLightChromeTheme") or {}
        return {
            "mode": "light",
            "background": _blend(
                theme.get("surface", "#ffffff"),
                theme.get("ink", "#111111"),
                0.03,
            ),
            "foreground": theme.get("ink", "#111111"),
            "secondary": _blend(
                theme.get("ink", "#111111"),
                theme.get("surface", "#ffffff"),
                0.48,
            ),
            "accent": theme.get("accent", "#0169cc"),
        }
    except (OSError, ValueError, TypeError):
        return fallback


def _windows_uses_dark_apps():
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (OSError, ValueError):
        return False


def _blend(color_a, color_b, amount):
    def rgb(value):
        value = str(value).lstrip("#")
        if len(value) != 6:
            raise ValueError("expected #RRGGBB")
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))

    try:
        first = rgb(color_a)
        second = rgb(color_b)
    except (TypeError, ValueError):
        return str(color_a)
    mixed = tuple(round(a * (1 - amount) + b * amount) for a, b in zip(first, second))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def rect_tuple(rect):
    return rect.left, rect.top, rect.right, rect.bottom


def point_in_box(x, y, left, top, right, bottom):
    return left <= x < right and top <= y < bottom


def menu_state_after_click(menu_open, x, y, rect):
    in_profile = point_in_box(
        x,
        y,
        rect.left,
        rect.bottom - PROFILE_HEIGHT,
        rect.left + PROFILE_WIDTH,
        rect.bottom,
    )
    if in_profile:
        return not menu_open
    if not menu_open:
        return False
    in_menu = point_in_box(
        x,
        y,
        rect.left,
        rect.bottom - MENU_HEIGHT,
        rect.left + MENU_WIDTH,
        rect.bottom - PROFILE_HEIGHT,
    )
    return in_menu


def is_usable_window(hwnd):
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    return rect.right - rect.left > 300 and rect.bottom - rect.top > 300


def get_window_rect(hwnd):
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def find_codex_window():
    candidates = []
    foreground = user32.GetForegroundWindow()

    def callback(hwnd, _):
        if not is_usable_window(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        image_path = process_image_path(pid.value)
        image_lower = image_path.lower()
        if "\\openai.codex_" not in image_lower or not image_lower.endswith("\\chatgpt.exe"):
            return True
        rect = get_window_rect(hwnd)
        if rect:
            candidates.append((hwnd, image_path, rect))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not candidates:
        return None
    for candidate in candidates:
        if candidate[0] == foreground:
            return candidate
    candidates.sort(
        key=lambda candidate: (
            (candidate[2].right - candidate[2].left)
            * (candidate[2].bottom - candidate[2].top)
        ),
        reverse=True,
    )
    return candidates[0]


class Overlay:
    def __init__(self):
        enable_dpi_awareness()
        self.high_resolution_timer = winmm.timeBeginPeriod(1) == 0
        self.root = tk.Tk()
        self.root.title("Codex usage overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-toolwindow", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.bind("<Button-3>", lambda _event: self.root.destroy())
        self.live_client = LiveRateLimitClient()
        self.expanded = False
        self.pointer_was_inside = False
        self.collapse_at = None
        self.hover_blocked = False
        self.hover_opened_at = 0.0
        self.sidebar_width = SIDEBAR_WIDTH
        self.next_width_check = 0.0
        self.width_state_mtime = None
        self.sidebar_dragging = False
        self.width_mouse_was_down = False

        self.content = tk.Frame(self.root)

        self.gauge = tk.Canvas(
            self.content,
            width=18,
            height=18,
            highlightthickness=0,
            borderwidth=0,
        )
        self.gauge.grid(row=0, column=0, rowspan=2, padx=(12, 7))

        self.content.columnconfigure(1, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        self.left_label = tk.Label(
            self.content,
            text="5小时 --",
            font=("Microsoft YaHei UI", 11),
            anchor="w",
            justify="left",
            padx=3,
            pady=2,
        )
        self.left_label.grid(row=0, column=1, sticky="nsew", pady=(6, 3))

        self.right_label = tk.Label(
            self.content,
            text="--",
            font=("Microsoft YaHei UI", 11),
            anchor="e",
            justify="right",
            padx=12,
            pady=2,
        )
        self.right_label.grid(row=0, column=2, sticky="nsew", pady=(6, 3))
        self.week_label = tk.Label(self.content, text="本周 --", font=("Microsoft YaHei UI", 11), anchor="w", padx=3, pady=2)
        self.week_label.grid(row=1, column=1, sticky="nsew", pady=(3, 6))
        self.week_reset_label = tk.Label(self.content, text="--", font=("Microsoft YaHei UI", 11), anchor="e", padx=12, pady=2)
        self.week_reset_label.grid(row=1, column=2, sticky="nsew", pady=(3, 6))
        self.toggle = tk.Canvas(self.root, height=COLLAPSED_HEIGHT, highlightthickness=0, cursor="hand2")
        self.toggle.pack(side="bottom", fill="x")
        self.toggle.bind("<Button-1>", self.toggle_expanded)
        self.toggle.bind("<Configure>", lambda _event: self.draw_toggle())

        self.separator = tk.Frame(self.root, height=1)
        self.separator.pack(side="bottom", fill="x")

        self.root.update_idletasks()
        tk_hwnd = self.root.winfo_id()
        self.overlay_hwnd = user32.GetParent(tk_hwnd) or tk_hwnd
        user32.ShowWindow(self.overlay_hwnd, SW_HIDE)

        self.last_fields = None
        self.last_theme = None
        self.last_remaining = None
        self.last_rate_snapshot = None
        self.owner_hwnd = None
        self.codex_hwnd = None
        self.last_target = None
        self.next_discovery_at = 0.0
        self.is_shown = False
        self.menu_open = False
        self.mouse_left_was_down = False
        self.escape_was_down = False

        self.refresh()
        self.track()

    def draw_toggle(self):
        color = (getattr(self, "last_theme", None) or {}).get("secondary", "#777777")
        center = self.toggle.winfo_width() / 2
        points = (center - 5, 9, center, 14, center + 5, 9) if self.expanded else (center - 5, 14, center, 9, center + 5, 14)
        self.toggle.delete("all")
        self.toggle.create_line(*points, fill=color, width=2, capstyle="round", joinstyle="round")

    def set_expanded(self, expanded):
        if self.expanded == expanded:
            return
        self.expanded = expanded
        if expanded:
            self.content.pack(side="top", fill="both", expand=True)
        else:
            self.content.pack_forget()
        self.collapse_at = None
        self.last_target = None
        self.apply_surface()
        self.draw_toggle()

    def apply_surface(self):
        theme = getattr(self, "last_theme", None) or load_theme()
        background = theme["background"] if self.expanded else TRANSPARENT_COLOR
        self.root.configure(bg=background)
        self.toggle.configure(bg=background)
        if self.expanded:
            self.separator.pack(side="bottom", fill="x")
        else:
            self.separator.pack_forget()

    def toggle_expanded(self, _event=None):
        # A click immediately following hover expansion still means "open".
        if self.expanded and time.monotonic() - self.hover_opened_at < 0.35:
            return
        self.hover_blocked = self.expanded
        self.set_expanded(not self.expanded)

    def update_hover(self):
        point = POINT()
        rect = get_window_rect(self.overlay_hwnd)
        if not rect or not user32.GetCursorPos(ctypes.byref(point)):
            return
        inside = point_in_box(point.x, point.y, rect.left, rect.top, rect.right, rect.bottom)
        now = time.monotonic()
        if inside:
            self.collapse_at = None
            if not self.expanded and not self.hover_blocked:
                self.hover_opened_at = now
                self.set_expanded(True)
        else:
            self.hover_blocked = False
            if self.expanded:
                if self.collapse_at is None:
                    self.collapse_at = now + AUTO_COLLAPSE_SECONDS
                elif now >= self.collapse_at:
                    self.set_expanded(False)
        self.pointer_was_inside = inside

    def refresh_style(self):
        theme = load_theme()
        if theme == self.last_theme:
            return
        background = theme["background"]
        self.root.configure(bg=background)
        self.content.configure(bg=background)
        self.gauge.configure(bg=background)
        self.left_label.configure(bg=background, fg=theme["foreground"])
        self.right_label.configure(bg=background, fg=theme["secondary"])
        self.week_label.configure(bg=background, fg=theme["foreground"])
        self.week_reset_label.configure(bg=background, fg=theme["secondary"])
        self.toggle.configure(bg=background)
        self.separator.configure(bg=_blend(background, theme["foreground"], 0.10))
        self.last_theme = theme
        self.apply_surface()
        self.draw_gauge()
        self.draw_toggle()

    def draw_gauge(self):
        if not self.last_theme:
            return
        secondary = self.last_theme["secondary"]
        accent = self.last_theme["accent"]
        remaining = 0 if self.last_remaining is None else self.last_remaining
        angle = 210 - 240 * (remaining / 100)
        radians = angle * 3.141592653589793 / 180
        x2 = 9 + 5 * math.cos(radians)
        y2 = 9 - 5 * math.sin(radians)
        self.gauge.delete("all")
        self.gauge.create_arc(
            3,
            3,
            15,
            15,
            start=210,
            extent=-240,
            style="arc",
            width=2,
            outline=secondary,
        )
        self.gauge.create_line(9, 9, x2, y2, width=2, fill=accent)
        self.gauge.create_oval(7.5, 7.5, 10.5, 10.5, fill=accent, outline=accent)

    def refresh(self):
        self.refresh_style()
        data = self.live_client.snapshot() or latest_rate_limit()
        snapshot = rate_limit_snapshot(data)
        fields = usage_fields(data) + usage_reset_fields(data)
        remaining_values = [
            remaining_percent(data.get(name))
            for name in ("five_hour", "weekly")
            if data and data.get(name)
        ]
        remaining = min(remaining_values) if remaining_values else None
        if exhausted_window(data):
            remaining = remaining_percent((data or {}).get("reserve"))
        if snapshot != self.last_rate_snapshot or fields != self.last_fields:
            self.left_label.configure(text=fields[0])
            self.week_label.configure(text=fields[1])
            self.right_label.configure(text=fields[2])
            self.week_reset_label.configure(text=fields[3])
            self.last_fields = fields
            self.last_remaining = remaining
            self.draw_gauge()
            self.last_rate_snapshot = snapshot
        self.root.after(REFRESH_MS, self.refresh)

    def set_owner(self, hwnd):
        if self.owner_hwnd == hwnd:
            return
        SetWindowLongPtr(self.overlay_hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(hwnd))
        self.owner_hwnd = hwnd

    def current_codex_window(self):
        if is_usable_window(self.codex_hwnd):
            rect = get_window_rect(self.codex_hwnd)
            if rect:
                return self.codex_hwnd, rect

        self.codex_hwnd = None
        now = time.monotonic()
        if now < self.next_discovery_at:
            return None
        self.next_discovery_at = now + DISCOVERY_MS / 1000
        candidate = find_codex_window()
        if not candidate:
            return None
        self.codex_hwnd = candidate[0]
        return candidate[0], candidate[2]

    def update_menu_state(self, rect):
        left_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        escape_down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)

        if escape_down and not self.escape_was_down:
            self.menu_open = False

        if left_down and not self.mouse_left_was_down:
            point = POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                self.menu_open = menu_state_after_click(
                    self.menu_open,
                    point.x,
                    point.y,
                    rect,
                )

        self.mouse_left_was_down = left_down
        self.escape_was_down = escape_down

    def hide(self):
        if self.is_shown:
            user32.ShowWindow(self.overlay_hwnd, SW_HIDE)
            self.is_shown = False

    def codex_is_foreground(self):
        foreground = user32.GetForegroundWindow()
        return foreground in (self.codex_hwnd, self.overlay_hwnd) or (
            foreground and user32.GetWindow(foreground, GW_OWNER) == self.codex_hwnd
        )

    def move_and_show(self, rect):
        self.set_owner(self.codex_hwnd)
        self.update_sidebar_width(rect)
        height = OVERLAY_HEIGHT if self.expanded else COLLAPSED_HEIGHT
        bottom_gap = BOTTOM_OFFSET - OVERLAY_HEIGHT
        target = (rect.left + LEFT_OFFSET, rect.bottom - bottom_gap - height, self.sidebar_width, height)
        if target != self.last_target or not self.is_shown:
            user32.SetWindowPos(
                self.overlay_hwnd,
                HWND_TOPMOST,
                target[0],
                target[1],
                target[2],
                target[3],
                SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW,
            )
            self.last_target = target
        if not self.is_shown:
            user32.ShowWindow(self.overlay_hwnd, SW_SHOWNOACTIVATE)
            self.is_shown = True

    def update_sidebar_width(self, rect):
        now = time.monotonic()
        scale = 1.0
        if hasattr(user32, "GetDpiForWindow"):
            scale = (user32.GetDpiForWindow(self.codex_hwnd) or 96) / 96
        if now >= self.next_width_check and not self.sidebar_dragging:
            self.next_width_check = now + 0.1
            try:
                modified = GLOBAL_STATE_PATH.stat().st_mtime_ns
                if modified != self.width_state_mtime:
                    state = json.loads(GLOBAL_STATE_PATH.read_text(encoding="utf-8"))
                    width = (state.get("electron-persisted-atom-state") or {}).get("sidebar-width")
                    if isinstance(width, (int, float)) and 160 <= width <= 800:
                        self.sidebar_width = round(width * scale)
                    self.width_state_mtime = modified
            except (OSError, ValueError, TypeError):
                pass
        point = POINT()
        down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if user32.GetCursorPos(ctypes.byref(point)):
            left = rect.left + LEFT_OFFSET
            if down and not self.width_mouse_was_down:
                self.sidebar_dragging = (
                    abs(point.x - (left + self.sidebar_width)) <= 6 * scale
                    and rect.top + 80 * scale < point.y < rect.bottom - 160 * scale
                )
            if self.sidebar_dragging and down:
                self.sidebar_width = round(max(160 * scale, min(800 * scale, point.x - left)))
            if not down and self.sidebar_dragging:
                self.sidebar_dragging = False
                self.width_state_mtime = None
                self.next_width_check = now + 0.25
        self.width_mouse_was_down = down

    def track(self):
        current = self.current_codex_window()
        if not current:
            self.menu_open = False
            self.last_target = None
            self.hide()
            self.root.after(TRACK_MS, self.track)
            return

        self.codex_hwnd, rect = current
        self.update_menu_state(rect)
        if self.menu_open or not self.codex_is_foreground():
            self.set_expanded(False)
            self.hide()
        else:
            self.move_and_show(rect)
            self.update_hover()
        self.root.after(TRACK_MS, self.track)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.live_client.close()
            if self.high_resolution_timer:
                winmm.timeEndPeriod(1)


def print_once():
    client = LiveRateLimitClient()
    data = client.wait_for_data() or latest_rate_limit()
    client.close()
    print(usage_text(data))
    if data:
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--once" in sys.argv:
        print_once()
    else:
        mutex, is_first = acquire_singleton()
        if not is_first:
            sys.exit(0)
        Overlay().run()
