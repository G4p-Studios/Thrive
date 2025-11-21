import wx
import threading
from datetime import datetime
from mastodon import StreamListener
from utils import strip_html, get_time_ago
from post_dialog import PostDetailsDialog
from profile_dialog import ViewProfileDialog
from settings_dialog import SettingsDialog
from sound_lib import stream
from sound_lib.main import BassError
from easysettings import EasySettings
import re

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


# Precompile regex for performance
_SINGULAR_RE = re.compile(r"\b1 (\w+)s( ago)?\b")

def singularize_time(text):
	"""Convert strings like '1 hours ago' -> '1 hour ago'.
	This only changes the unit when the quantity is exactly 1.
	"""
	if not text:
		return text
	return _SINGULAR_RE.sub(r"1 \1\2", text)

# Backwards-compatible helper name in case other code expects it
def formatted_time(created_at):
	"""Return human-friendly time string (backwards-compatible name)."""
	if not created_at:
		return ''
	return singularize_time(get_time_ago(created_at))

# Adapter: provide a ListBox-like API on top of a wx.ListCtrl (SysListView32)
class SysListViewAdapter(wx.ListCtrl):
	"""A thin wrapper around wx.ListCtrl that exposes simple ListBox-like methods.

	Columns: Author, Content, Time, Client
	Methods provided: Append(item), Insert(item, pos), Clear(), Delete(index), SetString(index, item), GetSelection()
	item may be a string (content) or a 4-element sequence mapping to columns.
	"""
	def __init__(self, parent, *args, **kwargs):
		super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
		# Set a comfortable row height using a dummy ImageList
		self.image_list = wx.ImageList(1, 48)
		self.AssignImageList(self.image_list, wx.IMAGE_LIST_SMALL)
		# Columns: Author, Content, Time, Client (reordered)
		self.InsertColumn(0, "Author", width=180)
		self.InsertColumn(1, "Content", width=640)
		self.InsertColumn(2, "Time", width=140)
		self.InsertColumn(3, "Client", width=140)

	def _normalize_row(self, item):
		"""Return a 4-element list for columns based on the given item."""
		if isinstance(item, (list, tuple)):
			cols = [str(c) if c is not None else "" for c in item]
			cols += [""] * (4 - len(cols))
			return cols[:4]
		# treat plain string as content only
		return ["", str(item), "", ""]

	def _insert_row(self, idx, cols):
		# insert a row and populate remaining columns
		self.InsertItem(idx, cols[0])
		for c in range(1, 4):
			self.SetItem(idx, c, cols[c])

	def Append(self, item):
		cols = self._normalize_row(item)
		idx = self.GetItemCount()
		self._insert_row(idx, cols)
		return idx

	def Insert(self, item, pos=0):
		cols = self._normalize_row(item)
		self._insert_row(pos, cols)

	def Clear(self):
		self.DeleteAllItems()

	def Delete(self, index):
		self.DeleteItem(index)

	def SetString(self, index, item):
		cols = self._normalize_row(item)
		# update all columns
		for c in range(4):
			self.SetItem(index, c, cols[c])

	def GetSelection(self):
		sel = self.GetFirstSelected()
		return sel if sel != -1 else wx.NOT_FOUND

# --- Sound loading at startup ---
try:
    conf = EasySettings("thrive.ini")
    soundpack = conf.get("soundpack", "default")
    folder = "Mastodon-" + soundpack
    tootsnd = stream.FileStream(file=f"sounds/{folder}/send_toot.wav")
except BassError:
    tootsnd = None
try:
    replysnd = stream.FileStream(file=f"sounds/{folder}/send_reply.wav")
except BassError:
    replysnd = None
try:
    boostsnd = stream.FileStream(file=f"sounds/{folder}/send_boost.wav")
except BassError:
    boostsnd = None
try:
    favsnd = stream.FileStream(file=f"sounds/{folder}/favorite.wav")
except BassError:
    favsnd = None
try:
    unfavsnd = stream.FileStream(file=f"sounds/{folder}/unfavorite.wav")
except BassError:
    unfavsnd = None
try:
    newtootsnd = stream.FileStream(file=f"sounds/{folder}/new_toot.wav")
except BassError:
    newtootsnd = None
