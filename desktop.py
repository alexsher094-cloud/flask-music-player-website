"""
desktop.py — Windows-версия плеера (pywebview + WebView2).

Кастомный титлбар, интеграция с Windows (иконка, медиаклавиши, taskbar,
jumplist, mini-режим) — см. windows_integration.py и file_assoc.py.
"""

import argparse
import atexit
import ctypes
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
from ctypes import wintypes

os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--autoplay-policy=no-user-gesture-required")

import webview
from flask import abort, jsonify, request

import file_assoc
import instance
from app import (ALLOWED_EXT, EXTERNAL_TRACKS, app, extract_cover,
                 extract_metadata, extract_replaygain, track_id_for, lib)
from presence import DiscordPresence
from windows_integration import WindowsIntegration, ensure_icon_file


# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
WINDOW_TITLE = "Music"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_MIN   = (460, 500)
WINDOW_BG    = "#0a0a0c"
PORT_START   = 5000
PORT_END     = 5100


# ============================================================
# WIN32
# ============================================================
user32  = ctypes.WinDLL("user32",  use_last_error=True)
dwmapi  = ctypes.WinDLL("dwmapi",  use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

WM_SIZE          = 0x0005
WM_GETMINMAXINFO = 0x0024
WM_ACTIVATE      = 0x0006
WM_NCCALCSIZE    = 0x0083
WM_NCACTIVATE    = 0x0086
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND    = 0x0112
WM_HOTKEY        = 0x0312
WM_COMMAND       = 0x0111
WM_APP_NCACTION  = 0x8001

SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_RESTORE  = 0xF120

HTCAPTION = 2
HT_RESIZE_CODES = range(10, 18)

GWL_STYLE      = -16
GWLP_WNDPROC   = -4
WS_THICKFRAME  = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU     = 0x00080000

SWP_NOSIZE       = 0x0001
SWP_NOMOVE       = 0x0002
SWP_NOZORDER     = 0x0004
SWP_NOACTIVATE   = 0x0010
SWP_FRAMECHANGED = 0x0020

MONITOR_DEFAULTTONEAREST = 2
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37
SM_SWAPBUTTON  = 23
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

ABM_GETAUTOHIDEBAREX = 0x0B


class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int),
                ("t", ctypes.c_int), ("b", ctypes.c_int)]


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype  = LRESULT
user32.DefWindowProcW.argtypes  = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype   = LRESULT
user32.PostMessageW.argtypes    = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype     = wintypes.BOOL
user32.ReleaseCapture.argtypes  = []
user32.ReleaseCapture.restype   = wintypes.BOOL
user32.IsZoomed.argtypes        = [wintypes.HWND]
user32.IsZoomed.restype         = wintypes.BOOL
user32.IsIconic.argtypes        = [wintypes.HWND]
user32.IsIconic.restype         = wintypes.BOOL
user32.GetCursorPos.argtypes    = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype     = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype  = ctypes.c_short
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype  = ctypes.c_int
user32.GetDoubleClickTime.argtypes = []
user32.GetDoubleClickTime.restype  = wintypes.UINT
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype  = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes   = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype    = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype  = wintypes.BOOL

_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype  = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype  = ctypes.c_ssize_t

dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
dwmapi.DwmExtendFrameIntoClientArea.restype  = ctypes.c_long
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype  = ctypes.c_long

shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype  = ctypes.c_size_t


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT), ("ptMinTrackSize", wintypes.POINT),
                ("ptMaxTrackSize", wintypes.POINT)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT)]


user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype  = wintypes.BOOL
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPlacement.restype  = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype  = wintypes.BOOL
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
shell32.SetCurrentProcessExplicitAppUserModelID.restype  = ctypes.c_long

APP_USER_MODEL_ID = "Music.Player"


def _log(msg: str) -> None:
    print(f"[WIN32] {msg}", file=sys.stderr)


def _monitor_rects(hwnd):
    """(rcMonitor, rcWork) монитора, на котором находится окно."""
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    return mi.rcMonitor, mi.rcWork


