import wx
from auth import AuthFrame
from utils import load_user_data
from main_frame import ThriveFrame
from mastodon import Mastodon

# --- Dark Mode for MSW ---
try:
    import ctypes
    from ctypes import wintypes
    import winreg

    class WxMswDarkMode:
        """
        Manages dark mode for top-level windows on Microsoft Windows.
        Uses undocumented APIs for immersive dark mode, so it may break.
        """
        _instance = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = super(WxMswDarkMode, cls).__new__(cls)
                try:
                    cls.dwmapi = ctypes.WinDLL("dwmapi")
                    # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 in recent SDKs
                    cls.DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                except (AttributeError, OSError):
                    cls.dwmapi = None
            return cls._instance

        def enable(self, window: wx.Window, enable: bool = True):
            """
            Enable or disable dark mode for a given wx.Window.
            """
            if not self.dwmapi:
                return False

            try:
                hwnd = window.GetHandle()
                value = wintypes.BOOL(enable)
                hr = self.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    self.DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(value),
                    ctypes.sizeof(value)
                )
                # If attribute 20 fails, try older attribute 19 as a fallback
                if hr != 0:
                    self.DWMWA_USE_IMMERSIVE_DARK_MODE = 19
                    hr = self.dwmapi.DwmSetWindowAttribute(
                        hwnd,
                        self.DWMWA_USE_IMMERSIVE_DARK_MODE,
                        ctypes.byref(value),
                        ctypes.sizeof(value)
                    )
                return hr == 0
            except Exception:
                return False

    def is_windows_dark_mode():
        """
        Checks the Windows Registry to determine if dark mode for apps is enabled.
        Returns True if dark mode is enabled, False otherwise.
        """
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
            value, regtype = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            winreg.CloseKey(key)
            return value == 0  # 0 means dark mode is on
        except (FileNotFoundError, OSError):
            return False

except (ImportError, ModuleNotFoundError):
    # Create dummy classes and functions if modules are not available (e.g., non-Windows)
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True):
            return False

    def is_windows_dark_mode():
        return False

# --- End of Dark Mode Logic ---


class ThriveApp(wx.App):
    def OnInit(self):
        user_data = load_user_data()
        if user_data and "access_token" in user_data:
            try:
                mastodon = Mastodon(
                    client_id=user_data["client_id"],
                    client_secret=user_data["client_secret"],
                    access_token=user_data["access_token"],
                    api_base_url=user_data["instance_url"]
                )
                self.frame = ThriveFrame(None, title="Thrive Mastodon Client", mastodon=mastodon)
            except Exception as e:
                wx.MessageBox(f"Error loading saved session: {e}", "Error")
                self.frame = AuthFrame(None, title="Thrive Login")
        else:
            self.frame = AuthFrame(None, title="Thrive Login")
        self.frame.Show()
        return True

if __name__ == "__main__":
    app = ThriveApp()
    app.MainLoop()