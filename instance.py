"""
instance.py — один экземпляр приложения, настройки и связь между экземплярами.

Второй запуск (например, двойной клик по mp3) не открывает новое окно:
он находит работающий экземпляр по %APPDATA%\\Music\\instance.json и
отправляет ему список файлов по локальному HTTP (с токеном).
"""

import ctypes
import json
import os
import time
import urllib.error
import urllib.request
from ctypes import wintypes

_MUTEX_NAME = "Local\\MusicPlayer.SingleInstance.v1"
_ERROR_ALREADY_EXISTS = 183

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_u32.AllowSetForegroundWindow.restype = wintypes.BOOL

_mutex = None  # держим хэндл до конца процесса


# ---------- папка данных / настройки ----------
def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "Music")
    os.makedirs(path, exist_ok=True)
    return path


def _info_path() -> str:
    return os.path.join(data_dir(), "instance.json")


def _settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


_DEFAULTS = {"close_to_tray": True, "window": None}


def load_settings() -> dict:
    data = dict(_DEFAULTS)
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except (OSError, ValueError):
        pass
    return data


def save_settings(settings: dict) -> None:
    try:
        tmp = _settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _settings_path())
    except OSError:
        pass


# ---------- single instance ----------
def acquire() -> bool:
    """True — мы первый экземпляр. False — приложение уже запущено."""
    global _mutex
    _mutex = _k32.CreateMutexW(None, False, _MUTEX_NAME)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def write_info(port: int, token: str) -> None:
    try:
        with open(_info_path(), "w", encoding="utf-8") as f:
            json.dump({"port": port, "token": token, "pid": os.getpid()}, f)
    except OSError:
        pass


def clear_info() -> None:
    try:
        os.remove(_info_path())
    except OSError:
        pass


def _read_info():
    try:
        with open(_info_path(), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def send_to_running(paths, mode="play", timeout: float = 8.0) -> bool:
    """Передать файлы работающему экземпляру (пустой список = просто показать окно)."""
    deadline = time.time() + timeout
    body = json.dumps({"paths": list(paths), "mode": mode}).encode("utf-8")

    while time.time() < deadline:
        info = _read_info()
        if info and "port" in info and "token" in info:
            try:
                _u32.AllowSetForegroundWindow(int(info.get("pid", 0)))
            except Exception:
                pass
            req = urllib.request.Request(
                f"http://127.0.0.1:{int(info['port'])}/api/_instance/open",
                data=body,
                headers={"Content-Type": "application/json",
                         "X-Token": str(info["token"])},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return resp.status == 200
            except (urllib.error.URLError, OSError, ValueError):
                pass
        time.sleep(0.3)
    return False