try:
    dmsnd = stream.FileStream(file=f"sounds/{folder}/new_dm.wav")
except BassError:
    dmsnd = None
try:
    mentionsnd = stream.FileStream(file=f"sounds/{folder}/new_mention.wav")
except BassError:
    mentionsnd = None
try:
    imagesnd = stream.FileStream(file=f"sounds/{folder}/image.wav")
except BassError:
    imagesnd = None
try:
    mediasnd = stream.FileStream(file=f"sounds/{folder}/media.wav")
except BassError:
    mediasnd = None
try:
    select_mentionsnd = stream.FileStream(file=f"sounds/{folder}/mention.wav")
except BassError:
    select_mentionsnd = None
try:
    pollsnd = stream.FileStream(file=f"sounds/{folder}/poll.wav")
except BassError:
    pollsnd = None
try:
    votesnd = stream.FileStream(file=f"sounds/{folder}/vote.wav")
except BassError:
    votesnd = None

# --- Custom Stream Listener ---
class CustomStreamListener(StreamListener):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def on_update(self, status):
        wx.CallAfter(self.frame.add_new_post, status)

    def on_delete(self, status_id):
        wx.CallAfter(self.frame.handle_post_deletion, status_id)

    def on_notification(self, notification):
        wx.CallAfter(self.frame.add_notification, notification)

    def on_status_update(self, status):
        wx.CallAfter(self.frame.handle_status_update, status)


