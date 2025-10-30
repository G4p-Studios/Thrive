import wx
from utils import strip_html

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
            # Key or value may not exist, assume light mode
            return False

except (ImportError, ModuleNotFoundError):
    # Create dummy classes and functions if modules are not available (e.g., non-Windows)
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True):
            return False
            
    def is_windows_dark_mode():
        return False

# --- End of Dark Mode Logic ---

class ViewProfileDialog(wx.Dialog):
	def __init__(self, parent, account):
		display_name = account.get("display_name", "")
		acct = account.get("acct", "")
		title = f"Profile for {display_name} ({acct})"
		super().__init__(parent, title=title, size=(600, 400))

		username = account.get("username", "")
		bio = strip_html(account.get("note", ""))
		followers = account.get("followers_count", 0)
		following = account.get("following_count", 0)
		statuses = account.get("statuses_count", 0)

		created = account.get("created_at")
		created_at = created.strftime("%B %d, %Y") if created else "Unknown"

		last_post = account.get("last_status_at", "") or "Unknown"
		website = account.get("url", "")

		info = f"""Display Name: {display_name}
Username: {acct}
Bio: {bio}
Followers: {followers}
Friends: {following}
Posts: {statuses}
Created: {created_at}
Last post: {last_post}
Website: {website}"""

		self.text = wx.TextCtrl(self, value=info, style=wx.TE_MULTILINE | wx.TE_READONLY)
		self.close_button = wx.Button(self, label="&Close",id=wx.ID_CANCEL)
		self.close_button.Bind(wx.EVT_CLOSE, lambda e: self.Close())

		# --- Conditional Dark Mode ---
		if is_windows_dark_mode():
			dark_color = wx.Colour(40, 40, 40)
			light_text_color = wx.WHITE
			dark_mode_manager = WxMswDarkMode()
			dark_mode_manager.enable(self)

			self.SetBackgroundColour(dark_color)
			self.text.SetBackgroundColour(dark_color)
			self.text.SetForegroundColour(light_text_color)
			self.close_button.SetBackgroundColour(dark_color)
			self.close_button.SetForegroundColour(light_text_color)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.text, 1, wx.ALL | wx.EXPAND, 10)
		sizer.Add(self.close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

		self.SetSizer(sizer)
		self.text.SetFocus()