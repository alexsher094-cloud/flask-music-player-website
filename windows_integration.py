"""
windows_integration.py — интеграция с Windows.

Возможности:
    • Иконка приложения (рисуется в коде через Pillow, без .ico-файлов).
    • Глобальные медиаклавиши (Play/Pause, Next, Prev, Stop).
    • Thumbnail toolbar в превью панели задач (Prev/PlayPause/Next).
    • Прогресс воспроизведения на значке панели задач.
    • Jump List с недавними треками (best effort).
    • Мини-режим окна (компактный, поверх остальных).

Зависимости:
    pip install pywin32 pillow

Taskbar использует raw COM через ctypes (vtable-вызовы), потому что
на многих системах shell32.dll не содержит Type Library для ITaskbarList3
(E_NOINTERFACE / ITaskbarList3 not in shell32 typelib).
"""

import ctypes
import os
import sys
import tempfile
import threading
import time
import uuid
from ctypes import wintypes

# ============================================================
# WIN32 — базовые объявления
# ============================================================
user32  = ctypes.WinDLL("user32",  use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32   = ctypes.WinDLL("ole32",   use_last_error=True)

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
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype  = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype  = wintypes.LPARAM
_set_class_long = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
_set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_class_long.restype  = ctypes.c_ssize_t
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype  = wintypes.HANDLE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype  = wintypes.BOOL

ole32.CoInitialize.argtypes = [ctypes.c_void_p]
ole32.CoInitialize.restype  = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype  = ctypes.c_long

# сообщения / константы
WM_HOTKEY     = 0x0312
WM_COMMAND    = 0x0111
WM_SYSCOMMAND = 0x0112
WM_SETICON    = 0x0080
SC_MAXIMIZE   = 0xF030
SC_RESTORE    = 0xF120

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
IMAGE_ICON      = 1
LR_LOADFROMFILE = 0x0010

ICON_SMALL  = 0
ICON_BIG    = 1
ICON_SMALL2 = 2
GCLP_HICON   = -14
GCLP_HICONSM = -34

SM_CXICON    = 11
SM_CYICON    = 12
SM_CXSMICON  = 49
SM_CYSMICON  = 50
SM_CXSCREEN  = 0
SM_CYSCREEN  = 1

HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010
SW_RESTORE     = 9

MINI_WIDTH  = 460
MINI_HEIGHT = 140

APP_USER_MODEL_ID = "Music.Player"

CLSCTX_INPROC_SERVER = 0x1


# ============================================================
# COM: GUID и THUMBBUTTON
# ============================================================
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_string(s: str) -> _GUID:
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i in range(8):
        g.Data4[i] = u.bytes[8 + i]
    return g


class THUMBBUTTON(ctypes.Structure):
    _fields_ = [
        ("dwMask",  wintypes.DWORD),
        ("iId",     wintypes.UINT),
        ("iBitmap", wintypes.UINT),
        ("hIcon",   wintypes.HICON),
        ("szTip",   ctypes.c_wchar * 260),
        ("dwFlags", wintypes.DWORD),
    ]


VT_RELEASE              = 2
VT_HRINIT               = 3
VT_SET_PROGRESS_VALUE   = 9
VT_SET_PROGRESS_STATE   = 10
VT_THUMB_BAR_ADD        = 15
VT_THUMB_BAR_UPDATE     = 16


# ============================================================
# ИКОНКИ: общая функция рисования через Pillow
# ============================================================
def _draw_note(size: int, draw_fn, bg: bool = True) -> "Image.Image":
    """Рисует нотку. bg=True — на фиолетовом скруглённом фоне (иконка приложения),
       bg=False — прозрачный фон (thumbnail-кнопки)."""
    from PIL import Image, ImageDraw

    s = size * 4 if size < 256 else size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if bg:
        radius = int(s * 0.22)
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=(168, 85, 247, 255))

    white = (255, 255, 255, 255)
    # головка ноты
    d.ellipse((s * 0.18, s * 0.56, s * 0.46, s * 0.80), fill=white)
    # штиль
    d.rectangle((s * 0.41, s * 0.22, s * 0.47, s * 0.68), fill=white)
    # флажок
    d.polygon(
        [(s * 0.41, s * 0.22), (s * 0.76, s * 0.32),
         (s * 0.76, s * 0.46), (s * 0.41, s * 0.36)],
        fill=white,
    )

    return img.resize((size, size), Image.LANCZOS)


def _make_app_icon(size: int = 256):
    """Иконка приложения: фиолетовый квадрат + белая нота."""
    return _draw_note(size, None, bg=True)


