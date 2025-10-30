import wx
import os
from utils import strip_html
from profile_dialog import ViewProfileDialog
from sound_lib import stream
from sound_lib import output as o
from sound_lib.main import BassError
from easysettings import EasySettings
import main_frame

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
    # Create dummy classes and functions if modules are not available
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True):
            return False

    def is_windows_dark_mode():
        return False

# --- End of Dark Mode Logic ---


out = o.Output()
if not os.path.exists("thrive.ini"):
	conf = EasySettings("thrive.ini")
	conf.setsave("soundpack", "default")
else:
	conf = EasySettings("thrive.ini")

class PostDetailsDialog(wx.Dialog):
	def __init__(self, parent, mastodon, status, me_account):
		account = status["account"]
		display_name = account.get("display_name", "")
		acct = account.get("acct", "")
		super().__init__(parent, title=f"View Post from {display_name} ({acct}) dialog", size=(600, 500))
		self.mastodon = mastodon
		self.status = status["reblog"] if status.get("reblog") else status
		self.me = me_account
		self.account = account
		self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
		self.privacy_values = ["public", "unlisted", "private", "direct"]
		
		self.panel = wx.Panel(self)
		
		# --- Conditional Dark Mode ---
		self.dark_mode_active = is_windows_dark_mode()
		if self.dark_mode_active:
			self.dark_color = wx.Colour(40, 40, 40)
			self.light_text_color = wx.WHITE
			self.dark_mode_manager = WxMswDarkMode()
			self.dark_mode_manager.enable(self)
			self.panel.SetBackgroundColour(self.dark_color)
			self.SetBackgroundColour(self.dark_color)

		content = strip_html(self.status["content"])
		self.reply_users=""
		me=self.me['acct']
		for i in content.split(" "):
			if i.startswith("@") and i!="@"+me: self.reply_users+=i+" "
		if self.account.acct!=me: self.reply_users="@"+self.account.acct+" "+self.reply_users

		self.content_label  = wx.StaticText(self.panel, label="Post C&ontent")
		self.content_box = wx.TextCtrl(self.panel, value=content, style=wx.TE_MULTILINE | wx.TE_READONLY)

		app = self.status.get("application")
		source = app["name"] if app and "name" in app else "Unknown"
		time_posted = self.status["created_at"].astimezone().strftime("%I:%M %p")
		boosts = self.status.get("reblogs_count", 0)
		favs = self.status.get("favourites_count", 0)
		replies = self.status.get("replies_count", 0)
		privacy = self.status.get("visibility", "unknown")
		language = self.status.get("language", "unknown")

		detail_text = f"""Posted: {time_posted}
From: {source}
Boosted {boosts} times
Favorited {favs} times.
{replies} replies
Privacy: {privacy}
Language: {language}"""
		self.details_label = wx.StaticText(self.panel, label="Post &Details")
		self.details_box = wx.TextCtrl(self.panel, value=detail_text, style=wx.TE_MULTILINE | wx.TE_READONLY)

		self.reply_button = wx.Button(self.panel, label="&Reply")
		self.boost_button = wx.Button(self.panel, label="Un&boost" if self.status["reblogged"] else "&Boost")
		self.fav_button = wx.Button(self.panel, label="Un&favourite" if self.status["favourited"] else "&Favourite")
		self.profile_button = wx.Button(self.panel, label=f"View &Profile of {display_name}")
		self.take_down_button = wx.Button(self.panel, label="&Take down")
		self.close_button = wx.Button(self.panel, id=wx.ID_CANCEL, label="&Close")

		# --- Apply dark theme to controls if active ---
		if self.dark_mode_active:
			for widget in [self.content_label, self.details_label]:
				widget.SetForegroundColour(self.light_text_color)
			for widget in [self.content_box, self.details_box]:
				widget.SetBackgroundColour(self.dark_color)
				widget.SetForegroundColour(self.light_text_color)
			for btn in [self.reply_button, self.boost_button, self.fav_button, self.profile_button, self.take_down_button, self.close_button]:
				btn.SetBackgroundColour(self.dark_color)
				btn.SetForegroundColour(self.light_text_color)
			
		self.reply_button.Bind(wx.EVT_BUTTON, self.reply)
		self.boost_button.Bind(wx.EVT_BUTTON, self.toggle_boost)
		self.fav_button.Bind(wx.EVT_BUTTON, self.toggle_fav)
		self.take_down_button.Bind(wx.EVT_BUTTON, self.on_take_down)
		self.profile_button.Bind(wx.EVT_BUTTON, lambda e: ViewProfileDialog(self, self.account).ShowModal())

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.content_label, 0, wx.ALL, 5)
		sizer.Add(self.content_box, 1, wx.ALL | wx.EXPAND, 5)
		sizer.Add(self.details_label, 0, wx.ALL, 5)
		sizer.Add(self.details_box, 0, wx.ALL | wx.EXPAND, 5)

		btns = wx.BoxSizer(wx.HORIZONTAL)
		btns.Add(self.reply_button, 0, wx.ALL, 5)
		btns.Add(self.boost_button, 0, wx.ALL, 5)
		btns.Add(self.fav_button, 0, wx.ALL, 5)
		if self.status['account']['id'] == self.me['id']:
			btns.Add(self.take_down_button, 0, wx.ALL, 5)
		btns.AddStretchSpacer()
		btns.Add(self.profile_button, 0, wx.ALL, 5)
		btns.Add(self.close_button, 0, wx.ALL, 5)

		sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 5)
		self.panel.SetSizer(sizer)
		
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(self.panel, 1, wx.EXPAND)
		self.SetSizer(main_sizer)

		self.setup_accelerators()

	def setup_accelerators(self):
		accel_entries = []
		accel_entries.append((wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_CANCEL))
		
		if self.status['account']['id'] == self.me['id']:
			take_down_id = wx.NewIdRef()
			self.take_down_button.SetId(take_down_id.GetId())
			accel_entries.append((wx.ACCEL_ALT, ord('T'), take_down_id.GetId()))
			self.Bind(wx.EVT_MENU, self.on_take_down, id=take_down_id.GetId())

		accel_tbl = wx.AcceleratorTable(accel_entries)
		self.SetAcceleratorTable(accel_tbl)

	def on_take_down(self, event):
		confirm = wx.MessageBox("Are you sure you want to take down this post? It will be removed from Mastodon. This action cannot be undone.", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING, self)
		if confirm == wx.YES:
			try:
				self.mastodon.status_delete(self.status['id'])
				self.Parent.handle_post_deletion(self.status['id'])
				self.Close()
			except Exception as e:
				wx.MessageBox(f"Error deleting post: {e}", "Error", wx.OK | wx.ICON_ERROR, self)

	def reply(self, event):
		dialog = wx.Dialog(self, title="Reply to Post", size=(500, 300))
		panel = wx.Panel(dialog)
		
		vbox = wx.BoxSizer(wx.VERTICAL)
		label = wx.StaticText(panel, label="&Reply")
		self.reply_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
		self.reply_text.SetValue(self.reply_users.strip() + " " if self.reply_users.strip() else "")
		self.reply_text.SetInsertionPoint(len(self.reply_text.GetValue()))
		privacy_label = wx.StaticText(panel, label="P&rivacy:")
		self.reply_privacy_choice = wx.Choice(panel, choices=self.privacy_options)
		send_button = wx.Button(panel, label="&Post")
		cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
		
		# --- Apply dark theme to reply dialog if active ---
		if self.dark_mode_active:
			dark_mode_manager = WxMswDarkMode()
			dark_mode_manager.enable(dialog)
			dialog.SetBackgroundColour(self.dark_color)
			panel.SetBackgroundColour(self.dark_color)
			for widget in [label, privacy_label]:
				widget.SetForegroundColour(self.light_text_color)
			for widget in [self.reply_text, self.reply_privacy_choice, send_button, cancel_button]:
				widget.SetBackgroundColour(self.dark_color)
				widget.SetForegroundColour(self.light_text_color)
		
		original_visibility = self.status.get("visibility", "public")
		try:
			default_index = self.privacy_values.index(original_visibility)
			self.reply_privacy_choice.SetSelection(default_index)
		except ValueError:
			self.reply_privacy_choice.SetSelection(0)
			
		send_button.Bind(wx.EVT_BUTTON, lambda e: self.send_reply(dialog, self.reply_text.GetValue()))

		vbox.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		vbox.Add(self.reply_text, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
		vbox.Add(privacy_label, 0, wx.LEFT | wx.RIGHT, 10)
		vbox.Add(self.reply_privacy_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		buttons.Add(send_button, 0, wx.ALL, 5)
		buttons.Add(cancel_button, 0, wx.ALL, 5)
		vbox.Add(buttons, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 10)

		panel.SetSizer(vbox)
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(panel, 1, wx.EXPAND)
		dialog.SetSizer(main_sizer)
		
		dialog.Bind(wx.EVT_CHAR_HOOK, self.on_reply_key_press)
		dialog.ShowModal()
		dialog.Destroy()
	
	def on_reply_key_press(self, event):
		mods = event.HasAnyModifiers()
		if event.GetKeyCode() == wx.WXK_RETURN and mods:
			self.send_reply(event.EventObject.Parent.Parent, self.reply_text.GetValue())
		else:
			event.Skip()
	
	def send_reply(self, dialog, text):
		text = text.strip()
		if not text:
			wx.MessageBox("Reply cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
			return
		try:
			selected_privacy_index = self.reply_privacy_choice.GetSelection()
			visibility = self.privacy_values[selected_privacy_index]
			self.mastodon.status_post(text, in_reply_to_id=self.status["id"], visibility=visibility)
			if main_frame.replysnd:
				main_frame.replysnd.play()
			dialog.Close()
		except Exception as e:
			wx.MessageBox(f"Error sending reply: {e}", "Error", wx.OK | wx.ICON_ERROR)

	def toggle_boost(self, event):
		try:
			if self.status["reblogged"]:
				self.mastodon.status_unreblog(self.status["id"])
				self.boost_button.SetLabel("&Boost")
			else:
				if main_frame.boostsnd:
					main_frame.boostsnd.play()
				self.mastodon.status_reblog(self.status["id"])
				self.boost_button.SetLabel("Un&boost")
			self.status["reblogged"] = not self.status["reblogged"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Boost Error")

	def toggle_fav(self, event):
		try:
			if self.status["favourited"]:
				if main_frame.unfavsnd:
					main_frame.unfavsnd.play()
				self.mastodon.status_unfavourite(self.status["id"])
				self.fav_button.SetLabel("&Favourite")
			else:
				if main_frame.favsnd:
					main_frame.favsnd.play()
				self.mastodon.status_favourite(self.status["id"])
				self.fav_button.SetLabel("Un&favourite")
			self.status["favourited"] = not self.status["favourited"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Favourite Error")