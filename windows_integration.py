"""
windows_integration.py — интеграция с Windows.

Возможности:
    • Глобальные медиаклавиши (Play/Pause, Next, Prev, Stop).
    • Thumbnail toolbar в превью панели задач (Prev/PlayPause/Next).
    • Прогресс воспроизведения на значке панели задач.
    • Jump List с недавними треками (категория «Недавние»).
    • Мини-режим окна (компактный, поверх остальных).

Зависимости:
    pip install pywin32 comtypes pillow

Использование в desktop.py (см. правки ниже):
    from windows_integration import WindowsIntegration
    integration = WindowsIntegration(controller)
    integration.setup(hwnd)          # после установки WndProc-хука
    ...
    # в _wndproc:
    if msg == WM_HOTKEY:  integration.handle_hotkey(int(wparam))
    if msg == WM_COMMAND: integration.handle_command(int(wparam), int(lparam))
"""

import ctypes
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes

# ============================================================
# WIN32 — базовые объявления
# ============================================================
user32  = ctypes.WinDLL("user32",  use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype  = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype  = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype  = wintypes.BOOL
user32.SetWindowPos.argtypes  = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype   = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype  = ctypes.c_int
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype  = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype  = wintypes.BOOL

user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype  = wintypes.HANDLE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype  = wintypes.BOOL

# сообщения / константы
WM_HOTKEY = 0x0312
WM_COMMAND = 0x0111

MOD_NOREPEAT = 0x4000

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP       = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

HK_PLAY_PAUSE = 1
HK_NEXT       = 2
HK_PREV       = 3
HK_STOP       = 4

# thumbnail toolbar
THBN_CLICKED  = 0x1800
THB_ICON      = 0x02
THB_TOOLTIP   = 0x04
THB_FLAGS     = 0x08
THBF_ENABLED  = 0x0000
THBF_DISABLED = 0x0001

CMD_PREV = 1
CMD_PLAY = 2
CMD_NEXT = 3

# progress state
TBPF_NOPROGRESS    = 0x00
TBPF_INDETERMINATE = 0x01
TBPF_NORMAL        = 0x02
TBPF_ERROR         = 0x04
TBPF_PAUSED        = 0x08

# icons
IMAGE_ICON     = 1
LR_LOADFROMFILE = 0x0010

SM_CXSCREEN = 0
SM_CYSCREEN = 1

HWND_TOPMOST    = -1
HWND_NOTOPMOST  = -2
SWP_NOMOVE      = 0x0002
SWP_NOSIZE      = 0x0001
SW_RESTORE      = 9

MINI_WIDTH  = 460
MINI_HEIGHT = 140


# ============================================================
# ИКОНКИ THUMBNAIL TOOLBAR
# Рисуем через Pillow — отдельные .ico файлы не нужны.
# ============================================================
def _make_icon_image(kind: str, size: int = 16):
    from PIL import Image, ImageDraw
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = (255, 255, 255, 255)

    if kind == "play":
        d.polygon([(s * 0.30, s * 0.18), (s * 0.30, s * 0.82), (s * 0.82, s * 0.50)], fill=w)
    elif kind == "pause":
        d.rectangle((s * 0.28, s * 0.20, s * 0.44, s * 0.80), fill=w)
        d.rectangle((s * 0.56, s * 0.20, s * 0.72, s * 0.80), fill=w)
    elif kind == "next":
        d.polygon([(s * 0.20, s * 0.18), (s * 0.20, s * 0.82), (s * 0.65, s * 0.50)], fill=w)
        d.rectangle((s * 0.68, s * 0.18, s * 0.80, s * 0.82), fill=w)
    elif kind == "prev":
        d.rectangle((s * 0.20, s * 0.18, s * 0.32, s * 0.82), fill=w)
        d.polygon([(s * 0.80, s * 0.18), (s * 0.80, s * 0.82), (s * 0.35, s * 0.50)], fill=w)

    return img.resize((size, size), Image.LANCZOS)


def _save_ico(img, path: str) -> None:
    img.save(path, format="ICO", sizes=[(16, 16)])


# ============================================================
# THUMBNAIL TOOLBAR / PROGRESS — через comtypes
# ============================================================
class _TaskbarBridge:
    """
    Ленивая обёртка над ITaskbarList3. Создаём объект только когда он нужен.
    Все публичные методы потокобезопасны — вызываем через lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._taskbar = None
        self._ok = False
        self._icons = {}          # kind -> HICON (держим ссылки!)
        self._tmpdir = tempfile.mkdtemp(prefix="music_thumbs_")

    def _ensure(self):
        if self._ok:
            return True
        try:
            import comtypes
            from comtypes import GUID, COMMETHOD, IUnknown, HRESULT
            from ctypes import POINTER, c_void_p, c_uint, c_ulong, c_ulonglong, c_wchar_p, Structure

            class THUMBBUTTON(Structure):
                _fields_ = [
                    ("dwMask",  c_ulong),
                    ("iId",     c_uint),
                    ("iBitmap", c_uint),
                    ("hIcon",   c_void_p),
                    ("szTip",   ctypes.c_wchar * 260),
                    ("dwFlags", c_ulong),
                ]

            CLSID_TaskbarList = GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")

            class ITaskbarList3(IUnknown):
                _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEA84}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "HrInit"),
                    COMMETHOD([], HRESULT, "AddTab",   (["in"], c_void_p, "hwnd")),
                    COMMETHOD([], HRESULT, "DeleteTab",(["in"], c_void_p, "hwnd")),
                    COMMETHOD([], HRESULT, "ActivateTab",(["in"], c_void_p, "hwnd")),
                    COMMETHOD([], HRESULT, "SetActiveAlt",(["in"], c_void_p, "hwnd")),
                    COMMETHOD([], HRESULT, "MarkFullscreenWindow",
                              (["in"], c_void_p, "hwnd"), (["in"], c_uint, "fFullscreen")),
                    COMMETHOD([], HRESULT, "SetProgressValue",
                              (["in"], c_void_p, "hwnd"),
                              (["in"], c_ulonglong, "ullCompleted"),
                              (["in"], c_ulonglong, "ullTotal")),
                    COMMETHOD([], HRESULT, "SetProgressState",
                              (["in"], c_void_p, "hwnd"), (["in"], c_uint, "tbpFlags")),
                    COMMETHOD([], HRESULT, "RegisterTab",
                              (["in"], c_void_p, "hwndTab"), (["in"], c_void_p, "hwndMDI")),
                    COMMETHOD([], HRESULT, "UnregisterTab",(["in"], c_void_p, "hwndTab")),
                    COMMETHOD([], HRESULT, "SetTabOrder",
                              (["in"], c_void_p, "hwndTab"),
                              (["in"], c_void_p, "hwndInsertBefore")),
                    COMMETHOD([], HRESULT, "SetTabActive",
                              (["in"], c_void_p, "hwndTab"),
                              (["in"], c_void_p, "hwndMDI"),
                              (["in"], c_ulong, "dwReserved")),
                    COMMETHOD([], HRESULT, "ThumbBarAddButtons",
                              (["in"], c_void_p, "hwnd"),
                              (["in"], c_uint, "cButtons"),
                              (["in"], POINTER(THUMBBUTTON), "pButton")),
                    COMMETHOD([], HRESULT, "ThumbBarUpdateButtons",
                              (["in"], c_void_p, "hwnd"),
                              (["in"], c_uint, "cButtons"),
                              (["in"], POINTER(THUMBBUTTON), "pButton")),
                    COMMETHOD([], HRESULT, "ThumbBarSetImageList",
                              (["in"], c_void_p, "hwnd"), (["in"], c_void_p, "himl")),
                    COMMETHOD([], HRESULT, "SetOverlayIcon",
                              (["in"], c_void_p, "hwnd"),
                              (["in"], c_void_p, "hIcon"),
                              (["in"], c_wchar_p, "pszDescription")),
                    COMMETHOD([], HRESULT, "SetThumbnailTooltip",
                              (["in"], c_void_p, "hwnd"), (["in"], c_wchar_p, "pszTip")),
                    COMMETHOD([], HRESULT, "SetThumbnailClip",
                              (["in"], c_void_p, "hwnd"), (["in"], c_void_p, "prcClip")),
                ]

            self._THUMBBUTTON = THUMBBUTTON
            self._ITaskbarList3 = ITaskbarList3

            self._taskbar = comtypes.CoCreateInstance(
                CLSID_TaskbarList, interface=ITaskbarList3
            )
            self._taskbar.HrInit()
            self._ok = True
            return True
        except Exception as e:
            print(f"[Taskbar] init failed: {e}", file=sys.stderr)
            return False

    # ---------- icons ----------
    def _load_icon(self, kind: str):
        if kind in self._icons:
            return self._icons[kind]
        try:
            img = _make_icon_image(kind, size=16)
            path = os.path.join(self._tmpdir, f"{kind}.ico")
            _save_ico(img, path)
            h = user32.LoadImageW(None, path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if h:
                self._icons[kind] = h
                return h
        except Exception as e:
            print(f"[Taskbar] icon {kind}: {e}", file=sys.stderr)
        return None

    def _icon_play(self):  return self._load_icon("play")
    def _icon_pause(self): return self._load_icon("pause")
    def _icon_prev(self):  return self._load_icon("prev")
    def _icon_next(self):  return self._load_icon("next")

    # ---------- buttons ----------
    def add_thumb_buttons(self, hwnd: int):
        with self._lock:
            if not self._ensure():
                return
            try:
                THUMBBUTTON = self._THUMBBUTTON
                btns = (THUMBBUTTON * 3)()

                # prev
                btns[0].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
                btns[0].iId     = CMD_PREV
                btns[0].hIcon   = self._icon_prev()
                btns[0].szTip   = "Предыдущий"
                btns[0].dwFlags = THBF_ENABLED

                # play/pause (сейчас — play)
                btns[1].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
                btns[1].iId     = CMD_PLAY
                btns[1].hIcon   = self._icon_play()
                btns[1].szTip   = "Играть"
                btns[1].dwFlags = THBF_ENABLED

                # next
                btns[2].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
                btns[2].iId     = CMD_NEXT
                btns[2].hIcon   = self._icon_next()
                btns[2].szTip   = "Следующий"
                btns[2].dwFlags = THBF_ENABLED

                self._taskbar.ThumbBarAddButtons(ctypes.c_void_p(hwnd), 3, btns)
            except Exception as e:
                print(f"[Taskbar] add buttons: {e}", file=sys.stderr)

    def set_play_icon(self, hwnd: int, is_playing: bool):
        with self._lock:
            if not self._ensure():
                return
            try:
                THUMBBUTTON = self._THUMBBUTTON
                btn = THUMBBUTTON()
                btn.dwMask  = THB_ICON | THB_TOOLTIP
                btn.iId     = CMD_PLAY
                btn.hIcon   = self._icon_pause() if is_playing else self._icon_play()
                btn.szTip   = "Пауза" if is_playing else "Играть"
                arr = (THUMBBUTTON * 1)(btn)
                self._taskbar.ThumbBarUpdateButtons(ctypes.c_void_p(hwnd), 1, arr)
            except Exception as e:
                print(f"[Taskbar] set icon: {e}", file=sys.stderr)

    # ---------- progress ----------
    def set_progress(self, hwnd: int, completed: int, total: int, state: int):
        with self._lock:
            if not self._ensure():
                return
            try:
                self._taskbar.SetProgressState(ctypes.c_void_p(hwnd), state)
                if state != TBPF_NOPROGRESS and total > 0:
                    self._taskbar.SetProgressValue(
                        ctypes.c_void_p(hwnd),
                        max(0, min(completed, total)),
                        total,
                    )
            except Exception:
                pass

    def cleanup(self):
        with self._lock:
            for h in self._icons.values():
                try:
                    if h:
                        user32.DestroyIcon(h)
                except Exception:
                    pass
            self._icons.clear()
            self._ok = False
            self._taskbar = None


# ============================================================
# JUMP LIST — через pywin32
# ============================================================
class _JumpListBridge:
    """
    Строит jump list с категорией «Недавние треки».
    Для каждого трека — IShellItem из аудиофайла: при клике Windows
    откроет его в приложении по ассоциации (наш плеер).
    """

    APP_ID = "Music.Player"

    def __init__(self):
        self._lock = threading.Lock()
        self._ok = False
        self._available = False
        try:
            import pythoncom  # noqa
            from win32com.shell import shell, shellcon  # noqa
            self._available = True
        except ImportError:
            pass

    def update(self, recent_paths):
        if not self._available:
            return
        # ограничим до 10, чтобы jump list не разрастался
        paths = [p for p in (recent_paths or []) if p][:10]
        with self._lock:
            try:
                import pythoncom
                from win32com.shell import shell, shellcon

                pythoncom.CoInitialize()
                try:
                    cdl = pythoncom.CoCreateInstance(
                        shell.CLSID_DestinationList, None,
                        pythoncom.CLSCTX_INPROC_SERVER,
                        shell.IID_ICustomDestinationList,
                    )
                    try:
                        cdl.SetAppID(self.APP_ID)
                    except Exception:
                        pass

                    # BeginList — вернёт max slots и removed items
                    cdl.BeginList()

                    oc = pythoncom.CoCreateInstance(
                        shell.CLSID_EnumerableObjectCollection, None,
                        pythoncom.CLSCTX_INPROC_SERVER,
                        shell.IID_IObjectCollection,
                    )

                    for path in paths:
                        try:
                            if not os.path.isfile(path):
                                continue
                            item = shell.SHCreateItemFromParsingName(
                                path, None, shell.IID_IShellItem
                            )
                            oc.AddObject(item)
                        except Exception:
                            continue

                    if oc.GetCount() > 0:
                        # пустая категория не нужна
                        cdl.AppendCategory(
                            "Недавние треки",
                            oc.QueryInterface(shell.IID_IObjectArray),
                        )

                    cdl.CommitList()
                finally:
                    pythoncom.CoUninitialize()
            except Exception as e:
                print(f"[JumpList] {e}", file=sys.stderr)


# ============================================================
# МИНИ-РЕЖИМ
# ============================================================
class _MiniModeBridge:
    """
    Управляет нативным окном при переключении мини-режима.
    UI (CSS/классы) переключает JS — Python только меняет
    размер/позицию окна и topmost.
    """

    def __init__(self, controller):
        self.controller = controller
        self.hwnd = None
        self.mini = False
        self._saved_rect = None
        self._saved_maximized = False

    def _is_maximized(self) -> bool:
        return bool(self.hwnd and user32.IsZoomed(self.hwnd))

    def enter(self):
        if not self.hwnd or self.mini:
            return
        try:
            self._saved_maximized = self._is_maximized()
            if self._saved_maximized:
                user32.ShowWindow(self.hwnd, SW_RESTORE)

            r = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            self._saved_rect = (r.left, r.top, r.right, r.bottom)

            sw = user32.GetSystemMetrics(SM_CXSCREEN)
            sh = user32.GetSystemMetrics(SM_CYSCREEN)

            w, h = MINI_WIDTH, MINI_HEIGHT
            x = max(0, sw - w - 24)
            y = max(0, sh - h - 64)

            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, 0)
            self.mini = True
            self.controller.eval("window.__onMiniMode && window.__onMiniMode(true)")
        except Exception as e:
            print(f"[MiniMode] enter: {e}", file=sys.stderr)

    def exit(self):
        if not self.hwnd or not self.mini:
            return
        try:
            if self._saved_rect:
                l, t, r, b = self._saved_rect
                user32.SetWindowPos(self.hwnd, HWND_NOTOPMOST,
                                    l, t, r - l, b - t, 0)
            self._saved_rect = None
            self.mini = False
            if self._saved_maximized:
                user32.PostMessageW(self.hwnd, 0x0112, 0xF030, 0)  # SC_MAXIMIZE
            self.controller.eval("window.__onMiniMode && window.__onMiniMode(false)")
        except Exception as e:
            print(f"[MiniMode] exit: {e}", file=sys.stderr)

    def toggle(self):
        if self.mini:
            self.exit()
        else:
            self.enter()


# ============================================================
# ФАСАД
# ============================================================
class WindowsIntegration:
    def __init__(self, controller):
        self.controller = controller
        self.hwnd = None
        self.taskbar = _TaskbarBridge()
        self.jumplist = _JumpListBridge()
        self.mini = _MiniModeBridge(controller)
        self._hotkeys = {}

    # ---------- setup / cleanup ----------
    def setup(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.mini.hwnd = hwnd

        self._register_hotkey(HK_PLAY_PAUSE, VK_MEDIA_PLAY_PAUSE)
        self._register_hotkey(HK_NEXT,       VK_MEDIA_NEXT_TRACK)
        self._register_hotkey(HK_PREV,       VK_MEDIA_PREV_TRACK)
        self._register_hotkey(HK_STOP,       VK_MEDIA_STOP)

        # кнопки на превью панели задач
        self.taskbar.add_thumb_buttons(hwnd)

    def cleanup(self) -> None:
        for hk_id in list(self._hotkeys):
            try:
                user32.UnregisterHotKey(self.hwnd, hk_id)
            except Exception:
                pass
        self._hotkeys.clear()
        self.taskbar.cleanup()

    # ---------- hotkeys ----------
    def _register_hotkey(self, hk_id: int, vk: int) -> None:
        try:
            ok = user32.RegisterHotKey(self.hwnd, hk_id, MOD_NOREPEAT, vk)
            if not ok:
                err = ctypes.get_last_error()
                print(f"[Hotkey] id={hk_id} vk=0x{vk:02X} failed (err={err})",
                      file=sys.stderr)
            else:
                self._hotkeys[hk_id] = vk
        except Exception as e:
            print(f"[Hotkey] {e}", file=sys.stderr)

    def handle_hotkey(self, hk_id: int) -> None:
        actions = {
            HK_PLAY_PAUSE: "playpause",
            HK_NEXT:       "next",
            HK_PREV:       "prev",
            HK_STOP:       "stop",
        }
        action = actions.get(hk_id)
        if action:
            self.controller.eval(
                f"window.__mediaAction && window.__mediaAction({action!r})"
            )

    # ---------- thumbnail toolbar ----------
    def handle_command(self, wparam: int, lparam: int) -> None:
        cmd_id = wparam & 0xFFFF
        notification = (wparam >> 16) & 0xFFFF
        if notification != THBN_CLICKED:
            return
        action = {CMD_PREV: "prev", CMD_PLAY: "playpause", CMD_NEXT: "next"}.get(cmd_id)
        if action:
            self.controller.eval(
                f"window.__mediaAction && window.__mediaAction({action!r})"
            )

    def set_playback_state(self, is_playing: bool) -> None:
        if self.hwnd:
            self.taskbar.set_play_icon(self.hwnd, bool(is_playing))

    def update_progress(self, position: float, duration: float,
                        is_playing: bool) -> None:
        if not self.hwnd:
            return
        try:
            dur = int(max(0, float(duration)))
            pos = int(max(0, min(float(position), dur)))
        except (TypeError, ValueError):
            return

        if dur <= 0:
            self.taskbar.set_progress(self.hwnd, 0, 0, TBPF_NOPROGRESS)
            return

        state = TBPF_NORMAL if is_playing else TBPF_PAUSED
        self.taskbar.set_progress(self.hwnd, pos, dur, state)

    def clear_progress(self) -> None:
        if self.hwnd:
            self.taskbar.set_progress(self.hwnd, 0, 0, TBPF_NOPROGRESS)

    # ---------- jump list ----------
    def update_recent_tracks(self, paths) -> None:
        def _run():
            self.jumplist.update(paths)
        threading.Thread(target=_run, daemon=True).start()

    # ---------- мини-режим ----------
    def toggle_mini_mode(self) -> None:
        self.mini.toggle()

    def set_mini_mode(self, enabled: bool) -> None:
        if enabled:
            self.mini.enter()
        else:
            self.mini.exit()

    def is_mini(self) -> bool:
        return self.mini.mini