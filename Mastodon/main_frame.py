import wx
import os
import threading
import webbrowser
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
import queue
import io
import urllib.request

try:
    import pyperclip
except ImportError:
    pyperclip = None

# --- Dark Mode for MSW ---
try:
    import ctypes
    from ctypes import wintypes
    import winreg

    class WxMswDarkMode:
        _instance = None
        def __new__(cls):
            if cls._instance is None:
                cls._instance = super(WxMswDarkMode, cls).__new__(cls)
                try:
                    cls.dwmapi = ctypes.WinDLL("dwmapi")
                    cls.DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                except (AttributeError, OSError):
                    cls.dwmapi = None
            return cls._instance

        def enable(self, window: wx.Window, enable: bool = True):
            if not self.dwmapi: return False
            try:
                hwnd = window.GetHandle()
                value = wintypes.BOOL(enable)
                hr = self.dwmapi.DwmSetWindowAttribute(hwnd, cls.DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
                if hr != 0:
                    cls.DWMWA_USE_IMMERSIVE_DARK_MODE = 19
                    hr = self.dwmapi.DwmSetWindowAttribute(hwnd, cls.DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
                return hr == 0
            except Exception: return False

    def is_windows_dark_mode():
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            winreg.CloseKey(key)
            return value == 0
        except (FileNotFoundError, OSError): return False

except (ImportError, ModuleNotFoundError):
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True): return False
    def is_windows_dark_mode(): return False

# --- End of Dark Mode Logic ---

class UserSelectionDialog(wx.Dialog):
    def __init__(self, parent, accounts):
        super().__init__(parent, title="Select a User to View")
        self.accounts = accounts
        self.selected_account = None

        vbox = wx.BoxSizer(wx.VERTICAL)
        choices = []
        for acc in self.accounts:
            display_name = acc.get('display_name') or acc.get('username', 'Unknown')
            acct = acc.get('acct', 'unknown_user')
            choices.append(f"{display_name} (@{acct})")

        self.user_list = wx.ListBox(self, choices=choices, style=wx.LB_SINGLE)
        vbox.Add(self.user_list, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        ok_button = self.FindWindowById(wx.ID_OK)
        if ok_button:
            ok_button.SetLabel("&View")
        vbox.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        self.SetSizer(vbox)
        self.SetMinSize((450, 300))
        self.CentreOnParent()

        if self.accounts:
            self.user_list.SetSelection(0)

        if is_windows_dark_mode():
            dark_color = wx.Colour(40, 40, 40)
            light_text_color = wx.WHITE
            WxMswDarkMode().enable(self)
            self.SetBackgroundColour(dark_color)
            self.user_list.SetBackgroundColour(dark_color)
            self.user_list.SetForegroundColour(light_text_color)
            if ok_button:
                ok_button.SetBackgroundColour(dark_color)
                ok_button.SetForegroundColour(light_text_color)
            cancel_button = self.FindWindowById(wx.ID_CANCEL)
            if cancel_button:
                cancel_button.SetBackgroundColour(dark_color)
                cancel_button.SetForegroundColour(light_text_color)

        self.user_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_ok)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    def on_ok(self, event):
        selection = self.user_list.GetSelection()
        if selection != wx.NOT_FOUND:
            self.selected_account = self.accounts[selection]
        self.EndModal(wx.ID_OK)

    def get_selected_account(self):
        return self.selected_account

_SINGULAR_RE = re.compile(r"\b1 (\w+)s( ago)?\b")

def singularize_time(text):
	if not text: return text
	return _SINGULAR_RE.sub(r"1 \1\2", text)

def formatted_time(created_at):
	if not created_at: return ''
	return singularize_time(get_time_ago(created_at))

class SysListViewAdapter(wx.ListCtrl):
	def __init__(self, parent, *args, **kwargs):
		super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
		self.image_list = wx.ImageList(48, 48)
		empty_bitmap = wx.Bitmap(48, 48)
		empty_bitmap.SetMaskColour(wx.BLACK)
		self.image_list.Add(empty_bitmap)
		self.AssignImageList(self.image_list, wx.IMAGE_LIST_SMALL)
		
		self.avatar_map = {}
		self.item_avatar_map = {}
		
		self.InsertColumn(0, "Author", width=180)
		self.InsertColumn(1, "Content", width=640)
		self.InsertColumn(2, "Time", width=140)
		self.InsertColumn(3, "Client", width=140)

	def _normalize_row(self, item):
		if isinstance(item, (list, tuple)):
			cols = [str(c) if c is not None else "" for c in item]
			cols += [""] * (4 - len(cols))
			return cols[:4]
		return ["", str(item), "", ""]

	def _insert_row(self, idx, cols, avatar_url=None):
		image_idx = self.avatar_map.get(avatar_url, 0)
		self.InsertItem(idx, cols[0], image_idx)
		for c in range(1, 4): self.SetItem(idx, c, cols[c])
		if avatar_url:
			self.item_avatar_map[idx] = avatar_url

	def Append(self, item, avatar_url=None):
		idx = self.GetItemCount()
		self._insert_row(idx, self._normalize_row(item), avatar_url)
		return idx

	def Insert(self, item, pos=0, avatar_url=None):
		self._insert_row(pos, self._normalize_row(item), avatar_url)

	def Clear(self):
		self.DeleteAllItems()
		self.item_avatar_map.clear()

	def Delete(self, index):
		self.DeleteItem(index)
		new_map = {}
		for i in range(self.GetItemCount()):
			if i in self.item_avatar_map:
				new_map[i] = self.item_avatar_map.get(i)
		self.item_avatar_map = new_map
		
	def SetString(self, index, item, avatar_url=None):
		cols = self._normalize_row(item)
		image_idx = self.avatar_map.get(avatar_url, 0)
		self.SetItem(index, 0, cols[0], image_idx)
		for c in range(1, 4): self.SetItem(index, c, cols[c])
		if avatar_url:
			self.item_avatar_map[index] = avatar_url

	def GetSelection(self):
		sel = self.GetFirstSelected()
		return sel if sel != -1 else wx.NOT_FOUND

	def update_avatars_for_url(self, url, bitmap):
		if url in self.avatar_map:
			return
		
		image_idx = self.image_list.Add(bitmap)
		self.avatar_map[url] = image_idx

		for idx, item_url in self.item_avatar_map.items():
			if item_url == url:
				self.SetItemImage(idx, image_idx)

sound_files = {
    "tootsnd": "send_toot.wav", "replysnd": "send_reply.wav", "boostsnd": "send_boost.wav",
    "favsnd": "favorite.wav", "unfavsnd": "unfavorite.wav", "newtootsnd": "new_toot.wav",
    "dmsnd": "new_dm.wav", "mentionsnd": "new_mention.wav", "imagesnd": "image.wav",
    "mediasnd": "media.wav", "select_mentionsnd": "mention.wav", "pollsnd": "poll.wav",
    "votesnd": "vote.wav", "notificationsnd": "new_notification.wav"
}
for name in sound_files: globals()[name] = None

def load_sounds_globally():
    try:
        conf = EasySettings("thrive.ini")
        soundpack = conf.get("soundpack", "default")
        folder = f"sounds/Mastodon-{soundpack}"
        for name, filename in sound_files.items():
            try:
                globals()[name] = stream.FileStream(file=f"{folder}/{filename}")
            except BassError as e:
                print(f"BASS error loading sound '{filename}': {e}")
                globals()[name] = None
    except Exception as e:
        print(f"General error loading sounds: {e}")
        wx.MessageBox(f"An error occurred while loading sounds: {e}\n\nPlease check your 'sounds' directory and configuration.", "Sound Loading Error", wx.OK | wx.ICON_ERROR)

load_sounds_globally()

class CustomStreamListener(StreamListener):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame
    def on_update(self, status): wx.CallAfter(self.frame.add_new_post, status)
    def on_delete(self, status_id): wx.CallAfter(self.frame.handle_post_deletion, status_id)
    def on_notification(self, notification): wx.CallAfter(self.frame.add_notification, notification)
    def on_status_update(self, status): wx.CallAfter(self.frame.handle_status_update, status)

class ThriveFrame(wx.Frame):
    def __init__(self, *args, **kwargs):
        mastodon = kwargs.pop("mastodon", None)
        super().__init__(*args, **kwargs, size=(1100, 700))

        self.mastodon = mastodon
        self.me = self.mastodon.me() if self.mastodon else None
        self.timelines_data = {"home": [], "local": [], "federated": [], "sent": [], "direct_messages": [], "favourites": [], "bookmarks": [], "notifications": [], "mentions": []}
        self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
        self.privacy_values = ["public", "unlisted", "private", "direct"]
        self.poll_duration_labels = ["5 minutes", "30 minutes", "1 hour", "6 hours", "12 hours", "1 day", "3 days", "7 days"]
        self.poll_duration_seconds = [300, 1800, 3600, 21600, 43200, 86400, 259200, 604800]
        self.show_avatars = False
        
        self.image_cache = {}
        self.image_download_queue = queue.Queue()
        self.pending_downloads = set()
        threading.Thread(target=self.image_downloader_worker, daemon=True).start()

        self.panel = wx.Panel(self)
        if is_windows_dark_mode():
            dark_color = wx.Colour(40, 40, 40)
            light_text_color = wx.WHITE
            WxMswDarkMode().enable(self)
            self.SetBackgroundColour(dark_color)
            self.panel.SetBackgroundColour(dark_color)
            self.panel.SetForegroundColour(light_text_color)
        
        menubar = wx.MenuBar()
        settings_menu = wx.Menu()
        settings_item = settings_menu.Append(wx.ID_ANY, "&Settings...\tAlt-S", "Open Settings")
        self.Bind(wx.EVT_MENU, self.open_settings, settings_item)
        menubar.Append(settings_menu, "&Settings")
        view_menu = wx.Menu()
        refresh_item = view_menu.Append(wx.ID_REFRESH, "&Refresh	F5", "Reload current timeline")
        self.Bind(wx.EVT_MENU, self.on_refresh, refresh_item)
        view_menu.AppendSeparator()
        self.show_avatars_item = view_menu.Append(wx.ID_ANY, "Show Profile Pictures", "Toggle display of profile pictures", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.on_toggle_show_avatars, self.show_avatars_item)
        view_menu.AppendSeparator()
        followers_item = view_menu.Append(wx.ID_ANY, "My &Followers", "View your followers")
        following_item = view_menu.Append(wx.ID_ANY, "My F&ollowing", "View who you follow")
        blocked_item = view_menu.Append(wx.ID_ANY, "&Blocked Users", "View blocked users")
        muted_item = view_menu.Append(wx.ID_ANY, "M&uted Users", "View muted users")
        follow_requests_item = view_menu.Append(wx.ID_ANY, "Follow &Requests", "View pending follow requests")
        self.Bind(wx.EVT_MENU, self.on_view_followers, followers_item)
        self.Bind(wx.EVT_MENU, self.on_view_following, following_item)
        self.Bind(wx.EVT_MENU, self.on_view_blocked, blocked_item)
        self.Bind(wx.EVT_MENU, self.on_view_muted, muted_item)
        self.Bind(wx.EVT_MENU, self.on_view_follow_requests, follow_requests_item)
        menubar.Append(view_menu, "&View")
        
        actions_menu = wx.Menu()
        reply_menu_item = actions_menu.Append(wx.ID_ANY, "&Reply\tCtrl+R")
        boost_menu_item = actions_menu.Append(wx.ID_ANY, "&Boost\tCtrl+Shift+R")
        fav_menu_item = actions_menu.Append(wx.ID_ANY, "&Favourite\tCtrl+F")
        bookmark_menu_item = actions_menu.Append(wx.ID_ANY, "Boo&kmark\tCtrl+B")
        actions_menu.AppendSeparator()
        copy_menu_item = actions_menu.Append(wx.ID_ANY, "&Copy Post Text\tCtrl+Shift+C")
        open_url_menu_item = actions_menu.Append(wx.ID_ANY, "&Open Post URL\tCtrl+Shift+O")
        view_thread_menu_item = actions_menu.Append(wx.ID_ANY, "View &Thread\tCtrl+T")
        actions_menu.AppendSeparator()
        follow_menu_item = actions_menu.Append(wx.ID_ANY, "Fo&llow/Unfollow User\tCtrl+L")
        block_menu_item = actions_menu.Append(wx.ID_ANY, "B&lock/Unblock User\tCtrl+Shift+B")
        mute_menu_item = actions_menu.Append(wx.ID_ANY, "&Mute/Unmute User\tCtrl+Shift+M")
        actions_menu.AppendSeparator()
        edit_menu_item = actions_menu.Append(wx.ID_ANY, "&Edit Post\tCtrl+E")
        pin_menu_item = actions_menu.Append(wx.ID_ANY, "&Pin/Unpin Post\tCtrl+P")
        actions_menu.AppendSeparator()
        profile_menu_item = actions_menu.Append(wx.ID_ANY, "View &User Profile\tCtrl+Shift+U")
        search_menu_item = actions_menu.Append(wx.ID_ANY, "&Search\tCtrl+Shift+S")
        user_timeline_menu_item = actions_menu.Append(wx.ID_ANY, "Open User Time&line\tCtrl+Shift+L")
        
        self.Bind(wx.EVT_MENU, self.on_reply, reply_menu_item)
        self.Bind(wx.EVT_MENU, self.on_boost, boost_menu_item)
        self.Bind(wx.EVT_MENU, self.on_favourite, fav_menu_item)
        self.Bind(wx.EVT_MENU, self.on_bookmark, bookmark_menu_item)
        self.Bind(wx.EVT_MENU, self.on_copy_post, copy_menu_item)
        self.Bind(wx.EVT_MENU, self.on_open_post_url, open_url_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_thread, view_thread_menu_item)
        self.Bind(wx.EVT_MENU, self.on_follow_user, follow_menu_item)
        self.Bind(wx.EVT_MENU, self.on_block_user, block_menu_item)
        self.Bind(wx.EVT_MENU, self.on_mute_user, mute_menu_item)
        self.Bind(wx.EVT_MENU, self.on_edit_post, edit_menu_item)
        self.Bind(wx.EVT_MENU, self.on_pin_post, pin_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_profile, profile_menu_item)
        self.Bind(wx.EVT_MENU, self.on_search, search_menu_item)
        self.Bind(wx.EVT_MENU, self.on_open_user_timeline, user_timeline_menu_item)
        menubar.Append(actions_menu, "&Actions")
        
        self.SetMenuBar(menubar)

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
        
        # --- Poll UI Refactor to fix Parent Assertion ---
        self.poll_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.panel, "Poll Options")
        self.poll_widgets = []

        self.poll_option_inputs = []
        for i in range(4):
            opt_label = wx.StaticText(self.panel, label=f"Option {i+1}:")
            opt_input = wx.TextCtrl(self.panel)
            self.poll_option_inputs.append(opt_input)
            self.poll_sizer.Add(opt_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
            self.poll_sizer.Add(opt_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
            self.poll_widgets.extend([opt_label, opt_input])

        duration_label = wx.StaticText(self.panel, label="Duration:")
        self.poll_duration_choice = wx.Choice(self.panel, choices=self.poll_duration_labels)
        self.poll_duration_choice.SetSelection(5)
        self.poll_multiple_choice = wx.CheckBox(self.panel, label="Allow multiple choices")

        self.poll_sizer.Add(duration_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        self.poll_sizer.Add(self.poll_duration_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.poll_sizer.Add(self.poll_multiple_choice, 0, wx.ALL, 5)
        self.poll_widgets.extend([duration_label, self.poll_duration_choice, self.poll_multiple_choice])

        vbox.Add(self.poll_sizer, 0, wx.EXPAND | wx.ALL, 5)

        for widget in self.poll_widgets:
            widget.Hide()
        self.poll_sizer.Show(False)

        # Media attachment UI
        self.media_toggle = wx.CheckBox(self.panel, label="Add M&edia")
        self.media_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_media)
        vbox.Add(self.media_toggle, 0, wx.ALL, 5)

        self.media_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.panel, "Media Attachments")
        self.media_files = []
        self.media_list = wx.ListBox(self.panel, style=wx.LB_SINGLE, size=(-1, 60))
        self.add_media_button = wx.Button(self.panel, label="Add &Media...")
        self.remove_media_button = wx.Button(self.panel, label="Re&move Media")
        self.alt_text_label = wx.StaticText(self.panel, label="Alt te&xt for selected:")
        self.alt_text_input = wx.TextCtrl(self.panel, size=(-1, 30))
        media_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        media_btn_sizer.Add(self.add_media_button, 0, wx.ALL, 3)
        media_btn_sizer.Add(self.remove_media_button, 0, wx.ALL, 3)
        self.media_sizer.Add(self.media_list, 0, wx.EXPAND | wx.ALL, 5)
        self.media_sizer.Add(media_btn_sizer, 0, wx.ALL, 2)
        self.media_sizer.Add(self.alt_text_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        self.media_sizer.Add(self.alt_text_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.media_widgets = [self.media_list, self.add_media_button, self.remove_media_button, self.alt_text_label, self.alt_text_input]
        
        self.add_media_button.Bind(wx.EVT_BUTTON, self.on_add_media)
        self.remove_media_button.Bind(wx.EVT_BUTTON, self.on_remove_media)
        self.media_list.Bind(wx.EVT_LISTBOX, self.on_media_selected)
        self.alt_text_input.Bind(wx.EVT_TEXT, self.on_alt_text_changed)
        
        vbox.Add(self.media_sizer, 0, wx.EXPAND | wx.ALL, 5)

        for widget in self.media_widgets:
            widget.Hide()
        self.media_sizer.Show(False)

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
            "local": self.timeline_tree.AppendItem(self.root, "Local"),
            "federated": self.timeline_tree.AppendItem(self.root, "Federated"),
            "sent": self.timeline_tree.AppendItem(self.root, "Sent"),
            "direct_messages": self.timeline_tree.AppendItem(self.root, "Direct Messages"),
            "favourites": self.timeline_tree.AppendItem(self.root, "Favourites"),
            "bookmarks": self.timeline_tree.AppendItem(self.root, "Bookmarks"),
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
            # Updated to include self.poll_widgets
            for widget in [self.toot_label, self.cw_label, self.cw_toggle, self.poll_toggle, self.media_toggle, self.privacy_label, self.posts_label, *self.poll_widgets, self.alt_text_label]:
                widget.SetForegroundColour(light_text_color)
                widget.SetBackgroundColour(dark_color)
            self.poll_sizer.GetStaticBox().SetForegroundColour(light_text_color)
            self.media_sizer.GetStaticBox().SetForegroundColour(light_text_color)
            for widget in [self.toot_input, self.cw_input, self.privacy_choice, self.timeline_tree, self.posts_list, self.post_button, self.exit_button, *self.media_widgets]:
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
        for key in self.timelines_data.keys():
            threading.Thread(target=lambda k=key: self.load_timeline(k), daemon=True).start()

        self.start_streaming()

    def image_downloader_worker(self):
        while True:
            url = self.image_download_queue.get()
            if url is None:
                break
            
            bitmap = None
            try:
                with urllib.request.urlopen(url) as response:
                    image_data = response.read()
                
                stream = io.BytesIO(image_data)
                image = wx.Image(stream)
                image.Rescale(48, 48, wx.IMAGE_QUALITY_HIGH)
                bitmap = wx.Bitmap(image)
            except Exception as e:
                print(f"Failed to download image {url}: {e}")
            
            wx.CallAfter(self.on_image_downloaded, url, bitmap)
            self.image_download_queue.task_done()
            self.pending_downloads.remove(url)

    def on_image_downloaded(self, url, bitmap):
        if bitmap:
            self.image_cache[url] = bitmap
            self.posts_list.update_avatars_for_url(url, bitmap)
        else:
            self.image_cache[url] = None

    def queue_avatar_download(self, url):
        if self.show_avatars and url and url not in self.image_cache and url not in self.pending_downloads:
            self.pending_downloads.add(url)
            self.image_download_queue.put(url)

    def on_toggle_show_avatars(self, event):
        self.show_avatars = self.show_avatars_item.IsChecked()
        self.on_refresh(event)

    def get_selected_status(self):
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND: return None, None
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if not key: return None, None
        try:
            item = self.timelines_data[key][selection]
            status = item.get("status") if key == "notifications" else item
            return status, selection
        except (IndexError, KeyError): return None, None

    def on_post_context_menu(self, event):
        if self.posts_list.GetSelection() != wx.NOT_FOUND: self.show_context_menu()

    def show_context_menu(self):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        menu = wx.Menu()
        reply_item = menu.Append(wx.ID_ANY, "&Reply\tCtrl+R")
        boost_label = "Un&boost\tCtrl+Shift+R" if status.get("reblogged") else "&Boost\tCtrl+Shift+R"
        boost_item = menu.Append(wx.ID_ANY, boost_label)
        fav_label = "Un&favourite\tCtrl+F" if status.get("favourited") else "&Favourite\tCtrl+F"
        fav_item = menu.Append(wx.ID_ANY, fav_label)
        bookmark_label = "Un&bookmark\tCtrl+B" if source.get("bookmarked") else "Boo&kmark\tCtrl+B"
        bookmark_item = menu.Append(wx.ID_ANY, bookmark_label)
        menu.AppendSeparator()
        copy_item = menu.Append(wx.ID_ANY, "&Copy Post Text\tCtrl+Shift+C")
        open_url_item = menu.Append(wx.ID_ANY, "&Open Post URL\tCtrl+Shift+O")
        thread_item = menu.Append(wx.ID_ANY, "View &Thread\tCtrl+T")
        menu.AppendSeparator()
        follow_item = menu.Append(wx.ID_ANY, "Fo&llow/Unfollow User\tCtrl+L")
        block_item = menu.Append(wx.ID_ANY, "B&lock/Unblock User\tCtrl+Shift+B")
        mute_item = menu.Append(wx.ID_ANY, "&Mute/Unmute User\tCtrl+Shift+M")
        menu.AppendSeparator()
        if source.get('account', {}).get('id') == (self.me or {}).get('id'):
            edit_item = menu.Append(wx.ID_ANY, "&Edit Post\tCtrl+E")
            pin_label = "Un&pin Post\tCtrl+P" if source.get("pinned") else "&Pin Post\tCtrl+P"
            pin_item = menu.Append(wx.ID_ANY, pin_label)
            self.Bind(wx.EVT_MENU, self.on_edit_post, edit_item)
            self.Bind(wx.EVT_MENU, self.on_pin_post, pin_item)
            menu.AppendSeparator()
        profile_item = menu.Append(wx.ID_ANY, "View &User Profile\tCtrl+Shift+U")
        user_tl_item = menu.Append(wx.ID_ANY, "Open User Time&line\tCtrl+Shift+L")
        self.Bind(wx.EVT_MENU, self.on_reply, reply_item)
        self.Bind(wx.EVT_MENU, self.on_boost, boost_item)
        self.Bind(wx.EVT_MENU, self.on_favourite, fav_item)
        self.Bind(wx.EVT_MENU, self.on_bookmark, bookmark_item)
        self.Bind(wx.EVT_MENU, self.on_copy_post, copy_item)
        self.Bind(wx.EVT_MENU, self.on_open_post_url, open_url_item)
        self.Bind(wx.EVT_MENU, self.on_view_thread, thread_item)
        self.Bind(wx.EVT_MENU, self.on_follow_user, follow_item)
        self.Bind(wx.EVT_MENU, self.on_block_user, block_item)
        self.Bind(wx.EVT_MENU, self.on_mute_user, mute_item)
        self.Bind(wx.EVT_MENU, self.on_view_profile, profile_item)
        self.Bind(wx.EVT_MENU, self.on_open_user_timeline, user_tl_item)
        self.posts_list.PopupMenu(menu)
        menu.Destroy()

    def setup_accelerators(self):
        accel_defs = [
            (wx.ACCEL_CTRL, ord('R'), self.on_reply),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('R'), self.on_boost),
            (wx.ACCEL_CTRL, ord('F'), self.on_favourite),
            (wx.ACCEL_CTRL, ord('B'), self.on_bookmark),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('C'), self.on_copy_post),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('O'), self.on_open_post_url),
            (wx.ACCEL_CTRL, ord('T'), self.on_view_thread),
            (wx.ACCEL_CTRL, ord('L'), self.on_follow_user),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('B'), self.on_block_user),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('M'), self.on_mute_user),
            (wx.ACCEL_CTRL, ord('E'), self.on_edit_post),
            (wx.ACCEL_CTRL, ord('P'), self.on_pin_post),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('U'), self.on_view_profile),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('S'), self.on_search),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('L'), self.on_open_user_timeline),
        ]
        accel_entries = []
        for flags, keycode, handler in accel_defs:
            id = wx.NewIdRef()
            self.Bind(wx.EVT_MENU, handler, id=id)
            accel_entries.append((flags, keycode, id.GetId()))
        self.SetAcceleratorTable(wx.AcceleratorTable(accel_entries))

    def on_reply(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        content = strip_html(status["content"].replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n'))
        reply_users, me_acct = "", self.me['acct']
        author_acct = status['account']['acct']
        if author_acct != me_acct: reply_users = f"@{author_acct} "
        for i in content.split(" "):
            if i.startswith("@") and i.strip('@') not in [me_acct, author_acct]: reply_users += i + " "
        
        dialog = wx.Dialog(self, title="Reply to Post", size=(500, 300))
        panel = wx.Panel(dialog)
        vbox = wx.BoxSizer(wx.VERTICAL)
        reply_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
        reply_text.SetValue(reply_users.strip() + " ")
        reply_text.SetInsertionPointEnd()
        privacy_label = wx.StaticText(panel, label="P&rivacy:")
        reply_privacy_choice = wx.Choice(panel, choices=self.privacy_options)
        send_button = wx.Button(panel, label="&Post")
        cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        
        try:
            reply_privacy_choice.SetSelection(self.privacy_values.index(status.get("visibility", "public")))
        except ValueError: reply_privacy_choice.SetSelection(0)
            
        def send_reply(e):
            text = reply_text.GetValue().strip()
            if not text: return wx.MessageBox("Reply cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
            try:
                self.mastodon.status_post(text, in_reply_to_id=status["id"], visibility=self.privacy_values[reply_privacy_choice.GetSelection()])
                if replysnd: replysnd.play()
                dialog.Close()
            except Exception as ex: wx.MessageBox(f"Error sending reply: {ex}", "Error", wx.OK | wx.ICON_ERROR)
        send_button.Bind(wx.EVT_BUTTON, send_reply)

        vbox.Add(wx.StaticText(panel, label="&Reply"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
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
            if status["reblogged"]: self.mastodon.status_unreblog(status["id"])
            else: self.mastodon.status_reblog(status["id"]); boostsnd and boostsnd.play()
            status["reblogged"] = not status["reblogged"]
            self.refresh_post_in_list(status, index)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Boost Error")

    def on_favourite(self, event):
        status, index = self.get_selected_status()
        if not status: return
        try:
            if status["favourited"]: self.mastodon.status_unfavourite(status["id"]); unfavsnd and unfavsnd.play()
            else: self.mastodon.status_favourite(status["id"]); favsnd and favsnd.play()
            status["favourited"] = not status["favourited"]
        except Exception as e: wx.MessageBox(f"Error: {e}", "Favourite Error")
    
    def on_bookmark(self, event):
        status, index = self.get_selected_status()
        if not status: return
        try:
            source = status.get('reblog') or status
            if source.get("bookmarked"):
                self.mastodon.status_unbookmark(source["id"])
                source["bookmarked"] = False
                wx.MessageBox("Post unbookmarked.", "Bookmark")
            else:
                self.mastodon.status_bookmark(source["id"])
                source["bookmarked"] = True
                wx.MessageBox("Post bookmarked.", "Bookmark")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Bookmark Error")

    def on_copy_post(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        content = strip_html((source.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
        if pyperclip:
            pyperclip.copy(content)
        else:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(content))
                wx.TheClipboard.Close()

    def on_open_post_url(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        url = source.get('url') or source.get('uri')
        if url:
            webbrowser.open(url)
        else:
            wx.MessageBox("No URL available for this post.", "Error")

    def on_view_thread(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        try:
            context = self.mastodon.status_context(source['id'])
            ancestors = context.get('ancestors', [])
            descendants = context.get('descendants', [])
            thread = ancestors + [source] + descendants
            timeline_key = f"thread:{source['id']}"
            self.timelines_data[timeline_key] = thread
            if timeline_key not in self.timeline_nodes:
                author = source['account'].get('display_name') or source['account'].get('username', '')
                node = self.timeline_tree.AppendItem(self.root, f"Thread by {author}")
                self.timeline_nodes[timeline_key] = node
            self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
        except Exception as e: wx.MessageBox(f"Error loading thread: {e}", "Thread Error")

    def on_follow_user(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        try:
            relationships = self.mastodon.account_relationships(account['id'])
            rel = relationships[0] if relationships else {}
            display = account.get('display_name') or account.get('username', '')
            if rel.get('following'):
                if wx.MessageBox(f"Unfollow {display}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.mastodon.account_unfollow(account['id'])
                    wx.MessageBox(f"Unfollowed {display}.", "Follow")
            else:
                self.mastodon.account_follow(account['id'])
                wx.MessageBox(f"Now following {display}.", "Follow")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Follow Error")

    def on_block_user(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        try:
            relationships = self.mastodon.account_relationships(account['id'])
            rel = relationships[0] if relationships else {}
            display = account.get('display_name') or account.get('username', '')
            if rel.get('blocking'):
                if wx.MessageBox(f"Unblock {display}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.mastodon.account_unblock(account['id'])
                    wx.MessageBox(f"Unblocked {display}.", "Block")
            else:
                if wx.MessageBox(f"Block {display}? You won't see their posts.", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.mastodon.account_block(account['id'])
                    wx.MessageBox(f"Blocked {display}.", "Block")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Block Error")

    def on_mute_user(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        try:
            relationships = self.mastodon.account_relationships(account['id'])
            rel = relationships[0] if relationships else {}
            display = account.get('display_name') or account.get('username', '')
            if rel.get('muting'):
                self.mastodon.account_unmute(account['id'])
                wx.MessageBox(f"Unmuted {display}.", "Mute")
            else:
                self.mastodon.account_mute(account['id'])
                wx.MessageBox(f"Muted {display}.", "Mute")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Mute Error")

    def on_edit_post(self, event):
        status, index = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        if source.get('account', {}).get('id') != (self.me or {}).get('id'):
            wx.MessageBox("You can only edit your own posts.", "Edit Error")
            return
        content = strip_html((source.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
        dialog = wx.Dialog(self, title="Edit Post", size=(500, 300))
        panel = wx.Panel(dialog)
        vbox = wx.BoxSizer(wx.VERTICAL)
        edit_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 150))
        edit_text.SetValue(content)
        edit_text.SetInsertionPointEnd()
        spoiler_label = wx.StaticText(panel, label="Content &Warning:")
        spoiler_input = wx.TextCtrl(panel, size=(480, 30))
        if source.get('spoiler_text'):
            spoiler_input.SetValue(source['spoiler_text'])
        save_button = wx.Button(panel, label="&Save")
        cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        
        def do_save(e):
            new_text = edit_text.GetValue().strip()
            if not new_text: return wx.MessageBox("Post cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
            try:
                spoiler = spoiler_input.GetValue().strip() or None
                updated = self.mastodon.status_update(source['id'], new_text, spoiler_text=spoiler)
                self.handle_status_update(updated)
                dialog.Close()
            except Exception as ex: wx.MessageBox(f"Error editing post: {ex}", "Error", wx.OK | wx.ICON_ERROR)
        
        save_button.Bind(wx.EVT_BUTTON, do_save)
        vbox.Add(wx.StaticText(panel, label="&Edit Post"), 0, wx.ALL, 10)
        vbox.Add(edit_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        vbox.Add(spoiler_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        vbox.Add(spoiler_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        btns.Add(save_button, 0, wx.ALL, 5)
        btns.Add(cancel_button, 0, wx.ALL, 5)
        vbox.Add(btns, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(vbox)
        dialog.ShowModal()
        dialog.Destroy()

    def on_pin_post(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        if source.get('account', {}).get('id') != (self.me or {}).get('id'):
            wx.MessageBox("You can only pin your own posts.", "Pin Error")
            return
        try:
            if source.get('pinned'):
                self.mastodon.status_unpin(source['id'])
                source['pinned'] = False
                wx.MessageBox("Post unpinned.", "Pin")
            else:
                self.mastodon.status_pin(source['id'])
                source['pinned'] = True
                wx.MessageBox("Post pinned.", "Pin")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Pin Error")

    def on_search(self, event):
        dlg = wx.TextEntryDialog(self, "Enter search query:", "Search")
        if dlg.ShowModal() == wx.ID_OK:
            query = dlg.GetValue().strip()
            if query:
                timeline_key = f"search:{query}"
                self.timelines_data[timeline_key] = []
                if timeline_key not in self.timeline_nodes:
                    node = self.timeline_tree.AppendItem(self.root, f"Search: {query}")
                    self.timeline_nodes[timeline_key] = node
                self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
                threading.Thread(target=self.load_timeline, args=(timeline_key,), daemon=True).start()
        dlg.Destroy()

    def on_open_user_timeline(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        display = account.get('display_name') or account.get('username', '')
        acct = account.get('acct', '')
        timeline_key = f"user:{account['id']}"
        self.timelines_data[timeline_key] = []
        if timeline_key not in self.timeline_nodes:
            node = self.timeline_tree.AppendItem(self.root, f"{display} (@{acct})")
            self.timeline_nodes[timeline_key] = node
        self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
        threading.Thread(target=self.load_timeline, args=(timeline_key,), daemon=True).start()

    def _show_account_list(self, title, accounts):
        dlg = wx.Dialog(self, title=title, size=(500, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 300))
        for acc in accounts:
            display = acc.get('display_name') or acc.get('username', '')
            acct = acc.get('acct', '')
            listbox.Append(f"{display} (@{acct})")
        sizer.Add(listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        view_btn = wx.Button(panel, label="&View Profile")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        btn_sizer.Add(view_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        
        def on_view(e):
            sel = listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            acc = accounts[sel]
            try:
                full_acc = self.mastodon.account(acc['id'])
            except Exception:
                full_acc = acc
            profile_dlg = ViewProfileDialog(dlg, full_acc, self.mastodon, self.me)
            profile_dlg.ShowModal()
            profile_dlg.Destroy()
        
        view_btn.Bind(wx.EVT_BUTTON, on_view)
        listbox.Bind(wx.EVT_LISTBOX_DCLICK, on_view)
        panel.SetSizer(sizer)
        
        if is_windows_dark_mode():
            dark_color = wx.Colour(40, 40, 40)
            light_text_color = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dark_color)
            panel.SetBackgroundColour(dark_color)
            for w in [listbox, view_btn, close_btn]:
                w.SetBackgroundColour(dark_color)
                w.SetForegroundColour(light_text_color)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_view_followers(self, event):
        try:
            accounts = self.mastodon.account_followers(self.me['id'], limit=80)
            self._show_account_list(f"Followers ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_following(self, event):
        try:
            accounts = self.mastodon.account_following(self.me['id'], limit=80)
            self._show_account_list(f"Following ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_blocked(self, event):
        try:
            accounts = self.mastodon.blocks(limit=80)
            self._show_account_list(f"Blocked Users ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_muted(self, event):
        try:
            accounts = self.mastodon.mutes(limit=80)
            self._show_account_list(f"Muted Users ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_follow_requests(self, event):
        try:
            accounts = self.mastodon.follow_requests(limit=80)
            if not accounts:
                wx.MessageBox("No pending follow requests.", "Follow Requests")
                return
            dlg = wx.Dialog(self, title=f"Follow Requests ({len(accounts)})", size=(500, 400))
            panel = wx.Panel(dlg)
            sizer = wx.BoxSizer(wx.VERTICAL)
            listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 300))
            for acc in accounts:
                display = acc.get('display_name') or acc.get('username', '')
                acct = acc.get('acct', '')
                listbox.Append(f"{display} (@{acct})")
            sizer.Add(listbox, 1, wx.EXPAND | wx.ALL, 10)
            
            btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
            accept_btn = wx.Button(panel, label="&Accept")
            reject_btn = wx.Button(panel, label="&Reject")
            close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
            btn_sizer.Add(accept_btn, 0, wx.ALL, 5)
            btn_sizer.Add(reject_btn, 0, wx.ALL, 5)
            btn_sizer.Add(close_btn, 0, wx.ALL, 5)
            sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
            
            def on_accept(e):
                sel = listbox.GetSelection()
                if sel == wx.NOT_FOUND: return
                try:
                    self.mastodon.follow_request_authorize(accounts[sel]['id'])
                    listbox.Delete(sel)
                    accounts.pop(sel)
                    wx.MessageBox("Follow request accepted.", "Accepted")
                except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
            
            def on_reject(e):
                sel = listbox.GetSelection()
                if sel == wx.NOT_FOUND: return
                try:
                    self.mastodon.follow_request_reject(accounts[sel]['id'])
                    listbox.Delete(sel)
                    accounts.pop(sel)
                    wx.MessageBox("Follow request rejected.", "Rejected")
                except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
            
            accept_btn.Bind(wx.EVT_BUTTON, on_accept)
            reject_btn.Bind(wx.EVT_BUTTON, on_reject)
            panel.SetSizer(sizer)
            
            if is_windows_dark_mode():
                dark_color = wx.Colour(40, 40, 40)
                light_text_color = wx.WHITE
                WxMswDarkMode().enable(dlg)
                dlg.SetBackgroundColour(dark_color)
                panel.SetBackgroundColour(dark_color)
                for w in [listbox, accept_btn, reject_btn, close_btn]:
                    w.SetBackgroundColour(dark_color)
                    w.SetForegroundColour(light_text_color)
            
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")
    
    def on_view_profile(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        unique_accounts = {}
        actor_account = status.get('account')
        if actor_account: unique_accounts[actor_account['id']] = actor_account
        
        source_status = status.get('reblog') or status
        original_author = source_status.get('account')
        if original_author: unique_accounts[original_author['id']] = original_author
        
        for mention in source_status.get('mentions', []): unique_accounts[mention['id']] = mention
        
        accounts_list = sorted(list(unique_accounts.values()), key=lambda acc: (acc.get('display_name') or acc.get('username', '')).lower())
        
        account_to_view = accounts_list[0] if len(accounts_list) == 1 else None
        if not account_to_view and len(accounts_list) > 1:
            dlg = UserSelectionDialog(self, accounts_list)
            if dlg.ShowModal() == wx.ID_OK:
                account_to_view = dlg.get_selected_account()
            dlg.Destroy()
        
        if account_to_view:
            try:
                full_account = self.mastodon.account(account_to_view['id'])
            except Exception:
                full_account = account_to_view
            profile_dlg = ViewProfileDialog(self, full_account, self.mastodon, self.me)
            profile_dlg.ShowModal()
            profile_dlg.Destroy()

    def on_add_media(self, event):
        if len(self.media_files) >= 4:
            wx.MessageBox("Maximum 4 media attachments allowed.", "Limit Reached")
            return
        wildcard = "Media files (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.mp4;*.webm;*.mp3;*.ogg;*.wav)|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.mp4;*.webm;*.mp3;*.ogg;*.wav|All files (*.*)|*.*"
        dlg = wx.FileDialog(self, "Choose media file", wildcard=wildcard, style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            self.media_files.append({"path": path, "alt_text": ""})
            self.media_list.Append(os.path.basename(path))
            self.media_list.SetSelection(len(self.media_files) - 1)
        dlg.Destroy()

    def on_remove_media(self, event):
        sel = self.media_list.GetSelection()
        if sel == wx.NOT_FOUND: return
        self.media_files.pop(sel)
        self.media_list.Delete(sel)
        self.alt_text_input.SetValue("")

    def on_media_selected(self, event):
        sel = self.media_list.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self.media_files):
            self.alt_text_input.SetValue(self.media_files[sel].get("alt_text", ""))

    def on_alt_text_changed(self, event):
        sel = self.media_list.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self.media_files):
            self.media_files[sel]["alt_text"] = self.alt_text_input.GetValue()

    def refresh_post_in_list(self, status, index):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if not key or index is None: return
        if key == "notifications": self.load_timeline(key)
        else:
            self.timelines_data[key][index] = status
            row, avatar_url = self.row_from_status(status)
            if row: 
                self.posts_list.SetString(index, row, avatar_url)
                self.queue_avatar_download(avatar_url)

    def open_settings(self, event):
        dlg = SettingsDialog(self, on_save_callback=self.load_sounds)
        if dlg.ShowModal() == wx.ID_OK: load_sounds_globally()
        dlg.Destroy()

    def on_toggle_cw(self, event):
        show = self.cw_toggle.IsChecked()
        self.cw_input.Show(show)
        self.cw_label.Show(show)
        self.panel.Layout()
    
    def on_toggle_poll(self, event):
        show = self.poll_toggle.IsChecked()
        for widget in self.poll_widgets:
            widget.Show(show)
        self.poll_sizer.Show(show)
        self.panel.Layout()

    def on_toggle_media(self, event):
        show = self.media_toggle.IsChecked()
        for widget in self.media_widgets:
            widget.Show(show)
        self.media_sizer.Show(show)
        self.panel.Layout()

    def on_post_activated(self, event): self.show_post_details()

    def on_post(self, event):
        status_text = self.toot_input.GetValue().strip()
        spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
        visibility = self.privacy_values[self.privacy_choice.GetSelection()]
        poll_data = None
        if self.poll_toggle.IsChecked():
            options = [opt.GetValue().strip() for opt in self.poll_option_inputs if opt.GetValue().strip()]
            if len(options) < 2: return wx.MessageBox("A poll must have at least two options.", "Poll Error", wx.OK | wx.ICON_ERROR)
            poll_data = {'options': options, 'expires_in': self.poll_duration_seconds[self.poll_duration_choice.GetSelection()], 'multiple': self.poll_multiple_choice.IsChecked()}
        if not status_text and not poll_data and not self.media_files: return wx.MessageBox("Cannot post empty status.", "Error", wx.OK | wx.ICON_ERROR)
        try:
            media_ids = []
            for mf in self.media_files:
                media = self.mastodon.media_post(mf["path"], description=mf.get("alt_text") or None)
                media_ids.append(media)
            self.mastodon.status_post(status_text, spoiler_text=spoiler, visibility=visibility, poll=poll_data, media_ids=media_ids if media_ids else None)
            if tootsnd: tootsnd.play()
            self.toot_input.SetValue(""); self.cw_input.SetValue(""); self.cw_toggle.SetValue(False); self.on_toggle_cw(None)
            self.media_files.clear(); self.media_list.Clear(); self.alt_text_input.SetValue("")
            self.media_toggle.SetValue(False); self.on_toggle_media(None)
            if poll_data:
                self.poll_toggle.SetValue(False); [opt.SetValue("") for opt in self.poll_option_inputs]; self.poll_duration_choice.SetSelection(5); self.poll_multiple_choice.SetValue(False); self.on_toggle_poll(None)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Post Error")

    def on_key_press(self, event):
        if event.GetKeyCode() == wx.WXK_DELETE and self.FindFocus() == self.posts_list: self.delete_selected_post()
        elif event.GetKeyCode() == wx.WXK_RETURN and self.FindFocus() == self.toot_input and event.HasAnyModifiers(): self.on_post(event)
        elif event.GetKeyCode() == wx.WXK_END and event.ControlDown() and self.FindFocus() == self.posts_list: self.load_older_posts()
        else: event.Skip()

    def load_older_posts(self):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if not key or not self.timelines_data.get(key): return
        last_items = self.timelines_data[key]
        if not last_items: return
        
        last_id = None
        if key == "notifications":
            last_id = last_items[-1].get('id')
        else:
            last_id = last_items[-1].get('id')
        
        if not last_id: return
        
        def _load():
            try:
                if key == "home": data = self.mastodon.timeline_home(max_id=last_id, limit=40)
                elif key == "local": data = self.mastodon.timeline_local(max_id=last_id, limit=40)
                elif key == "federated": data = self.mastodon.timeline_public(max_id=last_id, limit=40)
                elif key == "sent": data = [s for s in self.mastodon.account_statuses(self.me["id"], max_id=last_id, limit=40) if not s.get("reblog")]
                elif key == "favourites": data = self.mastodon.favourites(max_id=last_id, limit=40)
                elif key == "bookmarks": data = self.mastodon.bookmarks(max_id=last_id, limit=40)
                elif key == "notifications": data = self.mastodon.notifications(max_id=last_id, limit=40)
                elif key == "mentions": data = [n["status"] for n in self.mastodon.notifications(types=["mention"], max_id=last_id, limit=40) if n.get("status")]
                elif key.startswith("user:"): data = self.mastodon.account_statuses(key.split(":", 1)[1], max_id=last_id, limit=40)
                else: data = []
                
                self.timelines_data[key].extend(data)
                if self.timeline_tree.GetSelection() == self.timeline_nodes.get(key):
                    for item in data:
                        row, avatar_url = (self.row_from_notification(item) if key == "notifications" else self.row_from_status(item))
                        if row:
                            wx.CallAfter(self.posts_list.Append, row, avatar_url)
                            self.queue_avatar_download(avatar_url)
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Error loading more posts: {e}", "Error")
        
        threading.Thread(target=_load, daemon=True).start()

    def delete_selected_post(self):
        status, _ = self.get_selected_status()
        if not status or status.get('account', {}).get('id') != (self.me or {}).get('id'): return
        msg, title = ("unboost this post?", "Confirm Unboost") if status.get("reblog") else ("delete this post?", "Confirm Deletion")
        if wx.MessageBox(f"Are you sure you want to {msg}", title, wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            try:
                if status.get("reblog"): self.mastodon.status_unreblog(status['id'])
                else: self.mastodon.status_delete(status['id'])
            except Exception as e: wx.MessageBox(f"Error: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def start_streaming(self):
        if not self.mastodon: return
        threading.Thread(target=self.mastodon.stream_user, args=(CustomStreamListener(self),), daemon=True).start()

    def add_new_post(self, status):
        if not self.me or status.get("account", {}).get("id") == self.me.get("id"): pass
        else:
            is_mention_of_me = any(m.get('id') == self.me.get('id') for m in (status.get('reblog') or status).get('mentions', []))
            if is_mention_of_me: pass
            elif status.get("visibility") == "direct": dmsnd and dmsnd.play()
            else: newtootsnd and newtootsnd.play()
        
        self.timelines_data["home"].insert(0, status)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["home"]:
            row, avatar_url = self.row_from_status(status)
            if row: 
                self.posts_list.Insert(row, 0, avatar_url)
                self.queue_avatar_download(avatar_url)

        # Also add to local timeline for local posts
        if "local" in self.timeline_nodes:
            self.timelines_data.setdefault("local", []).insert(0, status)
            if self.timeline_tree.GetSelection() == self.timeline_nodes["local"]:
                row, avatar_url = self.row_from_status(status)
                if row:
                    self.posts_list.Insert(row, 0, avatar_url)
                    self.queue_avatar_download(avatar_url)

    def add_notification(self, notification):
        ntype = notification.get("type")
        if ntype in ["favourite", "reblog", "follow", "follow_request"]:
            notificationsnd and notificationsnd.play()
        elif ntype == "mention":
            mentionsnd and mentionsnd.play()
        
        self.timelines_data["notifications"].insert(0, notification)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["notifications"]:
            row, avatar_url = self.row_from_notification(notification)
            if row: 
                self.posts_list.Insert(row, 0, avatar_url)
                self.queue_avatar_download(avatar_url)

    def handle_status_update(self, status):
        for timeline in ["home", "local", "federated", "sent", "mentions", "direct_messages", "favourites", "bookmarks"]:
            for i, s in enumerate(self.timelines_data.get(timeline, [])):
                if s.get("id") == status.get("id"):
                    self.timelines_data[timeline][i] = status
                    if self.timeline_tree.GetSelection() == self.timeline_nodes.get(timeline):
                        row, avatar_url = self.row_from_status(status)
                        self.posts_list.SetString(i, row, avatar_url)
                        self.queue_avatar_download(avatar_url)
                    break

    def handle_post_deletion(self, status_id):
        for timeline in ["home", "local", "federated", "sent", "mentions", "direct_messages", "favourites", "bookmarks"]:
            for i, s in enumerate(self.timelines_data.get(timeline, [])):
                if s.get("id") == status_id:
                    self.timelines_data[timeline].pop(i)
                    if self.timeline_tree.GetSelection() == self.timeline_nodes.get(timeline): 
                        self.posts_list.Delete(i)
                    break

    def load_timeline(self, timeline):
        wx.CallAfter(self.posts_list.Clear)
        try:
            if timeline == "home": data = self.mastodon.timeline_home(limit=40)
            elif timeline == "local": data = self.mastodon.timeline_local(limit=40)
            elif timeline == "federated": data = self.mastodon.timeline_public(limit=40)
            elif timeline == "sent": data = [s for s in self.mastodon.account_statuses(self.me["id"], limit=40) if not s.get("reblog")]
            elif timeline == "direct_messages":
                convos = self.mastodon.conversations(limit=40)
                data = [c.get("last_status") for c in convos if c.get("last_status")]
            elif timeline == "favourites": data = self.mastodon.favourites(limit=40)
            elif timeline == "bookmarks": data = self.mastodon.bookmarks(limit=40)
            elif timeline == "notifications": data = self.mastodon.notifications(limit=40)
            elif timeline == "mentions": data = [n["status"] for n in self.mastodon.notifications(types=["mention"], limit=40) if n.get("status")]
            elif timeline.startswith("user:"): data = self.mastodon.account_statuses(timeline.split(":", 1)[1], limit=40)
            elif timeline.startswith("search:"): data = self.mastodon.search_v2(timeline.split(":", 1)[1], result_type="statuses").get("statuses", [])
            else: data = []
            
            self.timelines_data[timeline] = data
            
            if self.timeline_tree.GetSelection() == self.timeline_nodes.get(timeline):
                for item in data:
                    row, avatar_url = (self.row_from_notification(item) if timeline == "notifications" else self.row_from_status(item))
                    if row: 
                        wx.CallAfter(self.posts_list.Append, row, avatar_url)
                        self.queue_avatar_download(avatar_url)

        except Exception as e: 
            wx.MessageBox(f"Failed to load timeline: {e}", "Error")

    def on_timeline_selected(self, event):
        for key, node in self.timeline_nodes.items():
            if event.GetItem() == node:
                self.posts_list.Clear()
                for item in self.timelines_data.get(key, []):
                    row, avatar_url = (self.row_from_notification(item) if key == "notifications" else self.row_from_status(item))
                    if row: 
                        self.posts_list.Append(row, avatar_url)
                        self.queue_avatar_download(avatar_url)
                break

    def on_refresh(self, event):
        for key, node in self.timeline_nodes.items():
            if self.timeline_tree.GetSelection() == node:
                threading.Thread(target=self.load_timeline, args=(key,), daemon=True).start()
                break

    def on_post_selected(self, event):
        status, _ = self.get_selected_status()
        if not status: event.Skip(); return
        source_status = status.get('reblog') or status
        if pollsnd and source_status.get('poll'): pollsnd.stop(); pollsnd.play()
        elif select_mentionsnd and self.me and any(m.get('id') == self.me.get('id') for m in source_status.get('mentions', [])): select_mentionsnd.stop(); select_mentionsnd.play()
        elif source_status.get('media_attachments', []):
            types = {att.get('type') for att in source_status['media_attachments']}
            if mediasnd and any(t in types for t in ['video', 'gifv', 'audio']): mediasnd.stop(); mediasnd.play()
            elif imagesnd and 'image' in types: imagesnd.stop(); imagesnd.play()
        event.Skip()

    def load_sounds(self): load_sounds_globally()

    def format_notification_for_display(self, notification):
        ntype, account = notification.get("type"), notification.get("account", {})
        user = account.get("display_name") or account.get("username", "Unknown")
        status = notification.get("status")
        content = strip_html((status['content'] or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
        
        if ntype == "favourite" and status: return f"{user} favourited your post: {content}"
        if ntype == "reblog" and status: return f"{user} boosted your post: {content}"
        if ntype == "mention" and status: return f"{user} mentioned you: {content}"
        if ntype == "follow": return f"{user} followed you."
        if ntype == "follow_request": return f"{user} requested to follow you."
        if ntype == "poll" and status and status.get("poll", {}).get("expired"): return f"Poll ended in {user}'s post: {content}"
        if ntype == "update" and status: return f"{user}'s post you interacted with was edited: {content}"
        return f"{user}: {ntype}"

    def row_from_status(self, status):
        if not status: return None, None
        is_boost = bool(status.get('reblog'))
        source_obj = status['reblog'] if is_boost else status
        account = status['account']
        avatar_url = account.get('avatar_static') if self.show_avatars else None
        author_cell = account.get('display_name') or account.get('username')
        content = strip_html((source_obj.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
        
        if is_boost:
            original_author = source_obj['account']
            original_display = original_author.get('display_name') or original_author.get('username')
            original_handle = original_author.get('acct', '')
            content_body = f"CW: {source_obj['spoiler_text']}" if source_obj.get('spoiler_text') else content
            content_cell = f"boosting {original_display} (@{original_handle}): {content_body}"
        else:
            content_cell = f"CW: {status['spoiler_text']}" if status.get('spoiler_text') else content
            
        if source_obj.get('poll'): content_cell += " [Poll]"
        time_cell = self.format_time(source_obj.get('created_at')) or ''
        client_cell = self.get_app_name(source_obj) or ''
        
        return [author_cell or '', content_cell or '', time_cell, client_cell], avatar_url

    def row_from_notification(self, notification):
        account = notification.get('account', {})
        avatar_url = account.get('avatar_static') if self.show_avatars else None
        display_text = self.format_notification_for_display(notification)
        status = notification.get('status') or {}
        time_cell = self.format_time(notification.get('created_at'))
        client_cell = self.get_app_name(status)
        
        parts = display_text.split(':', 1)
        author_part = parts[0]
        content_part = parts[1].strip() if len(parts) > 1 else ''

        return [author_part, content_part, time_cell, client_cell], avatar_url

    def show_post_details(self):
        status, _ = self.get_selected_status()
        if not status: return wx.MessageBox("This notification has no associated post.", "No Post", wx.OK | wx.ICON_INFORMATION)
        dlg = PostDetailsDialog(self, self.mastodon, status, self.me, votesnd=votesnd)
        dlg.ShowModal()
        dlg.Destroy()

    def get_app_name(self, status_obj):
        if not status_obj: return 'Unknown'
        app = status_obj.get('application') or {}
        return app.get('name', 'Unknown') if isinstance(app, dict) else str(app or 'Unknown')

    def format_time(self, created_at):
        if not created_at: return ''
        return singularize_time(get_time_ago(created_at))