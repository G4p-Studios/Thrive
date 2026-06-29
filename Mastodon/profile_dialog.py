import wx
import webbrowser
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
	def __init__(self, parent, account, mastodon=None, me=None):
		display_name = account.get("display_name", "")
		acct = account.get("acct", "")
		title = f"Profile for {display_name} ({acct})"
		super().__init__(parent, title=title, size=(600, 500))

		self.account = account
		self.mastodon = mastodon
		self.me = me
		self.relationship = None

		username = account.get("username", "")
		bio = strip_html(account.get("note", ""))
		followers = account.get("followers_count", 0)
		following = account.get("following_count", 0)
		statuses = account.get("statuses_count", 0)

		created = account.get("created_at")
		created_at = created.strftime("%B %d, %Y") if created else "Unknown"

		last_post = account.get("last_status_at", "") or "Unknown"
		website = account.get("url", "")
		preferences = []
		if "show_media" in account:
			preferences.append("Media tab shown" if account.get("show_media") else "Media tab hidden")
		if account.get("show_media") and "show_media_replies" in account:
			preferences.append("Media replies included" if account.get("show_media_replies") else "Media replies hidden")
		if "show_featured" in account:
			preferences.append("Featured tab shown" if account.get("show_featured") else "Featured tab hidden")
		avatar_description = account.get("avatar_description")
		header_description = account.get("header_description")

		info = f"""Display Name: {display_name}
Username: {acct}
Bio: {bio}
Followers: {followers}
Friends: {following}
Posts: {statuses}
Created: {created_at}
Last post: {last_post}
Website: {website}"""
		if avatar_description:
			info += f"\nAvatar description: {avatar_description}"
		if header_description:
			info += f"\nHeader description: {header_description}"
		if preferences:
			info += "\nProfile preferences: " + ", ".join(preferences)

		# Get relationship info
		if self.mastodon and self.me and account.get('id') != self.me.get('id'):
			try:
				rels = self.mastodon.account_relationships(account['id'])
				self.relationship = rels[0] if rels else None
				if self.relationship:
					rel_info = []
					if self.relationship.get('following'): rel_info.append("You follow this user")
					if self.relationship.get('followed_by'): rel_info.append("This user follows you")
					if self.relationship.get('blocking'): rel_info.append("You have blocked this user")
					if self.relationship.get('muting'): rel_info.append("You have muted this user")
					if self.relationship.get('requested'): rel_info.append("Follow request pending")
					if rel_info:
						info += "\n\nRelationship:\n" + "\n".join(rel_info)
			except Exception:
				pass

		self.panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)

		self.text = wx.TextCtrl(self.panel, value=info, style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.ALL | wx.EXPAND, 10)

		# Action buttons
		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		
		if self.mastodon and self.me and account.get('id') != self.me.get('id'):
			follow_label = "Un&follow" if (self.relationship and self.relationship.get('following')) else "&Follow"
			self.follow_button = wx.Button(self.panel, label=follow_label)
			self.follow_button.Bind(wx.EVT_BUTTON, self.on_follow)
			btn_sizer.Add(self.follow_button, 0, wx.ALL, 5)

			mute_label = "Un&mute" if (self.relationship and self.relationship.get('muting')) else "&Mute"
			self.mute_button = wx.Button(self.panel, label=mute_label)
			self.mute_button.Bind(wx.EVT_BUTTON, self.on_mute)
			btn_sizer.Add(self.mute_button, 0, wx.ALL, 5)

			block_label = "Un&block" if (self.relationship and self.relationship.get('blocking')) else "&Block"
			self.block_button = wx.Button(self.panel, label=block_label)
			self.block_button.Bind(wx.EVT_BUTTON, self.on_block)
			btn_sizer.Add(self.block_button, 0, wx.ALL, 5)

		self.open_url_button = wx.Button(self.panel, label="Open in &Browser")
		self.open_url_button.Bind(wx.EVT_BUTTON, self.on_open_url)
		btn_sizer.Add(self.open_url_button, 0, wx.ALL, 5)

		btn_sizer.AddStretchSpacer()
		self.close_button = wx.Button(self.panel, label="&Close", id=wx.ID_CANCEL)
		self.close_button.Bind(wx.EVT_CLOSE, lambda e: self.Close())
		btn_sizer.Add(self.close_button, 0, wx.ALL, 5)

		sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.panel.SetSizer(sizer)

		# --- Conditional Dark Mode ---
		if is_windows_dark_mode():
			dark_color = wx.Colour(40, 40, 40)
			light_text_color = wx.WHITE
			dark_mode_manager = WxMswDarkMode()
			dark_mode_manager.enable(self)

			self.SetBackgroundColour(dark_color)
			self.panel.SetBackgroundColour(dark_color)
			self.text.SetBackgroundColour(dark_color)
			self.text.SetForegroundColour(light_text_color)
			for child in self.panel.GetChildren():
				if isinstance(child, wx.Button):
					child.SetBackgroundColour(dark_color)
					child.SetForegroundColour(light_text_color)

		self.text.SetFocus()

	def on_follow(self, event):
		if not self.mastodon: return
		try:
			if self.relationship and self.relationship.get('following'):
				if wx.MessageBox(f"Unfollow {self.account.get('display_name', '')}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
					self.mastodon.account_unfollow(self.account['id'])
					self.relationship['following'] = False
					self.follow_button.SetLabel("&Follow")
			else:
				self.mastodon.account_follow(self.account['id'])
				if self.relationship: self.relationship['following'] = True
				self.follow_button.SetLabel("Un&follow")
		except Exception as e: wx.MessageBox(f"Error: {e}", "Follow Error")

	def on_mute(self, event):
		if not self.mastodon: return
		try:
			if self.relationship and self.relationship.get('muting'):
				self.mastodon.account_unmute(self.account['id'])
				self.relationship['muting'] = False
				self.mute_button.SetLabel("&Mute")
			else:
				self.mastodon.account_mute(self.account['id'])
				if self.relationship: self.relationship['muting'] = True
				self.mute_button.SetLabel("Un&mute")
		except Exception as e: wx.MessageBox(f"Error: {e}", "Mute Error")

	def on_block(self, event):
		if not self.mastodon: return
		try:
			display = self.account.get('display_name', '')
			if self.relationship and self.relationship.get('blocking'):
				if wx.MessageBox(f"Unblock {display}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
					self.mastodon.account_unblock(self.account['id'])
					self.relationship['blocking'] = False
					self.block_button.SetLabel("&Block")
			else:
				if wx.MessageBox(f"Block {display}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
					self.mastodon.account_block(self.account['id'])
					if self.relationship: self.relationship['blocking'] = True
					self.block_button.SetLabel("Un&block")
		except Exception as e: wx.MessageBox(f"Error: {e}", "Block Error")

	def on_open_url(self, event):
		url = self.account.get('url', '')
		if url:
			webbrowser.open(url)
