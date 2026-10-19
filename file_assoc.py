"""
file_assoc.py — регистрация в Windows (без прав администратора, всё в HKCU):
  • «Открыть с помощью» / «Приложения по умолчанию» для аудиофайлов
  • пункты контекстного меню Проводника: «Воспроизвести» / «Добавить в очередь»
  • автозапуск вместе с Windows

CLI (для установщика):
    python file_assoc.py --register | --unregister
    python file_assoc.py --autostart on | off

Windows 10/11 не даёт программам самим назначить себя «по умолчанию» —
после register() пользователь выбирает Music в «Открыть с помощью»
или в Параметры → Приложения по умолчанию.
"""

import ctypes
import os
import sys
import winreg

APP_NAME = "Music"
PROGID = "MusicPlayer.Audio"
CAPS_KEY = r"Software\MusicPlayer\Capabilities"
EXTS = (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
HKCU = winreg.HKEY_CURRENT_USER


def launch_prefix() -> str:
    """Команда запуска приложения (без аргументов)."""
    if getattr(sys, "frozen", False):                  # PyInstaller / Nuitka
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):             # без консольного окна
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            exe = pyw
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "desktop.py"))
    return f'"{exe}" "{script}"'


def _set(path: str, name, value: str) -> None:
    with winreg.CreateKeyEx(HKCU, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str) -> None:
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + sub)
        winreg.DeleteKey(HKCU, path)
    except OSError:
        pass


def _notify_shell() -> None:
    try:
        # SHCNE_ASSOCCHANGED, SHCNF_IDLIST
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


# ---------- ассоциации и контекстное меню ----------
def register() -> None:
    prefix = launch_prefix()
    base = rf"Software\Classes\{PROGID}"

    _set(base, None, "Аудиофайл (Music)")
    _set(base + r"\shell\open", "FriendlyAppName", APP_NAME)
    _set(base + r"\shell\open\command", None, f'{prefix} "%1"')

    # ProgID виден в «Открыть с помощью» для каждого расширения
    for ext in EXTS:
        with winreg.CreateKeyEx(HKCU, rf"Software\Classes\{ext}\OpenWithProgids",
                                0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, PROGID, 0, winreg.REG_NONE, b"")

        # контекстное меню Проводника
        shell = rf"Software\Classes\SystemFileAssociations\{ext}\shell"
        _set(shell + r"\MusicPlayer.Play", "MUIVerb", "Воспроизвести в Music")
        _set(shell + r"\MusicPlayer.Play\command", None, f'{prefix} "%1"')
        _set(shell + r"\MusicPlayer.Enqueue", "MUIVerb", "Добавить в очередь Music")
        _set(shell + r"\MusicPlayer.Enqueue\command", None, f'{prefix} --enqueue "%1"')

    # раздел «Приложения по умолчанию»
    _set(CAPS_KEY, "ApplicationName", APP_NAME)
    _set(CAPS_KEY, "ApplicationDescription", "Музыкальный плеер")
    for ext in EXTS:
        _set(CAPS_KEY + r"\FileAssociations", ext, PROGID)
    _set(r"Software\RegisteredApplications", APP_NAME, CAPS_KEY)

    _notify_shell()


def unregister() -> None:
    _delete_tree(rf"Software\Classes\{PROGID}")
    for ext in EXTS:
        try:
            with winreg.OpenKey(HKCU, rf"Software\Classes\{ext}\OpenWithProgids",
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, PROGID)
        except OSError:
            pass
        _delete_tree(rf"Software\Classes\SystemFileAssociations\{ext}\shell\MusicPlayer.Play")
        _delete_tree(rf"Software\Classes\SystemFileAssociations\{ext}\shell\MusicPlayer.Enqueue")
    _delete_tree(r"Software\MusicPlayer")
    try:
        with winreg.OpenKey(HKCU, r"Software\RegisteredApplications",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass
    _notify_shell()


# ---------- автозапуск ----------
def set_autostart(enabled: bool) -> None:
    if enabled:
        _set(RUN_KEY, APP_NAME, f"{launch_prefix()} --minimized")
    else:
        try:
            with winreg.OpenKey(HKCU, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except OSError:
            pass


def is_autostart() -> bool:
    try:
        with winreg.OpenKey(HKCU, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--register"]:
        register()
        print("Зарегистрировано.")
    elif args[:1] == ["--unregister"]:
        unregister()
        print("Удалено.")
    elif args[:1] == ["--autostart"] and len(args) == 2 and args[1] in ("on", "off"):
        set_autostart(args[1] == "on")
        print("Автозапуск:", args[1])
    else:
        print(__doc__)