class ThriveFrame(wx.Frame):
    def __init__(self, *args, **kwargs):
        mastodon = kwargs.pop("mastodon", None)
        super().__init__(*args, **kwargs, size=(1100, 700))

        self.mastodon = mastodon
        self.me = self.mastodon.me() if self.mastodon else None
        self.timelines_data = {"home": [], "sent": [], "notifications": [], "mentions": []}
        self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
        self.privacy_values = ["public", "unlisted", "private", "direct"]
        self.poll_duration_labels = ["5 minutes", "30 minutes", "1 hour", "6 hours", "12 hours", "1 day", "3 days", "7 days"]
        self.poll_duration_seconds = [300, 1800, 3600, 21600, 43200, 86400, 259200, 604800]


        # --- UI setup ---
        self.panel = wx.Panel(self)

        # --- Conditional Dark Mode ---
        if is_windows_dark_mode():
            dark_color = wx.Colour(40, 40, 40)
            light_text_color = wx.WHITE
            dark_mode_manager = WxMswDarkMode()
            dark_mode_manager.enable(self)
            self.SetBackgroundColour(dark_color)
            self.panel.SetBackgroundColour(dark_color)
            self.panel.SetForegroundColour(light_text_color)
        
        menubar = wx.MenuBar()
        settings_menu = wx.Menu()
        settings_item = settings_menu.Append(wx.ID_ANY, "&Settings...\tAlt-S", "Open Settings")
        self.Bind(wx.EVT_MENU, self.open_settings, settings_item)
        menubar.Append(settings_menu, "&Settings")
        self.SetMenuBar(menubar)

        view_menu = wx.Menu()
        refresh_item = view_menu.Append(wx.ID_REFRESH, "&Refresh	F5", "Reload current timeline")
        self.Bind(wx.EVT_MENU, self.on_refresh, refresh_item)
        menubar.Append(view_menu, "&View")

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.toot_label = wx.StaticText(self.panel, label="&Create New Post")
        self.toot_input = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(780, 100))
        self.cw_label = wx.StaticText(self.panel, label="Content w&arning title:")
        self.cw_input = wx.TextCtrl(self.panel, size=(780, 30))
        self.cw_toggle = wx.CheckBox(self.panel, label="Add Content &Warning")
        self.cw_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_cw)
        self.cw_input.Hide()
        self.cw_label.Hide()
        vbox.Add(self.toot_label, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.toot_input, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.cw_label, 0, wx.LEFT | wx.RIGHT, 5)
        vbox.Add(self.cw_input, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.cw_toggle, 0, wx.ALL, 5)

        self.poll_toggle = wx.CheckBox(self.panel, label="Create &Poll")
        self.poll_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_poll)
        vbox.Add(self.poll_toggle, 0, wx.ALL, 5)
        
        self.poll_panel = wx.Panel(self.panel)
        poll_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.poll_panel, "Poll Options")
        
        self.poll_option_inputs = []
        for i in range(4):
            opt_label = wx.StaticText(self.poll_panel, label=f"Option {i+1}:")
            opt_input = wx.TextCtrl(self.poll_panel)
            self.poll_option_inputs.append(opt_input)
            poll_sizer.Add(opt_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
            poll_sizer.Add(opt_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        
        duration_label = wx.StaticText(self.poll_panel, label="Duration:")
        self.poll_duration_choice = wx.Choice(self.poll_panel, choices=self.poll_duration_labels)
        self.poll_duration_choice.SetSelection(5)
        
        self.poll_multiple_choice = wx.CheckBox(self.poll_panel, label="Allow multiple choices")
        
        poll_sizer.Add(duration_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        poll_sizer.Add(self.poll_duration_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        poll_sizer.Add(self.poll_multiple_choice, 0, wx.ALL, 5)

        self.poll_panel.SetSizer(poll_sizer)
        vbox.Add(self.poll_panel, 0, wx.EXPAND | wx.ALL, 5)
        self.poll_panel.Hide()

        self.privacy_label = wx.StaticText(self.panel, label="P&rivacy:")
        self.privacy_choice = wx.Choice(self.panel, choices=self.privacy_options)
        self.privacy_choice.SetSelection(0)

        self.post_button = wx.Button(self.panel, label="&Post")
        self.post_button.Bind(wx.EVT_BUTTON, self.on_post)
        self.exit_button = wx.Button(self.panel, label="E&xit")
        self.exit_button.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        self.posts_label = wx.StaticText(self.panel, label="Timelines &List")
        self.timeline_tree = wx.TreeCtrl(self.panel, style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT)
        self.root = self.timeline_tree.AddRoot("Timelines")
        self.timeline_nodes = {
            "home": self.timeline_tree.AppendItem(self.root, "Home"),
            "sent": self.timeline_tree.AppendItem(self.root, "Sent"),
            "notifications": self.timeline_tree.AppendItem(self.root, "Notifications"),
            "mentions": self.timeline_tree.AppendItem(self.root, "Mentions"),
        }
        self.timeline_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_timeline_selected)
        
        self.posts_list = SysListViewAdapter(self.panel)
        self.posts_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_post_selected)
        self.posts_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_post_activated)
        self.posts_list.Bind(wx.EVT_CONTEXT_MENU, self.on_post_context_menu)

        if is_windows_dark_mode():
            dark_color = wx.Colour(40, 40, 40)
            light_text_color = wx.WHITE
            for widget in [self.toot_label, self.cw_label, self.cw_toggle, self.poll_toggle, self.privacy_label, self.posts_label]:
                widget.SetForegroundColour(light_text_color)
                widget.SetBackgroundColour(dark_color)
            
            self.poll_panel.SetBackgroundColour(dark_color)
            poll_sizer.GetStaticBox().SetForegroundColour(light_text_color)
            for child in self.poll_panel.GetChildren():
                child.SetBackgroundColour(dark_color)
                child.SetForegroundColour(light_text_color)

            for widget in [self.toot_input, self.cw_input, self.privacy_choice, self.timeline_tree, self.posts_list, self.post_button, self.exit_button]:
                widget.SetForegroundColour(light_text_color)
                widget.SetBackgroundColour(dark_color)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(self.timeline_tree, 0, wx.EXPAND | wx.ALL, 5)
        hbox.Add(self.posts_list, 1, wx.EXPAND | wx.ALL, 5)

        vbox.Add(self.privacy_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        vbox.Add(self.privacy_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 5)
        vbox.Add(self.post_button, 0, wx.ALL | wx.CENTER, 5)
        vbox.Add(self.exit_button, 0, wx.ALL | wx.CENTER, 5)
        vbox.Add(self.posts_label, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(hbox, 1, wx.EXPAND, 0)

        self.panel.SetSizer(vbox)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.setup_accelerators()
        self.timeline_tree.SelectItem(self.timeline_nodes["home"])
        self.load_timeline("home")

        for key in ["sent", "notifications", "mentions"]:
            threading.Thread(target=lambda k=key: self.load_timeline(k), daemon=True).start()
        self.start_streaming()

    def get_selected_status(self):
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return None, None

        current_item = self.timeline_tree.GetSelection()
        key = next((k for k, v in self.timeline_nodes.items() if v == current_item), None)

        if not key:
            return None, None

        try:
            if key == "notifications":
                notification = self.timelines_data["notifications"][selection]
                return notification.get("status"), selection
            else:
                return self.timelines_data[key][selection], selection
        except (IndexError, KeyError):
            return None, None

    def on_post_context_menu(self, event):
        if self.posts_list.GetSelection() != wx.NOT_FOUND:
            self.show_context_menu()

    def show_context_menu(self):
        status, _ = self.get_selected_status()
        if not status:
            return

        menu = wx.Menu()
        
        reply_item = menu.Append(wx.ID_ANY, "&Reply\tCtrl+R")
        boost_label = "Un&boost\tCtrl+Shift+R" if status.get("reblogged") else "&Boost\tCtrl+Shift+R"
        boost_item = menu.Append(wx.ID_ANY, boost_label)
        fav_label = "Un&favourite\tCtrl+F" if status.get("favourited") else "&Favourite\tCtrl+F"
        fav_item = menu.Append(wx.ID_ANY, fav_label)
        menu.AppendSeparator()
        profile_user = status['account'].get('display_name') or status['account'].get('username')
        profile_item = menu.Append(wx.ID_ANY, f"View &Profile of {profile_user}\tCtrl+Shift+U")

        self.Bind(wx.EVT_MENU, self.on_reply, reply_item)
        self.Bind(wx.EVT_MENU, self.on_boost, boost_item)
        self.Bind(wx.EVT_MENU, self.on_favourite, fav_item)
        self.Bind(wx.EVT_MENU, self.on_view_profile, profile_item)

        self.posts_list.PopupMenu(menu)
        menu.Destroy()

    def setup_accelerators(self):
        reply_id = wx.NewIdRef()
        boost_id = wx.NewIdRef()
        fav_id = wx.NewIdRef()
        profile_id = wx.NewIdRef()

        self.Bind(wx.EVT_MENU, self.on_reply, id=reply_id)
        self.Bind(wx.EVT_MENU, self.on_boost, id=boost_id)
        self.Bind(wx.EVT_MENU, self.on_favourite, id=fav_id)
        self.Bind(wx.EVT_MENU, self.on_view_profile, id=profile_id)

        accel_tbl = wx.AcceleratorTable([
            (wx.ACCEL_CTRL, ord('R'), reply_id),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('R'), boost_id),
            (wx.ACCEL_CTRL, ord('F'), fav_id),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('U'), profile_id)
        ])
        self.SetAcceleratorTable(accel_tbl)

    def on_reply(self, event):
        status, _ = self.get_selected_status()
        if not status: return

        content_html = status["content"]
        processed_html = content_html.replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')
        content = strip_html(processed_html)
        
        reply_users = ""
        me_acct = self.me['acct']
        
        author_acct = status['account']['acct']
        if author_acct != me_acct:
            reply_users = f"@{author_acct} "
        
        for i in content.split(" "):
            if i.startswith("@") and i.strip('@') != me_acct and i.strip('@') != author_acct:
                reply_users += i + " "

        dialog = wx.Dialog(self, title="Reply to Post", size=(500, 300))
        panel = wx.Panel(dialog)
        
        vbox = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label="&Reply")
        reply_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
        reply_text.SetValue(reply_users.strip() + " ")
        reply_text.SetInsertionPointEnd()
        
        privacy_label = wx.StaticText(panel, label="P&rivacy:")
        reply_privacy_choice = wx.Choice(panel, choices=self.privacy_options)
        
        send_button = wx.Button(panel, label="&Post")
        cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        
        original_visibility = status.get("visibility", "public")
        try:
            default_index = self.privacy_values.index(original_visibility)
            reply_privacy_choice.SetSelection(default_index)
        except ValueError:
            reply_privacy_choice.SetSelection(0)
            
        def send_reply(e):
            text = reply_text.GetValue().strip()
            if not text:
                wx.MessageBox("Reply cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
                return
            try:
                visibility = self.privacy_values[reply_privacy_choice.GetSelection()]
                self.mastodon.status_post(text, in_reply_to_id=status["id"], visibility=visibility)
                if replysnd: replysnd.play()
                dialog.Close()
            except Exception as ex:
                wx.MessageBox(f"Error sending reply: {ex}", "Error", wx.OK | wx.ICON_ERROR)

        send_button.Bind(wx.EVT_BUTTON, send_reply)

        vbox.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        vbox.Add(reply_text, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        vbox.Add(privacy_label, 0, wx.LEFT | wx.RIGHT, 10)
        vbox.Add(reply_privacy_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(send_button, 0, wx.ALL, 5)
        buttons.Add(cancel_button, 0, wx.ALL, 5)
        vbox.Add(buttons, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(vbox)
        
        dialog.ShowModal()
        dialog.Destroy()

    def on_boost(self, event):
        status, index = self.get_selected_status()
        if not status: return
        try:
            if status["reblogged"]:
                self.mastodon.status_unreblog(status["id"])
            else:
                self.mastodon.status_reblog(status["id"])
                if boostsnd: boostsnd.play()
            
            status["reblogged"] = not status["reblogged"]
            self.refresh_post_in_list(status, index)
        except Exception as e:
            wx.MessageBox(f"Error: {e}", "Boost Error")

    def on_favourite(self, event):
        status, index = self.get_selected_status()
        if not status: return
        try:
            if status["favourited"]:
                self.mastodon.status_unfavourite(status["id"])
                if unfavsnd: unfavsnd.play()
            else:
                self.mastodon.status_favourite(status["id"])
                if favsnd: favsnd.play()
            
            status["favourited"] = not status["favourited"]
        except Exception as e:
            wx.MessageBox(f"Error: {e}", "Favourite Error")
    
    def on_view_profile(self, event):
        status, _ = self.get_selected_status()
        if not status: 
            return
        
        account = status.get('account')

        if account:
            dlg = ViewProfileDialog(self, account)
            dlg.ShowModal()
            dlg.Destroy()
            
    def refresh_post_in_list(self, status, index):
        current_item = self.timeline_tree.GetSelection()
        key = next((k for k, v in self.timeline_nodes.items() if v == current_item), None)
        if not key or index is None:
            return
        
        if key == "notifications":
            self.load_timeline(key)
        else:
            self.timelines_data[key][index] = status
            row = self.row_from_status(status)
            if row:
                self.posts_list.SetString(index, row)

    def open_settings(self, event):
        dlg = SettingsDialog(self, on_save_callback=self.load_sounds)
        dlg.ShowModal()
        dlg.Destroy()

    def on_toggle_cw(self, event):
        show = self.cw_toggle.IsChecked()
        self.cw_input.Show(show)
        self.cw_label.Show(show)
        self.panel.Layout()
    
    def on_toggle_poll(self, event):
        show = self.poll_toggle.IsChecked()
        self.poll_panel.Show(show)
        self.panel.Layout()

    def on_post_activated(self, event):
        self.show_post_details()

    def on_post(self, event):
        status_text = self.toot_input.GetValue().strip()
        spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
        visibility = self.privacy_values[self.privacy_choice.GetSelection()]
        
        poll_data = None
        if self.poll_toggle.IsChecked():
            options = [opt.GetValue().strip() for opt in self.poll_option_inputs if opt.GetValue().strip()]
            if len(options) < 2:
                wx.MessageBox("A poll must have at least two options.", "Poll Error", wx.OK | wx.ICON_ERROR)
                return
            
            duration_index = self.poll_duration_choice.GetSelection()
            duration = self.poll_duration_seconds[duration_index]
            multiple = self.poll_multiple_choice.IsChecked()
            poll_data = {
                'options': options,
                'expires_in': duration,
                'multiple': multiple
            }

        if not status_text and not poll_data:
            wx.MessageBox("Cannot post empty status.", "Error", wx.OK | wx.ICON_ERROR)
            return

        try:
            self.mastodon.status_post(status_text, spoiler_text=spoiler, visibility=visibility, poll=poll_data)
            if tootsnd:
                tootsnd.play()
            
            self.toot_input.SetValue("")
            self.cw_input.SetValue("")
            self.cw_toggle.SetValue(False)
            self.on_toggle_cw(None)

            if poll_data:
                self.poll_toggle.SetValue(False)
                for opt_input in self.poll_option_inputs:
                    opt_input.SetValue("")
                self.poll_duration_choice.SetSelection(5)
                self.poll_multiple_choice.SetValue(False)
                self.on_toggle_poll(None)

        except Exception as e:
            wx.MessageBox(f"Error: {e}", "Post Error")

    def on_key_press(self, event):
        mods = event.HasAnyModifiers()
        if event.GetKeyCode() == wx.WXK_DELETE and self.FindFocus() == self.posts_list:
            self.delete_selected_post()
        elif event.GetKeyCode() == wx.WXK_RETURN and self.FindFocus() == self.toot_input and mods:
            self.on_post(event)
        else:
            event.Skip()

    def delete_selected_post(self):
        status, _ = self.get_selected_status()
        if not status: return

        my_id = (self.me or {}).get('id')
        if status.get('account', {}).get('id') != my_id:
            wx.MessageBox("You can only delete your own posts.", "Error", wx.OK | wx.ICON_ERROR)
            return
        if status.get("reblog"):
            confirm = wx.MessageBox("Are you sure you want to unboost this post?", "Confirm Unboost", wx.YES_NO | wx.ICON_QUESTION)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_unreblog(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error unboosting: {e}", "Error", wx.OK | wx.ICON_ERROR)
        else:
            confirm = wx.MessageBox("Are you sure you want to delete this post?", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_delete(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error deleting post: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def start_streaming(self):
        if not self.mastodon:
            return
        listener = CustomStreamListener(self)
        threading.Thread(target=self.mastodon.stream_user, args=(listener,), daemon=True).start()

    def add_new_post(self, status):
        if status.get("visibility") == "direct":
            if dmsnd: dmsnd.play()
        elif self.me and status.get("account", {}).get("id") != self.me.get("id"):
            if newtootsnd: newtootsnd.play()
        
        self.timelines_data["home"].insert(0, status)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["home"]:
            row = self.row_from_status(status)
            if row: self.posts_list.Insert(row, 0)

    def add_notification(self, notification):
        self.timelines_data["notifications"].insert(0, notification)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["notifications"]:
            row = self.row_from_notification(notification)
            if row: self.posts_list.Insert(row, 0)

    def handle_status_update(self, status):
        for timeline in ["home", "sent", "mentions"]:
            for i, s in enumerate(self.timelines_data[timeline]):
                if s.get("id") == status.get("id"):
                    self.timelines_data[timeline][i] = status
                    if self.timeline_tree.GetSelection() == self.timeline_nodes[timeline]:
                        row = self.row_from_status(status)
                        self.posts_list.SetString(i, row)
                    break

    def handle_post_deletion(self, status_id):
        for timeline in ["home", "sent", "mentions"]:
            for i, s in enumerate(self.timelines_data[timeline]):
                if s.get("id") == status_id:
                    self.timelines_data[timeline].pop(i)
                    if self.timeline_tree.GetSelection() == self.timeline_nodes[timeline]:
                        self.posts_list.Delete(i)
                    break

    def load_timeline(self, timeline):
        wx.CallAfter(self.posts_list.Clear)
        try:
            if timeline == "home":
                statuses = self.mastodon.timeline_home(limit=40)
            elif timeline == "sent":
                statuses = self.mastodon.account_statuses(self.me["id"], limit=40)
                statuses = [s for s in statuses if not s.get("reblog")]
            elif timeline == "notifications":
                notifs = self.mastodon.notifications(limit=40)
                self.timelines_data["notifications"] = notifs
                for n in notifs:
                    row = self.row_from_notification(n)
                    if row: wx.CallAfter(self.posts_list.Append, row)
                return
            elif timeline == "mentions":
                notifs = self.mastodon.notifications(types=["mention"], limit=40)
                statuses = [n["status"] for n in notifs if n.get("status")]
            
            self.timelines_data[timeline] = statuses
            for s in statuses:
                row = self.row_from_status(s)
                if row: wx.CallAfter(self.posts_list.Append, row)
        except Exception as e:
            wx.MessageBox(f"Failed to load timeline: {e}", "Error")

    def on_timeline_selected(self, event):
        for key, node in self.timeline_nodes.items():
            if event.GetItem() == node:
                self.posts_list.Clear()
                data = self.timelines_data.get(key, [])
                for item in data:
                    row = self.row_from_notification(item) if key == "notifications" else self.row_from_status(item)
                    if row: self.posts_list.Append(row)
                break

    def on_refresh(self, event):
        current_item = self.timeline_tree.GetSelection()
        for key, node in self.timeline_nodes.items():
            if current_item == node:
                threading.Thread(target=self.load_timeline, args=(key,), daemon=True).start()
                break

    def on_post_selected(self, event):
        status, _ = self.get_selected_status()
        if not status: event.Skip(); return

        source_status = status.get('reblog') or status
        if pollsnd and source_status.get('poll'):
            pollsnd.stop(); pollsnd.play()
            event.Skip(); return
        if select_mentionsnd and self.me:
            my_id = self.me.get('id')
            if any(m.get('id') == my_id for m in source_status.get('mentions', [])):
                select_mentionsnd.stop(); select_mentionsnd.play()
                event.Skip(); return
        attachments = source_status.get('media_attachments', [])
        if not attachments: event.Skip(); return
        if mediasnd and any(att.get('type') in ['video', 'gifv', 'audio'] for att in attachments):
            mediasnd.stop(); mediasnd.play()
            event.Skip(); return
        if imagesnd and any(att.get('type') == 'image' for att in attachments):
            imagesnd.stop(); imagesnd.play()
            event.Skip(); return
        event.Skip()

    def conf(self):
        return EasySettings("thrive.ini")

    def load_sounds(self):
        global tootsnd, replysnd, boostsnd, favsnd, unfavsnd, newtootsnd, dmsnd, mentionsnd, imagesnd, mediasnd, select_mentionsnd, pollsnd, votesnd
        try:
            soundpack = self.conf().get("soundpack", "default")
            folder = "Mastodon-" + soundpack
        except Exception:
            folder = "Mastodon-default"

        def _safe_load(path):
            try:
                return stream.FileStream(file=path)
            except BassError:
                return None

        tootsnd = _safe_load(f"sounds/{folder}/send_toot.wav")
        replysnd = _safe_load(f"sounds/{folder}/send_reply.wav")
        boostsnd = _safe_load(f"sounds/{folder}/send_boost.wav")
        favsnd = _safe_load(f"sounds/{folder}/favorite.wav")
        unfavsnd = _safe_load(f"sounds/{folder}/unfavorite.wav")
        newtootsnd = _safe_load(f"sounds/{folder}/new_toot.wav")
        dmsnd = _safe_load(f"sounds/{folder}/new_dm.wav")
        mentionsnd = _safe_load(f"sounds/{folder}/new_mention.wav")
        imagesnd = _safe_load(f"sounds/{folder}/image.wav")
        mediasnd = _safe_load(f"sounds/{folder}/media.wav")
        select_mentionsnd = _safe_load(f"sounds/{folder}/mention.wav")
        pollsnd = _safe_load(f"sounds/{folder}/poll.wav")
        votesnd = _safe_load(f"sounds/{folder}/vote.wav")
        return True

    def format_notification_for_display(self, notification):
        ntype = notification.get("type")
        account = notification.get("account", {})
        user = account.get("display_name") or account.get("username", "Unknown")
        status = notification.get("status")
        
        def _prepare_content(html):
            if not html: return ""
            processed = html.replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')
            return strip_html(processed).strip()

        if ntype == "favourite" and status:
            if favsnd: favsnd.play()
            return f"{user} favourited your post: {_prepare_content(status['content'])}"
        elif ntype == "reblog" and status:
            if boostsnd: boostsnd.play()
            return f"{user} boosted your post: {_prepare_content(status['content'])}"
        elif ntype == "mention" and status:
            if mentionsnd: mentionsnd.play()
            return f"{user} mentioned you: {_prepare_content(status['content'])}"
        elif ntype == "poll" and status and status.get("poll") and status["poll"].get("expired"):
            if newtootsnd: newtootsnd.play()
            return f"Poll ended in {user}'s post: {_prepare_content(status['content'])}"
        elif ntype == "update" and status:
            if newtootsnd: newtootsnd.play()
            return f"{user}'s post you interacted with was edited: {_prepare_content(status['content'])}"
        else:
            return f"{user}: {ntype}"

    def format_status_for_display(self, status):
        def _prepare_content(html):
            if not html: return ""
            processed = html.replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')
            return strip_html(processed).strip()

        if status.get("reblog"):
            boost = status["reblog"]
            user = status["account"].get("display_name") or status["account"].get("username")
            original_user = boost["account"].get("display_name") or boost["account"].get("username")
            handle = boost["account"].get("acct", "")
            content = _prepare_content(boost.get("content", ""))
            app = boost.get("application") or {}
            source = app.get("name") if isinstance(app, dict) else "Unknown"
            if boost.get("spoiler_text"):
                display = f"{user}: Content warning: {boost['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: Boosting {original_user} ({handle}): {content}"
            display += f" — {singularize_time(get_time_ago(boost.get('created_at')))}, {source}"
            return display
        else:
            user = status["account"].get("display_name") or status["account"].get("username")
            content = _prepare_content(status.get("content", ""))
            app = status.get("application") or {}
            source = app.get("name") if isinstance(app, dict) else "Unknown"
            if status.get("spoiler_text"):
                display = f"{user}: Content warning: {status['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: {content}"
            display += f" — {singularize_time(get_time_ago(status.get('created_at')))}, {source}"
            return display

    def row_from_status(self, status):
        if not status:
            return None

        is_boost = bool(status.get('reblog'))
        source_obj = status['reblog'] if is_boost else status
        author_cell = status['account'].get('display_name') or status['account'].get('username')
        
        def _prepare_content(html):
            if not html: return ""
            processed = html.replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')
            return strip_html(processed).strip()

        if is_boost:
            original = source_obj['account'].get('display_name') or source_obj['account'].get('username')
            handle = source_obj['account'].get('acct', '')
            if source_obj.get('spoiler_text'):
                content_body = f"CW: {source_obj['spoiler_text']} (press Enter to view)"
            else:
                content_body = _prepare_content(source_obj.get('content', ''))
            content_cell = f"boosting {original} ({handle}): {content_body}"
        else:
            if status.get('spoiler_text'):
                content_cell = f"CW: {status['spoiler_text']} (press Enter to view)"
            else:
                content_cell = _prepare_content(status.get('content', ''))
        
        if source_obj.get('poll'):
            content_cell += " [Poll]"

        time_cell = self.format_time(source_obj.get('created_at')) or ''
        client_cell = self.get_app_name(source_obj) or ''
        return [author_cell or '', content_cell or '', time_cell, client_cell]

    def row_from_notification(self, notification):
        ntype = notification.get('type')
        account = notification.get('account', {})
        user = account.get('display_name') or account.get('username') or 'Unknown'
        status = notification.get('status') or {}
        
        def _prepare_content(html):
            if not html: return ""
            processed = html.replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')
            return strip_html(processed).strip()

        time_cell = self.format_time(status.get('created_at')) if status else ''
        client_cell = self.get_app_name(status) if status else ''
        content = _prepare_content(status.get('content', '')) if status else ''
        if status.get('poll'):
            content += " [Poll]"

        if ntype == 'favourite':
            return [f"{user} favorited", content, time_cell or '', client_cell or '']
        if ntype == 'reblog':
            return [f"{user} boosted", content, time_cell or '', client_cell or '']
        if ntype == 'mention':
            return [f"{user} mentioned you", content, time_cell or '', client_cell or '']
        return [f"{user}: {ntype}", '', '', '']

    def show_post_details(self):
        status, _ = self.get_selected_status()
        if not status: return

        dlg = PostDetailsDialog(self, self.mastodon, status, self.me, votesnd=votesnd)
        dlg.ShowModal()
        dlg.Destroy()

    def get_app_name(self, status_or_boost):
        if not status_or_boost:
            return 'Unknown'
        app = status_or_boost.get('application') or {}
        if isinstance(app, dict):
            return app.get('name', 'Unknown')
        return str(app) or 'Unknown'

    def format_time(self, created_at):
        if not created_at:
            return ''
        return singularize_time(get_time_ago(created_at))