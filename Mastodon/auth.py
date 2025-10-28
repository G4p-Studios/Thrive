import wx
import webbrowser
from mastodon import Mastodon
from utils import save_user_data
from main_frame import ThriveFrame

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

class AuthFrame(wx.Frame):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs, size=(400, 180))
		self.panel = wx.Panel(self)
		
		# --- Conditional Dark Mode ---
		self.dark_mode_active = is_windows_dark_mode()
		if self.dark_mode_active:
			dark_color = wx.Colour(40, 40, 40) # Corresponds to #282828
			light_text_color = wx.WHITE

			dark_mode_manager = WxMswDarkMode()
			dark_mode_manager.enable(self)

			self.SetBackgroundColour(dark_color)
			self.panel.SetBackgroundColour(dark_color)
			self.panel.SetForegroundColour(light_text_color)
		
		vbox = wx.BoxSizer(wx.VERTICAL)

		self.instance_label = wx.StaticText(self.panel, label="Mastodon Instance URL:")
		self.instance_input = wx.TextCtrl(self.panel, value="https://vee.seedy.cc")

		# Apply dark theme to widgets if active
		if self.dark_mode_active:
			light_text_color = wx.WHITE
			dark_color = wx.Colour(40, 40, 40)
			self.instance_label.SetForegroundColour(light_text_color)
			self.instance_input.SetBackgroundColour(dark_color)
			self.instance_input.SetForegroundColour(light_text_color)
		
		self.auth_button = wx.Button(self.panel, label="Authenticate")
		self.auth_button.Bind(wx.EVT_BUTTON, self.on_authenticate)

		vbox.Add(self.instance_label, 0, wx.ALL, 5)
		vbox.Add(self.instance_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.auth_button, 0, wx.ALL | wx.CENTER, 5)
		self.panel.SetSizer(vbox)

	def on_authenticate(self, event):
		instance_url = self.instance_input.GetValue().strip()
		if not instance_url:
			wx.MessageBox("Please enter the instance URL.", "Error")
			return

		try:
			client_id, client_secret = Mastodon.create_app(
				"Thrive",
				api_base_url=instance_url,
				scopes=["read", "write", "follow"]
			)

			self.mastodon = Mastodon(
				client_id=client_id,
				client_secret=client_secret,
				api_base_url=instance_url
			)

			auth_url = self.mastodon.auth_request_url(scopes=["read", "write", "follow"])
			webbrowser.open(auth_url)

			code = wx.GetTextFromUser("Enter the authorization code from the browser:", "Enter Code")
			if not code:
				return # User cancelled
			self.mastodon.log_in(code=code, scopes=["read", "write", "follow"])

			me_account = self.mastodon.me()
			username = me_account['username']

			save_user_data({
				"client_id": client_id,
				"client_secret": client_secret,
				"access_token": self.mastodon.access_token,
				"instance_url": instance_url,
				"username": username
			})

			ThriveFrame(None, title="Thrive Mastodon Client", mastodon=self.mastodon).Show()
			self.Close()

		except Exception as e:
			wx.MessageBox(f"Authentication failed: {e}", "Error")