def _maximized_client_rect(hwnd):
    rects = _monitor_rects(hwnd)
    if not rects:
        return None
    rc_monitor, rc_work = rects

    left, top = rc_work.left, rc_work.top
    right, bottom = rc_work.right, rc_work.bottom

    try:
        for edge in range(4):
            abd = APPBARDATA()
            abd.cbSize = ctypes.sizeof(APPBARDATA)
            abd.hWnd = hwnd
            abd.uEdge = edge
            abd.rc = rc_monitor
            if shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, ctypes.byref(abd)):
                if edge == 0:   left   += 1
                elif edge == 1: top    += 1
                elif edge == 2: right  -= 1
                else:           bottom -= 1
    except Exception:
        pass

    return left, top, right, bottom


# ============================================================
# ПОРТ / FLASK
# ============================================================
def find_free_port(start: int, end: int) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"Нет свободного порта в диапазоне {start}-{end}")


def wait_for_server(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def run_flask(port: int) -> None:
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


# ============================================================
# JS API  +  подмена WndProc
# ============================================================
class WindowApi:
    def __init__(self):
        self._window = None
        self._hwnd = None
        self._old_proc = None
        self._wndproc_ref = None
        self._installed = False
        self._lock = threading.Lock()

        self._maximized = False
        self._active = True
        self._last_click = (0.0, (0, 0))

        self._close_cb = None
        self._on_installed = None

        self._presence = None
        self._discord_status = None
        self._discord_set = None

        self._integration = None
        self.eval = self._eval_js_safe

    def bind(self, window):
        self._window = window
        window.events.loaded += self._on_loaded

    def _eval_js_safe(self, js: str):
        def run():
            try:
                if self._window:
                    self._window.evaluate_js(js)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_loaded(self):
        with self._lock:
            if self._installed:
                return
            try:
                form = self._window.native
                try:
                    from System import Action
                    if form.InvokeRequired:
                        form.Invoke(Action(self._install_ui))
                    else:
                        self._install_ui()
                except ImportError:
                    self._install_ui()
            except Exception as e:
                _log(f"не удалось установить оконный хук: {e}")

    def _install_ui(self):
        hwnd = int(self._window.native.Handle.ToInt64())

        style = _get_long(hwnd, GWL_STYLE)
        _set_long(hwnd, GWL_STYLE,
                  style | WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU)

        self._hwnd = hwnd
        self._wndproc_ref = WNDPROC(self._wndproc)
        new_proc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p).value
        self._old_proc = _set_long(hwnd, GWLP_WNDPROC, new_proc)

        try:
            m = MARGINS(1, 1, 1, 1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            pref = ctypes.c_int(DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception as e:
            _log(f"DWM: {e}")

        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

        self._maximized = bool(user32.IsZoomed(hwnd))
        self._installed = True

        try:
            self._integration = WindowsIntegration(self)
            self._integration.setup(hwnd)
        except Exception as e:
            _log(f"WindowsIntegration: {e}")

        _log(f"HWND={hwnd}, хук установлен")
        self._allow_media_permission()

        if self._on_installed:
            try:
                self._on_installed()
            except Exception as e:
                _log(f"on_installed: {e}")

    def _allow_media_permission(self):
        """
        Разрешает странице на 127.0.0.1 доступ к «микрофону». Он нужен только для того,
        чтобы Chromium показал НАЗВАНИЯ устройств вывода в настройках (кнопка
        «Показать названия»): поток сразу останавливается, звук не записывается.
        Без этого WebView2 показывал бы системный запрос разрешения.
        """
        try:
            wv = getattr(getattr(self._window.native, "browser", None), "web_view", None)
            core = getattr(wv, "CoreWebView2", None)
            if core is None:
                return
            from Microsoft.Web.WebView2.Core import (CoreWebView2PermissionKind,
                                                     CoreWebView2PermissionState)

            def on_permission(sender, args):
                try:
                    if (args.PermissionKind == CoreWebView2PermissionKind.Microphone
                            and str(args.Uri).startswith("http://127.0.0.1")):
                        args.State = CoreWebView2PermissionState.Allow
                except Exception as e:
                    _log(f"permission: {e}")

            core.PermissionRequested += on_permission
            self._permission_handler = on_permission   # держим ссылку, чтобы не собрал GC
        except Exception as e:
            _log(f"PermissionRequested: {e}")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        old = self._old_proc
        if not old:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        try:
            if msg == WM_NCCALCSIZE:
                self._on_nccalcsize(hwnd, wparam, lparam)
                return 0

            if msg == WM_NCACTIVATE:
                return user32.CallWindowProcW(old, hwnd, msg, wparam, -1)

            if msg == WM_APP_NCACTION:
                self._on_nc_action(hwnd, int(wparam))
                return 0

            if msg == WM_HOTKEY:
                if self._integration:
                    self._integration.handle_hotkey(int(wparam))
                return 0

            if msg == WM_COMMAND:
                if self._integration:
                    self._integration.handle_command(int(wparam), int(lparam))

            if msg == WM_GETMINMAXINFO:
                # Минимальный размер окна задаёт pywebview (WINDOW_MIN = 460x500) — для
                # мини-режима (460x140) его нужно переопределить, иначе окно не уменьшится.
                res = user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)
                if lparam:
                    mmi = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                    limits = self._integration.mini_limits() if self._integration else None
                    if limits:
                        min_w, height = limits
                        mmi.ptMinTrackSize.x = min_w
                        mmi.ptMinTrackSize.y = height
                        mmi.ptMaxTrackSize.y = height      # высота мини-окна фиксирована
                    else:
                        # Окно без рамки Windows разворачивает на весь монитор, а клиентская
                        # область мы задаём равной рабочей (WM_NCCALCSIZE) — внизу оставалась
                        # чёрная полоса высотой с панель задач. Ограничиваем maximize рабочей
                        # областью, чтобы rect окна и клиентская область совпадали.
                        rects = _monitor_rects(hwnd)
                        if rects:
                            rc_monitor, rc_work = rects
                            mmi.ptMaxPosition.x = rc_work.left - rc_monitor.left
                            mmi.ptMaxPosition.y = rc_work.top - rc_monitor.top
                            mmi.ptMaxSize.x = rc_work.right - rc_work.left
                            mmi.ptMaxSize.y = rc_work.bottom - rc_work.top
                return res

            if msg == WM_SIZE:
                res = user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)
                zoomed = bool(user32.IsZoomed(hwnd))
                if zoomed != self._maximized:
                    self._maximized = zoomed
                    self._push_state()
                return res

            if msg == WM_ACTIVATE:
                res = user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)
                active = (int(wparam) & 0xFFFF) != 0
                if active != self._active:
                    self._active = active
                    self._push_state()
                return res
        except Exception as e:
            _log(f"wndproc({msg:#x}): {e}")

        return user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)

    def _on_nccalcsize(self, hwnd, wparam, lparam):
        if not lparam:
            return
        if wparam:
            rect = ctypes.cast(lparam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents.rgrc[0]
        else:
            rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents

        if user32.IsZoomed(hwnd) and not user32.IsIconic(hwnd):
            area = _maximized_client_rect(hwnd)
            if area:
                rect.left, rect.top, rect.right, rect.bottom = area

    def _on_nc_action(self, hwnd, hit):
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return

        if hit == HTCAPTION:
            in_mini = bool(self._integration and self._integration.is_mini())
            if not in_mini and self._is_double_click(pt):
                self._post_syscommand(
                    SC_RESTORE if user32.IsZoomed(hwnd) else SC_MAXIMIZE)
                return
        elif hit in HT_RESIZE_CODES:
            if user32.IsZoomed(hwnd):
                return
        else:
            return

        vk = VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        if not (user32.GetAsyncKeyState(vk) & 0x8000):
            return

        user32.ReleaseCapture()
        lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, hit, lparam)

    def _is_double_click(self, pt) -> bool:
        now = time.monotonic()
        last_t, (lx, ly) = self._last_click
        slop_x = max(user32.GetSystemMetrics(SM_CXDOUBLECLK) // 2, 4)
        slop_y = max(user32.GetSystemMetrics(SM_CYDOUBLECLK) // 2, 4)
        if ((now - last_t) * 1000 <= user32.GetDoubleClickTime()
                and abs(pt.x - lx) <= slop_x and abs(pt.y - ly) <= slop_y):
            self._last_click = (0.0, (0, 0))
            return True
        self._last_click = (now, (pt.x, pt.y))
        return False

    def _post_syscommand(self, cmd: int):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_SYSCOMMAND, cmd, 0)

    def _push_state(self):
        def run():
            try:
                hwnd = self._hwnd
                if not hwnd:
                    return
                zoomed = "true" if user32.IsZoomed(hwnd) else "false"
                active = "true" if self._active else "false"
                self._window.evaluate_js(
                    f"window.__onWindowState && window.__onWindowState({zoomed}, {active})")
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    # ---------- публичное API ----------
    def minimize(self):
        self._post_syscommand(SC_MINIMIZE)

    def toggle_maximize(self):
        if self._hwnd:
            self._post_syscommand(
                SC_RESTORE if user32.IsZoomed(self._hwnd) else SC_MAXIMIZE)

    def is_maximized(self) -> bool:
        return bool(self._hwnd and user32.IsZoomed(self._hwnd))

    def close(self):
        if self._close_cb:
            self._close_cb()
        elif self._window:
            self._window.destroy()

    def start_drag(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_NCACTION, HTCAPTION, 0)

    def start_resize(self, edge: int):
        try:
            edge = int(edge)
        except (TypeError, ValueError):
            return
        if self._hwnd and edge in HT_RESIZE_CODES:
            user32.PostMessageW(self._hwnd, WM_APP_NCACTION, edge, 0)

    def set_background(self, css_color: str) -> bool:
        rgb = _parse_css_color(css_color)
        if not rgb or not self._window:
            return False
        try:
            from System import Action
            from System.Drawing import Color

            form = self._window.native

            def apply():
                color = Color.FromArgb(255, *rgb)
                form.BackColor = color
                try:
                    wv = getattr(getattr(form, "browser", None), "web_view", None)
                    if wv is not None:
                        wv.DefaultBackgroundColor = color
                except Exception:
                    pass

            if form.InvokeRequired:
                form.Invoke(Action(apply))
            else:
                apply()
            return True
        except Exception as e:
            _log(f"set_background: {e}")
            return False

    # ---------- Windows-интеграция ----------
    def set_playback_state(self, is_playing: bool) -> bool:
        if self._integration:
            self._integration.set_playback_state(bool(is_playing))
            return True
        return False

    def update_taskbar_progress(self, position, duration, is_playing: bool) -> bool:
        if self._integration:
            self._integration.update_progress(position, duration, bool(is_playing))
            return True
        return False

    def clear_taskbar_progress(self) -> bool:
        if self._integration:
            self._integration.clear_progress()
            return True
        return False

    # ---------- Discord Rich Presence ----------
    def update_presence(self, data) -> bool:
        if self._presence is not None and isinstance(data, dict):
            self._presence.update(data)
        return True

    def clear_presence(self) -> bool:
        if self._presence is not None:
            self._presence.clear()
        return True

    def get_discord_config(self) -> dict:
        cb = self._discord_status
        return cb() if cb else {}

    def set_discord_config(self, enabled: bool, client_id: str = "") -> dict:
        cb = self._discord_set
        return cb(bool(enabled), str(client_id or "")) if cb else {}

    def update_recent_tracks(self, paths) -> bool:
        if self._integration:
            self._integration.update_recent_tracks(list(paths or []))
            return True
        return False

    def update_recent_tracks_by_ids(self, ids) -> bool:
        if not self._integration:
            return False
        try:
            from app import find_track_path
        except Exception:
            return False
        paths = []
        for tid in (ids or []):
            p = find_track_path(str(tid))
            if p:
                paths.append(p)
        self._integration.update_recent_tracks(paths)
        return True

    def toggle_mini_mode(self) -> bool:
        if self._integration:
            self._integration.toggle_mini_mode()
            return True
        return False

    def set_mini_mode(self, enabled: bool) -> bool:
        if self._integration:
            self._integration.set_mini_mode(bool(enabled))
            return True
        return False

    def is_mini_mode(self) -> bool:
        return bool(self._integration and self._integration.is_mini())


def _parse_css_color(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    m = re.match(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", value)
    if m:
        return tuple(max(0, min(255, int(x))) for x in m.groups())
    m = re.match(r"#([0-9a-fA-F]{6})$", value)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return None


# ============================================================
# ОТКРЫТИЕ ФАЙЛОВ ИЗВНЕ
# ============================================================
def resolve_file(path: str):
    try:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return None
        if os.path.splitext(path)[1].lower() not in ALLOWED_EXT:
            return None

        track_id = "x" + track_id_for(path)[:15]
        EXTERNAL_TRACKS[track_id] = path

        meta = extract_metadata(path)
        cover, _ = extract_cover(path)
        return {
            "id": track_id,
            "file": os.path.basename(path),
            "title": meta["title"],
            "artist": meta["artist"],
            "album": meta["album"],
            "duration": meta["duration"],
            "date": meta["date"],
            "mtime": meta["mtime"],
            "hasCover": cover is not None,
            "rg": extract_replaygain(path),
        }
    except Exception as e:
        _log(f"resolve_file({path!r}): {e}")
        return None


# ============================================================
# КОНТРОЛЛЕР
# ============================================================
class AppController:
    def __init__(self):
        self.token = ""
        self.window = None
        self.api = None
        self.tray = None
        self.tray_ok = False
        self.quitting = False
        self.settings = instance.load_settings()

        self.start_hidden = False
        self._last_play = 0.0
        self._pending_open = None
        self._pending_maximize = False
        self.presence = None

    # ---------- Discord Rich Presence ----------
    def init_presence(self):
        try:
            self.presence = DiscordPresence(
                client_id=(os.environ.get("MUSIC_DISCORD_CLIENT_ID")
                           or self.settings.get("discord_client_id", "")),
                enabled=bool(self.settings.get("discord_presence", True)),
                large_image=self.settings.get("discord_large_image", ""),
            )
            self.presence.start()
        except Exception as e:
            _log(f"discord presence: {e}")
            self.presence = None

    def discord_status(self) -> dict:
        if self.presence is None:
            return {
                "available": False,
                "enabled": bool(self.settings.get("discord_presence", True)),
                "client_id": self.settings.get("discord_client_id", ""),
                "connected": False,
                "error": "pypresence не установлен",
            }
        status = self.presence.status()
        status["enabled"] = bool(self.settings.get("discord_presence", True))
        status["client_id"] = (self.settings.get("discord_client_id", "")
                               or os.environ.get("MUSIC_DISCORD_CLIENT_ID", ""))
        return status

    def set_discord_config(self, enabled: bool, client_id: str) -> dict:
        cid = "".join(str(client_id or "").split())
        self.settings["discord_presence"] = bool(enabled)
        self.settings["discord_client_id"] = cid
        instance.save_settings(self.settings)
        if self.presence is not None:
            self.presence.configure(client_id=cid, enabled=bool(enabled))
        return self.discord_status()

    def eval(self, js: str):
        def run():
            try:
                self.window.evaluate_js(js)
            except Exception:
                pass
        if self.window:
            threading.Thread(target=run, daemon=True).start()

    def click(self, element_id: str):
        self.eval("(function(){var e=document.getElementById(%s);if(e)e.click();})()"
                  % json.dumps(element_id))

    def _call_js_when_ready(self, fn: str, *args):
        def run():
            try:
                for _ in range(40):
                    if self.window.evaluate_js("typeof window.%s" % fn) == "function":
                        break
                    time.sleep(0.25)
                else:
                    return
                self.window.evaluate_js(
                    "window.%s(%s)" % (fn, ",".join(json.dumps(a) for a in args)))
            except Exception as e:
                _log(f"{fn}: {e}")
        threading.Thread(target=run, daemon=True).start()

    def is_mini(self) -> bool:
        return bool(self.api and self.api.is_mini_mode())

    def toggle_mini(self):
        """Мини-режим из меню трея (окно может быть спрятано — тогда сначала показываем)."""
        integ = getattr(self.api, "_integration", None)
        if not integ:
            return
        if not integ.is_mini():
            self.show_window()
        integ.toggle_mini_mode()

    def show_window(self):
        if not self.window:
            return
        try:
            self.window.show()
            hwnd = self.api._hwnd if self.api else None
            if hwnd:
                if user32.IsIconic(hwnd):
                    user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
                if self._pending_maximize:
                    self._pending_maximize = False
                    user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
                user32.SetForegroundWindow(hwnd)
        except Exception as e:
            _log(f"show_window: {e}")

    def save_geometry(self):
        hwnd = self.api._hwnd if self.api else None
        if not hwnd:
            return

        # В мини-режиме сохраняем не крошечное окно, а обычное — каким оно было до входа.
        integ = getattr(self.api, "_integration", None)
        if integ is not None and integ.is_mini():
            integ.mini.remember()
            normal = integ.mini.normal_placement()
            if normal:
                l, t, r, b, mx = normal
                self.settings["window"] = {"l": l, "t": t, "r": r, "b": b, "max": bool(mx)}
                instance.save_settings(self.settings)
            return

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            r = wp.rcNormalPosition
            self.settings["window"] = {
                "l": r.left, "t": r.top, "r": r.right, "b": r.bottom,
                "max": bool(user32.IsZoomed(hwnd)),
            }
            instance.save_settings(self.settings)

    def restore_geometry(self):
        g = self.settings.get("window")
        hwnd = self.api._hwnd if self.api else None
        if not hwnd or not isinstance(g, dict):
            return
        try:
            l, t, r, b = (int(g[k]) for k in ("l", "t", "r", "b"))
        except (KeyError, TypeError, ValueError):
            return
        if r - l < 300 or b - t < 300:
            return

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return
        wp.rcNormalPosition = wintypes.RECT(l, t, r, b)
        if self.start_hidden:
            wp.showCmd = 0
            self._pending_maximize = bool(g.get("max"))
        else:
            wp.showCmd = 3 if g.get("max") else 1
        user32.SetWindowPlacement(hwnd, ctypes.byref(wp))

    def _hide_to_tray(self) -> bool:
        return self.tray_ok and bool(self.settings.get("close_to_tray", True))

    def request_close(self):
        if self.quitting or not self._hide_to_tray():
            self.quit()
            return
        self.save_geometry()
        self.window.hide()

    def on_closing(self):
        if self.quitting:
            return True
        self.save_geometry()
        if self._hide_to_tray():
            threading.Thread(target=self.window.hide, daemon=True).start()
            return False
        return True

    def quit(self):
        if self.quitting:
            return
        self.quitting = True

        # 1) Windows-интеграция
        try:
            if self.api and getattr(self.api, "_integration", None):
                self.api._integration.cleanup()
        except Exception:
            pass

        # 2) библиотека: остановить watcher и корректно закрыть SQLite (WAL)
        try:
            lib.stop()
        except Exception:
            pass

        # 3) Discord: убрать активность и закрыть IPC-канал
        try:
            if self.presence is not None:
                self.presence.shutdown()
        except Exception:
            pass

        # 4) состояние окна
        try:
            self.save_geometry()
        except Exception:
            pass
        instance.clear_info()

        # 5) трей: остановить ровно один раз и обнулить ссылку
        tray = self.tray
        self.tray = None
        if tray:
            try:
                tray.stop()
            except Exception:
                pass

        # 6) окно — в отдельном потоке, чтобы не блокировать текущий
        if self.window:
            threading.Thread(target=self.window.destroy, daemon=True).start()

    def open_paths(self, paths, mode: str = "play"):
        items = [t for t in (resolve_file(p) for p in paths) if t]
        if not items:
            self.show_window()
            return

        now = time.monotonic()
        if mode == "play":
            if self._last_play and now - self._last_play < 1.5:
                mode = "enqueue"
            self._last_play = now
        if mode == "play":
            self.show_window()

        self._call_js_when_ready("__openExternal", items, mode)

    def on_loaded(self):
        if self._pending_open:
            paths, mode = self._pending_open
            self._pending_open = None
            threading.Thread(target=self.open_paths, args=(paths, mode), daemon=True).start()


controller = AppController()


@app.route("/api/_instance/open", methods=["POST"])
def _api_instance_open():
    if not controller.token or request.headers.get("X-Token") != controller.token:
        abort(403)
    data = request.get_json(silent=True) or {}
    paths = [p for p in (data.get("paths") or []) if isinstance(p, str)]
    mode = "enqueue" if data.get("mode") == "enqueue" else "play"
    threading.Thread(target=controller.open_paths, args=(paths, mode), daemon=True).start()
    return jsonify(ok=True)


# ============================================================
# ГЛАВНОЕ
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(description="Music")
    p.add_argument("files", nargs="*", help="аудиофайлы для воспроизведения")
    p.add_argument("--enqueue", action="store_true", help="добавить в очередь")
    p.add_argument("--minimized", action="store_true", help="стартовать скрытым в трей")
    p.add_argument("--register", action="store_true", help="зарегистрировать ассоциации и выйти")
    p.add_argument("--unregister", action="store_true", help="удалить ассоциации и выйти")
    return p.parse_args()


def _install_signal_handlers() -> None:
    """SIGINT (Ctrl+C) / SIGTERM — корректный выход через controller.quit()."""
    def handler(signum, frame):
        try:
            controller.quit()
        except Exception:
            pass
    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except Exception:
                pass


def main() -> None:
    args = parse_args()

    if args.register:
        file_assoc.register()
        return
    if args.unregister:
        file_assoc.unregister()
        return

    files = [os.path.abspath(f) for f in args.files]
    mode = "enqueue" if args.enqueue else "play"

    if not instance.acquire():
        ok = instance.send_to_running(files, mode)
        sys.exit(0 if ok else 1)

    atexit.register(instance.clear_info)
    _install_signal_handlers()

    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

    # Иконка: файл + AUMID IconResource — иначе кнопка на панели задач
    # показывает иконку python.exe, а не нашу.
    try:
        ico = ensure_icon_file()
        file_assoc.register_app_id(icon_path=ico)
    except Exception as e:
        _log(f"register_app_id: {e}")

    port = find_free_port(PORT_START, PORT_END)
    url = f"http://127.0.0.1:{port}"

    controller.token = secrets.token_hex(16)

    # Discord Rich Presence: свой поток, плеер его не ждёт
    controller.init_presence()

    # Библиотека: уведомлять UI об изменениях и запустить кэш/watcher'а
    lib.on_change = lambda: controller.eval(
        "window.__onLibraryChanged && window.__onLibraryChanged()")
    lib.start()

    flask_thread = threading.Thread(target=run_flask, args=(port,), daemon=True)
    flask_thread.start()

    if not wait_for_server(port):
        print("Flask не запустился за 10 секунд", file=sys.stderr)
        sys.exit(1)

    instance.write_info(port, controller.token)

    try:
        from tray import Tray
        controller.tray = Tray(controller)
        controller.tray.start()
        controller.tray_ok = True
    except Exception as e:
        print(f"Трей недоступен ({e}). Установите: pip install pystray pillow",
              file=sys.stderr)

    controller.start_hidden = bool(args.minimized and not files and controller.tray_ok)
    if files:
        controller._pending_open = (files, mode)

    api = WindowApi()
    api._close_cb = controller.request_close
    api._on_installed = controller.restore_geometry
    api._presence = controller.presence
    api._discord_status = controller.discord_status
    api._discord_set = controller.set_discord_config
    controller.api = api

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN,
        frameless=True,
        easy_drag=False,
        hidden=controller.start_hidden,
        background_color=WINDOW_BG,
        js_api=api,
    )
    controller.window = window
    api.bind(window)
    window.events.loaded += controller.on_loaded
    window.events.closing += controller.on_closing

    webview.start(debug=False)

    # webview.start() вернулся — приложение закрывается.
    controller.quitting = True
    instance.clear_info()

    # Трей мог быть уже остановлен в quit(); повторный stop() безопасен
    # (idempotent), но ссылку всё равно обнуляем.
    tray = controller.tray
    controller.tray = None
    if tray:
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()