def ensure_icon_file() -> str:
    """
    Сохраняет .ico приложения (многоразмерный).

    ВАЖНО: Store-версия Python (Microsoft Store) виртуализирует запись в
    %APPDATA%: файл попадает в Packages\\...\\LocalCache\\Roaming, а Explorer
    ищет его по настоящему пути — файла нет, и кнопка на панели задач
    показывает иконку python.exe (из IconResource в AUMID).

    Поэтому сначала пишем в папку приложения (не виртуализируется),
    затем в корень профиля пользователя, и только потом в %APPDATA%.
    Возвращает путь или "" при ошибке.
    """
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        app_dir if app_dir else None,
        os.path.expanduser("~"),
        os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Music"),
    ]

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    for folder in candidates:
        if not folder:
            continue
        try:
            if folder != app_dir:
                os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "Music.ico")
            _make_app_icon(256).save(path, format="ICO", sizes=sizes)
            return path
        except (OSError, ValueError) as e:
            print(f"[Icon] save failed ({folder}: {e})", file=sys.stderr)
    return ""


def _make_icon_image(kind: str, size: int = 16):
    """Иконки для thumbnail toolbar: play/pause/next/prev на прозрачном фоне."""
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


def _save_ico(img, path: str, sizes=None) -> None:
    if sizes:
        img.save(path, format="ICO", sizes=sizes)
    else:
        img.save(path, format="ICO", sizes=[(16, 16)])


# ============================================================
# THUMBNAIL TOOLBAR / PROGRESS — raw ctypes vtable
# ============================================================
class _TaskbarBridge:
    """
    Обёртка над ITaskbarList3 через raw ctypes (без Type Library).

    На многих системах shell32.tlb не содержит ITaskbarList3
    (E_NOINTERFACE), а comtypes.client.GetModule("shell32.dll") не находит
    интерфейс. Поэтому создаём объект через ole32.CoCreateInstance с IID
    ITaskbarList3 и вызываем методы через vtable (function pointer +
    WINFUNCTYPE).
    """

    IID_ITaskbarList3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
    CLSID_TaskbarList = "{56FDF344-FD6D-11d0-958A-006097C9A090}"

    def __init__(self):
        self._lock = threading.Lock()
        self._ptr = None
        self._attempted = False
        self._ok = False
        self._icons = {}
        self._tmpdir = tempfile.mkdtemp(prefix="music_thumbs_")

    @staticmethod
    def _co_init():
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass

    def _get_method(self, index: int, restype, *argtypes):
        if not self._ptr:
            return None
        vtable_ptr = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.c_void_p))
        vtable_addr = vtable_ptr[0]
        method_arr = ctypes.cast(vtable_addr, ctypes.POINTER(ctypes.c_void_p))
        method_addr = method_arr[index]
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(method_addr)

    def _ensure(self) -> bool:
        if self._attempted:
            return self._ok
        with self._lock:
            if self._attempted:
                return self._ok
            self._attempted = True
            try:
                self._co_init()

                clsid = _guid_from_string(self.CLSID_TaskbarList)
                iid = _guid_from_string(self.IID_ITaskbarList3)

                ptr = ctypes.c_void_p()
                hr = ole32.CoCreateInstance(
                    ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                    ctypes.byref(iid), ctypes.byref(ptr),
                )
                if hr != 0:
                    raise RuntimeError(f"CoCreateInstance hr={hr & 0xffffffff:#010x}")
                if not ptr.value:
                    raise RuntimeError("CoCreateInstance returned NULL")

                self._ptr = ptr

                hrinit = self._get_method(VT_HRINIT, ctypes.c_long)
                if hrinit is None:
                    raise RuntimeError("no HrInit")
                hr = hrinit(self._ptr)
                if hr < 0:
                    raise RuntimeError(f"HrInit hr={hr & 0xffffffff:#010x}")

                self._ok = True
                return True
            except Exception as e:
                print(f"[Taskbar] init failed: {e}", file=sys.stderr)
                self._ok = False
                if self._ptr:
                    try:
                        rel = self._get_method(VT_RELEASE, ctypes.c_ulong)
                        if rel:
                            rel(self._ptr)
                    except Exception:
                        pass
                    self._ptr = None
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
        if not self._ensure():
            return
        self._co_init()
        try:
            add = self._get_method(
                VT_THUMB_BAR_ADD, ctypes.c_long,
                wintypes.HWND, wintypes.UINT, ctypes.POINTER(THUMBBUTTON)
            )
            if add is None:
                return

            btns = (THUMBBUTTON * 3)()

            btns[0].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
            btns[0].iId     = CMD_PREV
            btns[0].hIcon   = self._icon_prev()
            btns[0].szTip   = "Предыдущий"
            btns[0].dwFlags = THBF_ENABLED

            btns[1].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
            btns[1].iId     = CMD_PLAY
            btns[1].hIcon   = self._icon_play()
            btns[1].szTip   = "Играть"
            btns[1].dwFlags = THBF_ENABLED

            btns[2].dwMask  = THB_ICON | THB_TOOLTIP | THB_FLAGS
            btns[2].iId     = CMD_NEXT
            btns[2].hIcon   = self._icon_next()
            btns[2].szTip   = "Следующий"
            btns[2].dwFlags = THBF_ENABLED

            hr = add(self._ptr, hwnd, 3, btns)
            if hr < 0:
                print(f"[Taskbar] ThumbBarAddButtons hr={hr & 0xffffffff:#010x}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[Taskbar] add buttons: {e}", file=sys.stderr)

    def set_play_icon(self, hwnd: int, is_playing: bool):
        if not self._ensure():
            return
        self._co_init()
        try:
            upd = self._get_method(
                VT_THUMB_BAR_UPDATE, ctypes.c_long,
                wintypes.HWND, wintypes.UINT, ctypes.POINTER(THUMBBUTTON)
            )
            if upd is None:
                return

            btn = THUMBBUTTON()
            btn.dwMask  = THB_ICON | THB_TOOLTIP
            btn.iId     = CMD_PLAY
            btn.hIcon   = self._icon_pause() if is_playing else self._icon_play()
            btn.szTip   = "Пауза" if is_playing else "Играть"

            arr = (THUMBBUTTON * 1)(btn)
            upd(self._ptr, hwnd, 1, arr)
        except Exception:
            pass

    # ---------- progress ----------
    def set_progress(self, hwnd: int, completed: int, total: int, state: int):
        if not self._ensure():
            return
        self._co_init()
        try:
            set_state = self._get_method(
                VT_SET_PROGRESS_STATE, ctypes.c_long,
                wintypes.HWND, ctypes.c_uint
            )
            if set_state is None:
                return
            set_state(self._ptr, hwnd, state)

            if state != TBPF_NOPROGRESS and total > 0:
                set_val = self._get_method(
                    VT_SET_PROGRESS_VALUE, ctypes.c_long,
                    wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong
                )
                if set_val is not None:
                    set_val(self._ptr, hwnd,
                            max(0, min(completed, total)), total)
        except Exception:
            pass

    def cleanup(self):
        with self._lock:
            if self._ptr:
                try:
                    rel = self._get_method(VT_RELEASE, ctypes.c_ulong)
                    if rel:
                        rel(self._ptr)
                except Exception:
                    pass
                self._ptr = None
            for h in self._icons.values():
                try:
                    if h:
                        user32.DestroyIcon(h)
                except Exception:
                    pass
            self._icons.clear()
            self._ok = False
            self._attempted = False


