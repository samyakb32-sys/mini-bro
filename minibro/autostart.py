import sys

from .config import launch_command

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "MiniBro"

if sys.platform == "win32":
    import winreg

    def is_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False

    def set_enabled(enabled):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
else:
    def is_enabled():
        return False

    def set_enabled(enabled):
        pass