# ============================================================
# JUMP LIST — best effort через pywin32
# ============================================================
class _JumpListBridge:
    """
    Строит jump list с категорией «Недавние треки».

    Best effort: если BeginList/CommitList возвращают E_ACCESSDENIED
    (например, из-за политики ОС, Windows Store shim, запуска из
    WindowsApps или AUMID без системной регистрации в HKLM), мы
    тихо отключаемся — спама в логе нет, jumplist просто не появится.

    SetAppID НЕ вызывается: он даёт E_ACCESSDENIED без системной
    регистрации AUMID в HKLM.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._available = False
        self._attempted = False
        self._ok = False
        try:
            import pythoncom  # noqa
            from win32com.shell import shell, shellcon  # noqa
            self._available = True
        except ImportError:
            pass

    def update(self, recent_paths):
        if not self._available:
            return
        if self._attempted and not self._ok:
            return

        paths = [p for p in (recent_paths or []) if p][:10]

        with self._lock:
            if self._attempted and not self._ok:
                return
            self._attempted = True

            try:
                import pythoncom
                from win32com.shell import shell, shellcon

                try:
                    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
                except Exception:
                    pass

                cdl = pythoncom.CoCreateInstance(
                    shell.CLSID_DestinationList, None,
                    pythoncom.CLSCTX_INPROC_SERVER,
                    shell.IID_ICustomDestinationList,
                )
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
                    cdl.AppendCategory(
                        "Недавние треки",
                        oc.QueryInterface(shell.IID_IObjectArray),
                    )

                cdl.CommitList()
                self._ok = True
            except Exception:
                # E_ACCESSDENIED и подобные — известное ограничение среды.
                # Молча отключаемся, чтобы не спамить в лог.
                self._ok = False


# ============================================================
# МИНИ-РЕЖИМ
# ============================================================
class _MiniModeBridge:
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
                user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
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

        self._window_icons = []                     # HICON'ы для cleanup
        self._icons_tmpdir = tempfile.mkdtemp(prefix="music_appicon_")

    # ---------- setup / cleanup ----------
    def setup(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.mini.hwnd = hwnd

        self._install_window_icon(hwnd)

        self._register_hotkey(HK_PLAY_PAUSE, VK_MEDIA_PLAY_PAUSE)
        self._register_hotkey(HK_NEXT,       VK_MEDIA_NEXT_TRACK)
        self._register_hotkey(HK_PREV,       VK_MEDIA_PREV_TRACK)
        self._register_hotkey(HK_STOP,       VK_MEDIA_STOP)

        self.taskbar.add_thumb_buttons(hwnd)

    def cleanup(self) -> None:
        for hk_id in list(self._hotkeys):
            try:
                user32.UnregisterHotKey(self.hwnd, hk_id)
            except Exception:
                pass
        self._hotkeys.clear()
        self.taskbar.cleanup()

        for h in self._window_icons:
            try:
                user32.DestroyIcon(h)
            except Exception:
                pass
        self._window_icons.clear()

    # ---------- иконка окна / панели задач ----------
    def _install_window_icon(self, hwnd: int) -> None:
        """
        Ставит иконку тремя способами — taskbar читает её НЕ из WM_SETICON,
        а из GCLP_HICON/HICONSM класса окна (или из AUMID IconResource):
          1. WM_SETICON                              — заголовок, Alt+Tab.
          2. SetClassLongPtr(GCLP_HICON/HICONSM)     — кнопка на панели задач.
          3. AUMID IconResource (см. file_assoc)     — гарантия на кнопке.
        """
        try:
            ico_path = ensure_icon_file()
            if not ico_path or not os.path.isfile(ico_path):
                return

            cx_big   = user32.GetSystemMetrics(SM_CXICON)    or 32
            cy_big   = user32.GetSystemMetrics(SM_CYICON)    or 32
            cx_small = user32.GetSystemMetrics(SM_CXSMICON)  or 16
            cy_small = user32.GetSystemMetrics(SM_CYSMICON)  or 16

            h_big = user32.LoadImageW(
                None, ico_path, IMAGE_ICON, cx_big, cy_big, LR_LOADFROMFILE
            )
            h_small = user32.LoadImageW(
                None, ico_path, IMAGE_ICON, cx_small, cy_small, LR_LOADFROMFILE
            )

            if h_big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                _set_class_long(hwnd, GCLP_HICON, h_big)
                self._window_icons.append(h_big)

            if h_small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL,  h_small)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, h_small)
                _set_class_long(hwnd, GCLP_HICONSM, h_small)
                self._window_icons.append(h_small)
        except Exception as e:
            print(f"[Icon] failed: {e}", file=sys.stderr)
        try:
            from PIL import Image  # noqa: проверка доступности Pillow

            ico_path = os.path.join(self._icons_tmpdir, "app.ico")
            base = _make_app_icon(256)
            base.save(
                ico_path,
                format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)],
            )

            cx_big = user32.GetSystemMetrics(SM_CXICON) or 32
            cy_big = user32.GetSystemMetrics(SM_CYICON) or 32
            cx_small = user32.GetSystemMetrics(SM_CXSMICON) or 16
            cy_small = user32.GetSystemMetrics(SM_CYSMICON) or 16

            h_big = user32.LoadImageW(
                None, ico_path, IMAGE_ICON, cx_big, cy_big, LR_LOADFROMFILE
            )
            h_small = user32.LoadImageW(
                None, ico_path, IMAGE_ICON, cx_small, cy_small, LR_LOADFROMFILE
            )

            if h_big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                self._window_icons.append(h_big)

            if h_small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
                # ICON_SMALL2 появился в Vista — на всякий случай
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, h_small)
                self._window_icons.append(h_small)
        except Exception as e:
            print(f"[Icon] failed: {e}", file=sys.stderr)

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

    def update_progress(self, position, duration, is_playing: bool) -> None:
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

    def update_recent_tracks(self, paths) -> None:
        def _run():
            self.jumplist.update(paths)
        threading.Thread(target=_run, daemon=True).start()

    def toggle_mini_mode(self) -> None:
        self.mini.toggle()

    def set_mini_mode(self, enabled: bool) -> None:
        if enabled:
            self.mini.enter()
        else:
            self.mini.exit()

    def is_mini(self) -> bool:
        return self.mini.mini