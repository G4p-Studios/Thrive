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
    "votesnd": "vote.wav", "notificationsnd": "new_notification.wav",
    "errorsnd": "error.wav", "maxsnd": "max.wav", "searchsnd": "new_search.wav",
    "open_timelinesnd": "open_timeline.wav", "send_dmsnd": "send_dm.wav",
    "followsnd": "follow.wav", "unfollowsnd": "unfollow.wav", "boundarysnd": "boundary.wav",
    "close_timelinesnd": "close_timeline.wav",
    "favoritessnd": "favorites.wav", "search_updatedsnd": "search_updated.wav",
    "usersnd": "user.wav"
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
        menubar.Append(settings_menu, "&Client")
        view_menu = wx.Menu()
        refresh_item = view_menu.Append(wx.ID_REFRESH, "&Refresh	F5", "Reload current timeline")
        self.Bind(wx.EVT_MENU, self.on_refresh, refresh_item)
        view_menu.AppendSeparator()
        self.show_avatars_item = view_menu.Append(wx.ID_ANY, "Show Profile Pictures", "Toggle display of profile pictures", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.on_toggle_show_avatars, self.show_avatars_item)
        view_menu.AppendSeparator()
        find_item = view_menu.Append(wx.ID_ANY, "&Find in Timeline...", "Search within the current timeline")
        view_menu.AppendSeparator()
        scheduled_item = view_menu.Append(wx.ID_ANY, "&Scheduled Posts...", "View and manage scheduled posts")
        view_menu.AppendSeparator()
        explore_item = view_menu.Append(wx.ID_ANY, "E&xplore/Discover...\tCtrl+T\tCtrl+T", "Browse trending posts, hashtags, and links")
        lists_item = view_menu.Append(wx.ID_ANY, "&Lists...", "Manage and view lists")
        followed_hashtags_item = view_menu.Append(wx.ID_ANY, "Followed &Hashtags...", "View and manage followed hashtags")
        view_menu.AppendSeparator()
        view_favourites_item = view_menu.Append(wx.ID_ANY, "Fav&ourites\tCtrl+Alt+K", "View your favourited posts")
        view_bookmarks_item = view_menu.Append(wx.ID_ANY, "Boo&kmarks\tCtrl+Alt+B", "View your bookmarked posts")
        view_menu.AppendSeparator()
        followers_item = view_menu.Append(wx.ID_ANY, "My &Followers\tCtrl+[", "View your followers")
        following_item = view_menu.Append(wx.ID_ANY, "My F&ollowing\tCtrl+]\tCtrl+]", "View who you follow")
        blocked_item = view_menu.Append(wx.ID_ANY, "&Blocked Users", "View blocked users")
        muted_item = view_menu.Append(wx.ID_ANY, "M&uted Users", "View muted users")
        follow_requests_item = view_menu.Append(wx.ID_ANY, "Follow &Requests", "View pending follow requests")
        view_menu.AppendSeparator()
        edit_profile_item = view_menu.Append(wx.ID_ANY, "Edit &My Profile...", "Edit your display name, bio, and avatar")
        view_menu.AppendSeparator()
        instance_info_item = view_menu.Append(wx.ID_ANY, "&Instance Info...\tCtrl+I", "View information about the Mastodon instance")
        self.Bind(wx.EVT_MENU, self.on_find_in_timeline, find_item)
        self.Bind(wx.EVT_MENU, self.on_scheduled_posts, scheduled_item)
        self.Bind(wx.EVT_MENU, self.on_explore, explore_item)
        self.Bind(wx.EVT_MENU, self.on_lists, lists_item)
        self.Bind(wx.EVT_MENU, self.on_followed_hashtags, followed_hashtags_item)
        self.Bind(wx.EVT_MENU, self.on_view_favourites_timeline, view_favourites_item)
        self.Bind(wx.EVT_MENU, self.on_view_bookmarks_timeline, view_bookmarks_item)
        self.Bind(wx.EVT_MENU, self.on_view_followers, followers_item)
        self.Bind(wx.EVT_MENU, self.on_view_following, following_item)
        self.Bind(wx.EVT_MENU, self.on_view_blocked, blocked_item)
        self.Bind(wx.EVT_MENU, self.on_view_muted, muted_item)
        self.Bind(wx.EVT_MENU, self.on_view_follow_requests, follow_requests_item)
        self.Bind(wx.EVT_MENU, self.on_edit_my_profile, edit_profile_item)
        self.Bind(wx.EVT_MENU, self.on_instance_info, instance_info_item)
        menubar.Append(view_menu, "&View")
        
        actions_menu = wx.Menu()
        compose_menu_item = actions_menu.Append(wx.ID_ANY, "Compose &New Post\tCtrl+N")
        reply_menu_item = actions_menu.Append(wx.ID_ANY, "&Reply\tCtrl+R")
        quote_menu_item = actions_menu.Append(wx.ID_ANY, "&Quote\tCtrl+Q")
        boost_menu_item = actions_menu.Append(wx.ID_ANY, "&Boost\tCtrl+Shift+R")
        fav_menu_item = actions_menu.Append(wx.ID_ANY, "&Favourite\tCtrl+F")
        bookmark_menu_item = actions_menu.Append(wx.ID_ANY, "Boo&kmark\tAlt+B")
        actions_menu.AppendSeparator()
        copy_menu_item = actions_menu.Append(wx.ID_ANY, "&Copy Post Text\tCtrl+C")
        open_url_menu_item = actions_menu.Append(wx.ID_ANY, "&Open Post on Web\tAlt+W")
        view_post_menu_item = actions_menu.Append(wx.ID_ANY, "&View Post Details\tAlt+V")
        view_thread_menu_item = actions_menu.Append(wx.ID_ANY, "Get &Thread/Conversation\tCtrl+G")
        actions_menu.AppendSeparator()
        follow_menu_item = actions_menu.Append(wx.ID_ANY, "Fo&llow User\tCtrl+L")
        block_menu_item = actions_menu.Append(wx.ID_ANY, "B&lock User\tCtrl+B")
        mute_menu_item = actions_menu.Append(wx.ID_ANY, "&Mute/Unmute User")
        actions_menu.AppendSeparator()
        edit_menu_item = actions_menu.Append(wx.ID_ANY, "&Edit Post\tCtrl+E")
        pin_menu_item = actions_menu.Append(wx.ID_ANY, "&Pin/Unpin Post")
        actions_menu.AppendSeparator()
        mute_convo_menu_item = actions_menu.Append(wx.ID_ANY, "Mute Con&versation")
        report_menu_item = actions_menu.Append(wx.ID_ANY, "&Report Post/User...")
        actions_menu.AppendSeparator()
        view_media_menu_item = actions_menu.Append(wx.ID_ANY, "View M&edia Attachments")
        view_boosters_menu_item = actions_menu.Append(wx.ID_ANY, "View &Who Boosted")
        view_favouriters_menu_item = actions_menu.Append(wx.ID_ANY, "View Who Fav&ourited")
        view_history_menu_item = actions_menu.Append(wx.ID_ANY, "View Edit &History")
        actions_menu.AppendSeparator()
        profile_menu_item = actions_menu.Append(wx.ID_ANY, "View &User Profile\tCtrl+Shift+U")
        search_menu_item = actions_menu.Append(wx.ID_ANY, "&Search\tCtrl+/")
        user_timeline_menu_item = actions_menu.Append(wx.ID_ANY, "Open User Time&line\tCtrl+U")
        dm_user_menu_item = actions_menu.Append(wx.ID_ANY, "Send &Direct Message\tCtrl+D")

        self.Bind(wx.EVT_MENU, self.on_focus_compose, compose_menu_item)
        self.Bind(wx.EVT_MENU, self.on_reply, reply_menu_item)
        self.Bind(wx.EVT_MENU, self.on_quote, quote_menu_item)
        self.Bind(wx.EVT_MENU, self.on_boost, boost_menu_item)
        self.Bind(wx.EVT_MENU, self.on_favourite, fav_menu_item)
        self.Bind(wx.EVT_MENU, self.on_bookmark, bookmark_menu_item)
        self.Bind(wx.EVT_MENU, self.on_copy_post, copy_menu_item)
        self.Bind(wx.EVT_MENU, self.on_open_post_url, open_url_menu_item)
        self.Bind(wx.EVT_MENU, self.on_post_activated, view_post_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_thread, view_thread_menu_item)
        self.Bind(wx.EVT_MENU, self.on_follow_user, follow_menu_item)
        self.Bind(wx.EVT_MENU, self.on_block_user, block_menu_item)
        self.Bind(wx.EVT_MENU, self.on_mute_user, mute_menu_item)
        self.Bind(wx.EVT_MENU, self.on_edit_post, edit_menu_item)
        self.Bind(wx.EVT_MENU, self.on_pin_post, pin_menu_item)
        self.Bind(wx.EVT_MENU, self.on_mute_conversation, mute_convo_menu_item)
        self.Bind(wx.EVT_MENU, self.on_report, report_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_media, view_media_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_boosters, view_boosters_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_favouriters, view_favouriters_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_edit_history, view_history_menu_item)
        self.Bind(wx.EVT_MENU, self.on_view_profile, profile_menu_item)
        self.Bind(wx.EVT_MENU, self.on_search, search_menu_item)
        self.Bind(wx.EVT_MENU, self.on_open_user_timeline, user_timeline_menu_item)
        self.Bind(wx.EVT_MENU, self.on_dm_user, dm_user_menu_item)
        menubar.Append(actions_menu, "&Actions")
        
        self.SetMenuBar(menubar)

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.toot_label = wx.StaticText(self.panel, label="&Create New Post")
        self.toot_input = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(-1, 80))
        self.cw_label = wx.StaticText(self.panel, label="Content w&arning:")
        self.cw_input = wx.TextCtrl(self.panel, size=(400, -1))
        self.cw_input.Hide()
        self.cw_label.Hide()
        vbox.Add(self.toot_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        vbox.Add(self.toot_input, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.cw_label, 0, wx.LEFT | wx.RIGHT, 5)
        vbox.Add(self.cw_input, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Compact controls row: toggles, dropdowns, buttons all horizontal
        controls_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.cw_toggle = wx.CheckBox(self.panel, label="C&W")
        self.cw_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_cw)
        self.poll_toggle = wx.CheckBox(self.panel, label="&Poll")
        self.poll_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_poll)
        self.media_toggle = wx.CheckBox(self.panel, label="M&edia")
        self.media_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_media)
        self.schedule_toggle = wx.CheckBox(self.panel, label="Sc&hedule")
        self.schedule_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_schedule)
        self.privacy_label = wx.StaticText(self.panel, label="P&rivacy:")
        self.privacy_choice = wx.Choice(self.panel, choices=self.privacy_options)
        self.privacy_choice.SetSelection(0)
        self.language_label = wx.StaticText(self.panel, label="&Lang:")
        self.language_names = ["Default", "English", "Spanish", "French", "German", "Italian", "Portuguese", "Japanese", "Chinese", "Korean", "Russian", "Arabic", "Dutch", "Polish", "Swedish", "Turkish", "Indonesian", "Hindi", "Thai", "Vietnamese"]
        self.language_codes = ["", "en", "es", "fr", "de", "it", "pt", "ja", "zh", "ko", "ru", "ar", "nl", "pl", "sv", "tr", "id", "hi", "th", "vi"]
        self.language_choice = wx.Choice(self.panel, choices=self.language_names)
        self.language_choice.SetSelection(0)
        self.post_button = wx.Button(self.panel, label="&Post")
        self.post_button.Bind(wx.EVT_BUTTON, self.on_post)
        self.exit_button = wx.Button(self.panel, label="E&xit")
        self.exit_button.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        for w in [self.cw_toggle, self.poll_toggle, self.media_toggle, self.schedule_toggle]:
            controls_sizer.Add(w, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        controls_sizer.AddSpacer(10)
        controls_sizer.Add(self.privacy_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 3)
        controls_sizer.Add(self.privacy_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        controls_sizer.Add(self.language_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 3)
        controls_sizer.Add(self.language_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        controls_sizer.AddStretchSpacer()
        controls_sizer.Add(self.post_button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        controls_sizer.Add(self.exit_button, 0, wx.ALIGN_CENTER_VERTICAL)
        vbox.Add(controls_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

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
        # Media items that only show when files are present
        self.media_file_widgets = [self.media_list, self.remove_media_button, self.alt_text_label, self.alt_text_input]
        
        self.add_media_button.Bind(wx.EVT_BUTTON, self.on_add_media)
        self.remove_media_button.Bind(wx.EVT_BUTTON, self.on_remove_media)
        self.media_list.Bind(wx.EVT_LISTBOX, self.on_media_selected)
        self.alt_text_input.Bind(wx.EVT_TEXT, self.on_alt_text_changed)
        
        vbox.Add(self.media_sizer, 0, wx.EXPAND | wx.ALL, 5)

        for widget in self.media_widgets:
            widget.Hide()
            widget.Disable()
        self.media_sizer.Show(False)

        # Schedule UI
        self.schedule_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.schedule_date_label = wx.StaticText(self.panel, label="Date (YYYY-MM-DD):")
        self.schedule_date_input = wx.TextCtrl(self.panel, size=(120, -1))
        self.schedule_time_label = wx.StaticText(self.panel, label="Time (HH:MM):")
        self.schedule_time_input = wx.TextCtrl(self.panel, size=(80, -1))
        self.schedule_sizer.Add(self.schedule_date_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.schedule_sizer.Add(self.schedule_date_input, 0, wx.RIGHT, 10)
        self.schedule_sizer.Add(self.schedule_time_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.schedule_sizer.Add(self.schedule_time_input, 0)
        self.schedule_widgets = [self.schedule_date_label, self.schedule_date_input, self.schedule_time_label, self.schedule_time_input]
        vbox.Add(self.schedule_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        for w in self.schedule_widgets:
            w.Hide()

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
            for widget in [self.toot_label, self.cw_label, self.cw_toggle, self.poll_toggle, self.media_toggle, self.schedule_toggle, self.privacy_label, self.language_label, self.posts_label, *self.poll_widgets, self.alt_text_label, *self.schedule_widgets]:
                widget.SetForegroundColour(light_text_color)
                widget.SetBackgroundColour(dark_color)
            self.poll_sizer.GetStaticBox().SetForegroundColour(light_text_color)
            self.media_sizer.GetStaticBox().SetForegroundColour(light_text_color)
            for widget in [self.toot_input, self.cw_input, self.privacy_choice, self.language_choice, self.timeline_tree, self.posts_list, self.post_button, self.exit_button, *self.media_widgets]:
                widget.SetForegroundColour(light_text_color)
                widget.SetBackgroundColour(dark_color)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(self.timeline_tree, 0, wx.EXPAND | wx.ALL, 5)
        hbox.Add(self.posts_list, 1, wx.EXPAND | wx.ALL, 5)
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
        quote_item = menu.Append(wx.ID_ANY, "&Quote\tCtrl+Q")
        boost_label = "Un&boost\tCtrl+Shift+R" if status.get("reblogged") else "&Boost\tCtrl+Shift+R"
        boost_item = menu.Append(wx.ID_ANY, boost_label)
        fav_label = "Un&favourite\tCtrl+F" if status.get("favourited") else "&Favourite\tCtrl+F"
        fav_item = menu.Append(wx.ID_ANY, fav_label)
        bookmark_label = "Un&bookmark\tAlt+B" if source.get("bookmarked") else "Boo&kmark\tAlt+B"
        bookmark_item = menu.Append(wx.ID_ANY, bookmark_label)
        menu.AppendSeparator()
        copy_item = menu.Append(wx.ID_ANY, "&Copy Post Text\tCtrl+C")
        open_url_item = menu.Append(wx.ID_ANY, "&Open Post on Web\tAlt+W")
        view_post_item = menu.Append(wx.ID_ANY, "&View Post Details\tAlt+V")
        thread_item = menu.Append(wx.ID_ANY, "Get &Thread/Conversation\tCtrl+G")
        menu.AppendSeparator()
        follow_item = menu.Append(wx.ID_ANY, "Fo&llow User\tCtrl+L")
        block_item = menu.Append(wx.ID_ANY, "B&lock User\tCtrl+B")
        mute_item = menu.Append(wx.ID_ANY, "&Mute/Unmute User")
        menu.AppendSeparator()
        if source.get('account', {}).get('id') == (self.me or {}).get('id'):
            edit_item = menu.Append(wx.ID_ANY, "&Edit Post\tCtrl+E")
            pin_label = "Un&pin Post" if source.get("pinned") else "&Pin Post"
            pin_item = menu.Append(wx.ID_ANY, pin_label)
            self.Bind(wx.EVT_MENU, self.on_edit_post, edit_item)
            self.Bind(wx.EVT_MENU, self.on_pin_post, pin_item)
            menu.AppendSeparator()
        profile_item = menu.Append(wx.ID_ANY, "View &User Profile\tCtrl+Shift+U")
        user_tl_item = menu.Append(wx.ID_ANY, "Open User Time&line\tCtrl+U")
        menu.AppendSeparator()
        view_media_item = menu.Append(wx.ID_ANY, "View M&edia Attachments")
        view_boosters_item = menu.Append(wx.ID_ANY, "View &Who Boosted")
        view_favouriters_item = menu.Append(wx.ID_ANY, "View Who Fav&ourited")
        view_history_item = menu.Append(wx.ID_ANY, "View Edit &History")
        menu.AppendSeparator()
        mute_convo_item = menu.Append(wx.ID_ANY, "Mute Con&versation")
        report_item = menu.Append(wx.ID_ANY, "&Report Post/User...")
        dm_item = menu.Append(wx.ID_ANY, "Send &Direct Message\tCtrl+D")
        
        # Notification-specific items
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if key in ("notifications", "mentions"):
            menu.AppendSeparator()
            dismiss_notif_item = menu.Append(wx.ID_ANY, "D&ismiss Notification")
            clear_notifs_item = menu.Append(wx.ID_ANY, "C&lear All Notifications")
            self.Bind(wx.EVT_MENU, self.on_dismiss_notification, dismiss_notif_item)
            self.Bind(wx.EVT_MENU, self.on_clear_all_notifications, clear_notifs_item)
            # Accept/reject follow request
            sel = self.posts_list.GetSelection()
            if sel != wx.NOT_FOUND and sel < len(self.timelines_data.get(key, [])):
                notif = self.timelines_data[key][sel]
                if notif.get('type') == 'follow_request':
                    accept_item = menu.Append(wx.ID_ANY, "&Accept Follow Request")
                    reject_item = menu.Append(wx.ID_ANY, "Re&ject Follow Request")
                    self.Bind(wx.EVT_MENU, self.on_accept_follow_request, accept_item)
                    self.Bind(wx.EVT_MENU, self.on_reject_follow_request, reject_item)
        
        self.Bind(wx.EVT_MENU, self.on_reply, reply_item)
        self.Bind(wx.EVT_MENU, self.on_quote, quote_item)
        self.Bind(wx.EVT_MENU, self.on_boost, boost_item)
        self.Bind(wx.EVT_MENU, self.on_favourite, fav_item)
        self.Bind(wx.EVT_MENU, self.on_bookmark, bookmark_item)
        self.Bind(wx.EVT_MENU, self.on_copy_post, copy_item)
        self.Bind(wx.EVT_MENU, self.on_open_post_url, open_url_item)
        self.Bind(wx.EVT_MENU, self.on_post_activated, view_post_item)
        self.Bind(wx.EVT_MENU, self.on_view_thread, thread_item)
        self.Bind(wx.EVT_MENU, self.on_follow_user, follow_item)
        self.Bind(wx.EVT_MENU, self.on_block_user, block_item)
        self.Bind(wx.EVT_MENU, self.on_mute_user, mute_item)
        self.Bind(wx.EVT_MENU, self.on_view_profile, profile_item)
        self.Bind(wx.EVT_MENU, self.on_open_user_timeline, user_tl_item)
        self.Bind(wx.EVT_MENU, self.on_view_media, view_media_item)
        self.Bind(wx.EVT_MENU, self.on_view_boosters, view_boosters_item)
        self.Bind(wx.EVT_MENU, self.on_view_favouriters, view_favouriters_item)
        self.Bind(wx.EVT_MENU, self.on_view_edit_history, view_history_item)
        self.Bind(wx.EVT_MENU, self.on_mute_conversation, mute_convo_item)
        self.Bind(wx.EVT_MENU, self.on_report, report_item)
        self.Bind(wx.EVT_MENU, self.on_dm_user, dm_item)
        self.posts_list.PopupMenu(menu)
        menu.Destroy()

    def setup_accelerators(self):
        # Accelerators are now handled via EVT_CHAR_HOOK for focus-aware behaviour
        pass

    def on_focus_compose(self, event):
        self.toot_input.SetFocus()

    def on_reply(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        me_acct = self.me['acct']
        author_acct = source['account']['acct']
        # Build mention list using the API mentions array for correct @user@instance handles
        mentions = []
        if author_acct != me_acct:
            mentions.append(f"@{author_acct}")
        for m in source.get('mentions', []):
            acct = m.get('acct', '')
            if acct and acct != me_acct and acct != author_acct:
                mentions.append(f"@{acct}")
        reply_users = " ".join(mentions)
        
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

    def on_quote(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status

        # Check quote approval
        quote_approval = source.get('quote_approval')
        if quote_approval:
            current_user = quote_approval.get('current_user') if isinstance(quote_approval, dict) else getattr(quote_approval, 'current_user', None)
            if current_user in ('denied', 'unknown'):
                if errorsnd: errorsnd.play()
                wx.MessageBox("This post cannot be quoted. The author has disabled quoting.", "Quote Not Allowed", wx.OK | wx.ICON_INFORMATION)
                return

        status_url = source.get('url') or source.get('uri', '')
        status_id = source['id']

        dialog = wx.Dialog(self, title="Quote Post", size=(500, 300))
        panel = wx.Panel(dialog)
        vbox = wx.BoxSizer(wx.VERTICAL)
        quote_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
        quote_text.SetFocus()
        privacy_label = wx.StaticText(panel, label="P&rivacy:")
        quote_privacy_choice = wx.Choice(panel, choices=self.privacy_options)
        send_button = wx.Button(panel, label="&Post")
        cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")

        try:
            quote_privacy_choice.SetSelection(self.privacy_values.index(status.get("visibility", "public")))
        except ValueError: quote_privacy_choice.SetSelection(0)

        def send_quote(e):
            text = quote_text.GetValue().strip()
            if not text: return wx.MessageBox("Quote text cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
            visibility = self.privacy_values[quote_privacy_choice.GetSelection()]
            try:
                # Try quoted_status_id (Mastodon 4.5+) first, then URL fallback
                posted = False
                try:
                    self.mastodon.status_post(text, quoted_status_id=status_id, visibility=visibility)
                    posted = True
                except Exception:
                    pass
                if not posted:
                    self.mastodon.status_post(f"{text}\n\n{status_url}", visibility=visibility)
                if tootsnd: tootsnd.play()
                dialog.Close()
            except Exception as ex: wx.MessageBox(f"Error sending quote: {ex}", "Error", wx.OK | wx.ICON_ERROR)
        send_button.Bind(wx.EVT_BUTTON, send_quote)

        vbox.Add(wx.StaticText(panel, label="&Quote"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        vbox.Add(quote_text, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        vbox.Add(privacy_label, 0, wx.LEFT | wx.RIGHT, 10)
        vbox.Add(quote_privacy_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
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
            source = status.get('reblog') or status
            if status["favourited"]:
                self.mastodon.status_unfavourite(status["id"]); unfavsnd and unfavsnd.play()
                # Remove from favourites timeline
                fav_data = self.timelines_data.get("favourites", [])
                for i, s in enumerate(fav_data):
                    if s.get('id') == source.get('id'):
                        fav_data.pop(i)
                        if self.timeline_tree.GetSelection() == self.timeline_nodes.get("favourites"):
                            self.posts_list.Delete(i)
                        break
            else:
                self.mastodon.status_favourite(status["id"]); favsnd and favsnd.play()
                # Add to favourites timeline
                self.timelines_data.setdefault("favourites", []).insert(0, source)
                if self.timeline_tree.GetSelection() == self.timeline_nodes.get("favourites"):
                    row, avatar_url = self.row_from_status(source)
                    if row:
                        self.posts_list.Insert(row, 0, avatar_url)
                        self.queue_avatar_download(avatar_url)
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
                if open_timelinesnd: open_timelinesnd.play()
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
                    if unfollowsnd: unfollowsnd.play()
                    wx.MessageBox(f"Unfollowed {display}.", "Follow")
            else:
                self.mastodon.account_follow(account['id'])
                if followsnd: followsnd.play()
                wx.MessageBox(f"Now following {display}.", "Follow")
        except Exception as e:
            if errorsnd: errorsnd.play()
            wx.MessageBox(f"Error: {e}", "Follow Error")

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
        except Exception as e:
            if errorsnd: errorsnd.play()
            wx.MessageBox(f"Error: {e}", "Block Error")

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
                if searchsnd: searchsnd.play()
                timeline_key = f"search:{query}"
                self.timelines_data[timeline_key] = []
                if timeline_key not in self.timeline_nodes:
                    node = self.timeline_tree.AppendItem(self.root, f"Search: {query}")
                    self.timeline_nodes[timeline_key] = node
                    if open_timelinesnd: open_timelinesnd.play()
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

    def on_dismiss_notification(self, event):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if key not in ("notifications", "mentions"): return
        sel = self.posts_list.GetSelection()
        if sel == wx.NOT_FOUND: return
        try:
            notif = self.timelines_data[key][sel]
            self.mastodon.notifications_dismiss(notif['id'])
            self.timelines_data[key].pop(sel)
            self.posts_list.Delete(sel)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_clear_all_notifications(self, event):
        if wx.MessageBox("Clear all notifications? This cannot be undone.", "Confirm", wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        try:
            self.mastodon.notifications_clear()
            self.timelines_data["notifications"] = []
            self.timelines_data["mentions"] = []
            key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
            if key in ("notifications", "mentions"):
                self.posts_list.Clear()
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_accept_follow_request(self, event):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        sel = self.posts_list.GetSelection()
        if sel == wx.NOT_FOUND or not key: return
        try:
            notif = self.timelines_data[key][sel]
            account = notif.get('account', {})
            self.mastodon.follow_request_authorize(account['id'])
            wx.MessageBox(f"Accepted follow request from {account.get('display_name', account.get('username', ''))}.", "Follow Request")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_reject_follow_request(self, event):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        sel = self.posts_list.GetSelection()
        if sel == wx.NOT_FOUND or not key: return
        try:
            notif = self.timelines_data[key][sel]
            account = notif.get('account', {})
            self.mastodon.follow_request_reject(account['id'])
            wx.MessageBox(f"Rejected follow request from {account.get('display_name', account.get('username', ''))}.", "Follow Request")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_favourites_timeline(self, event):
        if favoritessnd: favoritessnd.play()
        self.timeline_tree.SelectItem(self.timeline_nodes.get("favourites", self.timeline_tree.GetSelection()))

    def on_view_bookmarks_timeline(self, event):
        self.timeline_tree.SelectItem(self.timeline_nodes.get("bookmarks", self.timeline_tree.GetSelection()))

    def on_instance_info(self, event):
        try:
            instance = self.mastodon.instance()
            title = instance.get('title', 'Unknown')
            desc = strip_html(instance.get('description', '') or instance.get('short_description', '') or '')
            version = instance.get('version', 'Unknown')
            users = instance.get('stats', {}).get('user_count', '?')
            statuses = instance.get('stats', {}).get('status_count', '?')
            domains = instance.get('stats', {}).get('domain_count', '?')
            uri = instance.get('uri', '')
            contact = instance.get('contact_account', {})
            admin = contact.get('display_name') or contact.get('username', 'Unknown') if contact else 'Unknown'

            info = f"""Instance: {title}
URI: {uri}
Version: {version}
Admin: {admin}

Users: {users}
Posts: {statuses}
Known domains: {domains}

Description:
{desc}"""
            dlg = wx.Dialog(self, title=f"Instance Info: {title}", size=(550, 400))
            panel = wx.Panel(dlg)
            sizer = wx.BoxSizer(wx.VERTICAL)
            text = wx.TextCtrl(panel, value=info, style=wx.TE_MULTILINE | wx.TE_READONLY)
            sizer.Add(text, 1, wx.EXPAND | wx.ALL, 10)
            close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
            sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
            panel.SetSizer(sizer)
            if is_windows_dark_mode():
                dc = wx.Colour(40, 40, 40)
                lt = wx.WHITE
                WxMswDarkMode().enable(dlg)
                dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
                text.SetBackgroundColour(dc); text.SetForegroundColour(lt)
                close_btn.SetBackgroundColour(dc); close_btn.SetForegroundColour(lt)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_edit_my_profile(self, event):
        if not self.mastodon or not self.me: return
        dlg = wx.Dialog(self, title="Edit My Profile", size=(550, 500))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(panel, label="Display &Name:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        name_input = wx.TextCtrl(panel, size=(-1, 30))
        name_input.SetValue(self.me.get('display_name', ''))
        sizer.Add(name_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        sizer.Add(wx.StaticText(panel, label="&Bio:"), 0, wx.LEFT | wx.RIGHT, 10)
        bio_input = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 120))
        # Get source bio (plain text) if available
        source = self.me.get('source', {})
        bio_input.SetValue(source.get('note', '') or strip_html(self.me.get('note', '')))
        sizer.Add(bio_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        # Metadata fields (up to 4)
        sizer.Add(wx.StaticText(panel, label="Profile &Fields:"), 0, wx.LEFT | wx.RIGHT, 10)
        fields = self.me.get('source', {}).get('fields', []) or self.me.get('fields', [])
        field_inputs = []
        for i in range(4):
            fsizer = wx.BoxSizer(wx.HORIZONTAL)
            name_lbl = wx.StaticText(panel, label=f"Label {i+1}:")
            name_ctrl = wx.TextCtrl(panel, size=(150, -1))
            val_lbl = wx.StaticText(panel, label="Value:")
            val_ctrl = wx.TextCtrl(panel, size=(250, -1))
            if i < len(fields):
                name_ctrl.SetValue(fields[i].get('name', ''))
                val_ctrl.SetValue(strip_html(fields[i].get('value', '')))
            fsizer.Add(name_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 3)
            fsizer.Add(name_ctrl, 0, wx.RIGHT, 8)
            fsizer.Add(val_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 3)
            fsizer.Add(val_ctrl, 1)
            sizer.Add(fsizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
            field_inputs.append((name_ctrl, val_ctrl))
        
        locked_check = wx.CheckBox(panel, label="&Lock account (require follow approval)")
        locked_check.SetValue(self.me.get('locked', False))
        sizer.Add(locked_check, 0, wx.LEFT | wx.RIGHT, 10)
        
        bot_check = wx.CheckBox(panel, label="Mark as &bot account")
        bot_check.SetValue(self.me.get('bot', False))
        sizer.Add(bot_check, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(panel, label="&Save")
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        btn_sizer.Add(save_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        
        def on_save(e):
            try:
                new_fields = []
                for name_ctrl, val_ctrl in field_inputs:
                    n = name_ctrl.GetValue().strip()
                    v = val_ctrl.GetValue().strip()
                    if n or v:
                        new_fields.append((n, v))
                
                self.mastodon.account_update_credentials(
                    display_name=name_input.GetValue().strip(),
                    note=bio_input.GetValue().strip(),
                    locked=locked_check.IsChecked(),
                    bot=bot_check.IsChecked(),
                    fields=new_fields if new_fields else None,
                )
                self.me = self.mastodon.me()
                wx.MessageBox("Profile updated successfully!", "Profile")
                dlg.Close()
            except Exception as ex:
                wx.MessageBox(f"Error updating profile: {ex}", "Error")
        
        save_btn.Bind(wx.EVT_BUTTON, on_save)
        
        all_widgets = [name_input, bio_input, locked_check, bot_check, save_btn, cancel_btn]
        all_labels = list(panel.GetChildren())
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in panel.GetChildren():
                w.SetBackgroundColour(dc)
                w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_find_in_timeline(self, event):
        key = next((k for k, v in self.timeline_nodes.items() if v == self.timeline_tree.GetSelection()), None)
        if not key or not self.timelines_data.get(key):
            wx.MessageBox("No timeline selected or timeline is empty.", "Find")
            return
        dlg = wx.TextEntryDialog(self, "Search text:", "Find in Timeline")
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        query = dlg.GetValue().strip().lower()
        dlg.Destroy()
        if not query: return
        
        items = self.timelines_data[key]
        results = []
        for i, item in enumerate(items):
            status = item.get("status") if key == "notifications" else item
            if not status: continue
            source = status.get('reblog') or status
            content = strip_html((source.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', ' ')).strip().lower()
            author = (source.get('account', {}).get('display_name', '') or source.get('account', {}).get('username', '')).lower()
            spoiler = (source.get('spoiler_text', '') or '').lower()
            if query in content or query in author or query in spoiler:
                results.append((i, status))
        
        if not results:
            wx.MessageBox(f"No posts found matching '{query}'.", "Find")
            return
        
        if len(results) == 1:
            self.posts_list.Select(results[0][0])
            self.posts_list.EnsureVisible(results[0][0])
            self.posts_list.SetFocus()
            return
        
        result_dlg = wx.Dialog(self, title=f"Find Results ({len(results)} matches)", size=(600, 400))
        panel = wx.Panel(result_dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        results_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 300))
        for idx, status in results:
            source = status.get('reblog') or status
            author = source.get('account', {}).get('display_name') or source.get('account', {}).get('username', '')
            content = strip_html((source.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', ' ')).strip()[:120]
            results_listbox.Append(f"{author}: {content}")
        sizer.Add(results_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        go_btn = wx.Button(panel, label="&Go to Post")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        btn_sizer.Add(go_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_go(e):
            sel = results_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            idx = results[sel][0]
            self.posts_list.Select(idx)
            self.posts_list.EnsureVisible(idx)
            self.posts_list.SetFocus()
            result_dlg.Close()
        
        go_btn.Bind(wx.EVT_BUTTON, on_go)
        results_listbox.Bind(wx.EVT_LISTBOX_DCLICK, on_go)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(result_dlg)
            result_dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [results_listbox, go_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        result_dlg.ShowModal()
        result_dlg.Destroy()

    def on_scheduled_posts(self, event):
        dlg = wx.Dialog(self, title="Scheduled Posts", size=(600, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        scheduled_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 280))
        sizer.Add(scheduled_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        scheduled = []
        try:
            scheduled = self.mastodon.scheduled_statuses()
            for s in scheduled:
                sched_time = s.get('scheduled_at', '')
                if hasattr(sched_time, 'strftime'):
                    sched_time = sched_time.strftime('%Y-%m-%d %H:%M')
                params = s.get('params', {})
                text = params.get('text', '')[:100]
                scheduled_listbox.Append(f"[{sched_time}] {text}")
        except Exception as e:
            wx.MessageBox(f"Error loading scheduled posts: {e}", "Error")
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        edit_btn = wx.Button(panel, label="&Edit Time")
        delete_btn = wx.Button(panel, label="&Delete")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        btn_sizer.Add(edit_btn, 0, wx.ALL, 5)
        btn_sizer.Add(delete_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_edit_time(e):
            sel = scheduled_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            s = scheduled[sel]
            time_dlg = wx.TextEntryDialog(dlg, "New scheduled time (YYYY-MM-DD HH:MM):", "Reschedule")
            if time_dlg.ShowModal() == wx.ID_OK:
                try:
                    from datetime import datetime, timezone
                    new_time = datetime.strptime(time_dlg.GetValue().strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    self.mastodon.scheduled_status_update(s['id'], scheduled_at=new_time)
                    scheduled[sel]['scheduled_at'] = new_time
                    params = s.get('params', {})
                    text = params.get('text', '')[:100]
                    scheduled_listbox.SetString(sel, f"[{new_time.strftime('%Y-%m-%d %H:%M')}] {text}")
                except ValueError:
                    wx.MessageBox("Invalid format. Use YYYY-MM-DD HH:MM.", "Error")
                except Exception as ex:
                    wx.MessageBox(f"Error: {ex}", "Error")
            time_dlg.Destroy()
        
        def on_delete_scheduled(e):
            sel = scheduled_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            if wx.MessageBox("Delete this scheduled post?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                try:
                    self.mastodon.scheduled_status_delete(scheduled[sel]['id'])
                    scheduled.pop(sel)
                    scheduled_listbox.Delete(sel)
                except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
        
        edit_btn.Bind(wx.EVT_BUTTON, on_edit_time)
        delete_btn.Bind(wx.EVT_BUTTON, on_delete_scheduled)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [scheduled_listbox, edit_btn, delete_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_explore(self, event):
        dlg = wx.Dialog(self, title="Explore / Discover", size=(700, 500))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        notebook = wx.Notebook(panel)
        
        # Trending posts tab
        posts_panel = wx.Panel(notebook)
        posts_sizer = wx.BoxSizer(wx.VERTICAL)
        posts_list = wx.ListBox(posts_panel, style=wx.LB_SINGLE, size=(-1, 350))
        posts_sizer.Add(posts_list, 1, wx.EXPAND | wx.ALL, 5)
        posts_panel.SetSizer(posts_sizer)
        notebook.AddPage(posts_panel, "Trending Posts")
        
        # Trending hashtags tab
        tags_panel = wx.Panel(notebook)
        tags_sizer = wx.BoxSizer(wx.VERTICAL)
        tags_list = wx.ListBox(tags_panel, style=wx.LB_SINGLE, size=(-1, 300))
        tags_sizer.Add(tags_list, 1, wx.EXPAND | wx.ALL, 5)
        follow_tag_btn = wx.Button(tags_panel, label="&Open Hashtag Timeline")
        tags_sizer.Add(follow_tag_btn, 0, wx.ALL, 5)
        tags_panel.SetSizer(tags_sizer)
        notebook.AddPage(tags_panel, "Trending Hashtags")
        
        # Trending links tab
        links_panel = wx.Panel(notebook)
        links_sizer = wx.BoxSizer(wx.VERTICAL)
        links_list = wx.ListBox(links_panel, style=wx.LB_SINGLE, size=(-1, 300))
        links_sizer.Add(links_list, 1, wx.EXPAND | wx.ALL, 5)
        open_link_btn = wx.Button(links_panel, label="&Open Link in Browser")
        links_sizer.Add(open_link_btn, 0, wx.ALL, 5)
        links_panel.SetSizer(links_sizer)
        notebook.AddPage(links_panel, "Trending Links")
        
        sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        panel.SetSizer(sizer)
        
        trending_posts = []
        trending_tags = []
        trending_links = []
        try:
            trending_posts = self.mastodon.trending_statuses(limit=20)
            for post in trending_posts:
                author = post['account'].get('display_name') or post['account'].get('username', '')
                content = strip_html((post.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', ' ')).strip()[:150]
                posts_list.Append(f"{author}: {content}")
        except Exception: pass
        try:
            trending_tags = self.mastodon.trending_tags(limit=20)
            for tag in trending_tags:
                history = tag.get('history', [{}])
                uses = sum(int(h.get('uses', 0)) for h in history[:1])
                tags_list.Append(f"#{tag['name']} ({uses} recent uses)")
        except Exception: pass
        try:
            trending_links = self.mastodon.trending_links(limit=20)
            for link in trending_links:
                links_list.Append(f"{link.get('title', 'Untitled')} - {link.get('url', '')}")
        except Exception: pass
        
        def on_open_trending_post(e):
            sel = posts_list.GetSelection()
            if sel != wx.NOT_FOUND and sel < len(trending_posts):
                post_dlg = PostDetailsDialog(dlg, self.mastodon, trending_posts[sel], self.me, votesnd=votesnd)
                post_dlg.ShowModal()
                post_dlg.Destroy()
        
        def on_open_tag_timeline(e):
            sel = tags_list.GetSelection()
            if sel != wx.NOT_FOUND and sel < len(trending_tags):
                tag_name = trending_tags[sel]['name']
                timeline_key = f"hashtag:{tag_name}"
                self.timelines_data[timeline_key] = []
                if timeline_key not in self.timeline_nodes:
                    node = self.timeline_tree.AppendItem(self.root, f"#{tag_name}")
                    self.timeline_nodes[timeline_key] = node
                    if open_timelinesnd: open_timelinesnd.play()
                dlg.Close()
                self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
                threading.Thread(target=self.load_timeline, args=(timeline_key,), daemon=True).start()
        
        def on_open_link(e):
            sel = links_list.GetSelection()
            if sel != wx.NOT_FOUND and sel < len(trending_links):
                webbrowser.open(trending_links[sel].get('url', ''))
        
        posts_list.Bind(wx.EVT_LISTBOX_DCLICK, on_open_trending_post)
        follow_tag_btn.Bind(wx.EVT_BUTTON, on_open_tag_timeline)
        tags_list.Bind(wx.EVT_LISTBOX_DCLICK, on_open_tag_timeline)
        open_link_btn.Bind(wx.EVT_BUTTON, on_open_link)
        links_list.Bind(wx.EVT_LISTBOX_DCLICK, on_open_link)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            for w in [dlg, panel, posts_panel, tags_panel, links_panel]:
                w.SetBackgroundColour(dc)
            for w in [posts_list, tags_list, links_list, follow_tag_btn, open_link_btn, close_btn, notebook]:
                w.SetBackgroundColour(dc)
                w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_lists(self, event):
        dlg = wx.Dialog(self, title="Lists", size=(500, 450))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        lists_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 250))
        sizer.Add(lists_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        user_lists = []
        try:
            user_lists = self.mastodon.lists()
            for lst in user_lists:
                lists_listbox.Append(lst.get('title', 'Untitled'))
        except Exception: pass
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        open_btn = wx.Button(panel, label="&Open Timeline")
        create_btn = wx.Button(panel, label="&Create List")
        delete_btn = wx.Button(panel, label="&Delete List")
        manage_btn = wx.Button(panel, label="&Manage Members")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="C&lose")
        btn_sizer.Add(open_btn, 0, wx.ALL, 3)
        btn_sizer.Add(create_btn, 0, wx.ALL, 3)
        btn_sizer.Add(delete_btn, 0, wx.ALL, 3)
        btn_sizer.Add(manage_btn, 0, wx.ALL, 3)
        btn_sizer.Add(close_btn, 0, wx.ALL, 3)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_open_list_timeline(e):
            sel = lists_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            lst = user_lists[sel]
            timeline_key = f"list:{lst['id']}"
            self.timelines_data[timeline_key] = []
            if timeline_key not in self.timeline_nodes:
                node = self.timeline_tree.AppendItem(self.root, f"List: {lst['title']}")
                self.timeline_nodes[timeline_key] = node
                if open_timelinesnd: open_timelinesnd.play()
            dlg.Close()
            self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
            threading.Thread(target=self.load_timeline, args=(timeline_key,), daemon=True).start()
        
        def on_create_list(e):
            name_dlg = wx.TextEntryDialog(dlg, "List name:", "Create List")
            if name_dlg.ShowModal() == wx.ID_OK:
                name = name_dlg.GetValue().strip()
                if name:
                    try:
                        new_list = self.mastodon.list_create(name)
                        user_lists.append(new_list)
                        lists_listbox.Append(new_list.get('title', 'Untitled'))
                    except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
            name_dlg.Destroy()
        
        def on_delete_list(e):
            sel = lists_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            lst = user_lists[sel]
            if wx.MessageBox(f"Delete list '{lst['title']}'?", "Confirm", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                try:
                    self.mastodon.list_delete(lst['id'])
                    user_lists.pop(sel)
                    lists_listbox.Delete(sel)
                except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
        
        def on_manage_members(e):
            sel = lists_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            lst = user_lists[sel]
            self._manage_list_members(dlg, lst)
        
        open_btn.Bind(wx.EVT_BUTTON, on_open_list_timeline)
        lists_listbox.Bind(wx.EVT_LISTBOX_DCLICK, on_open_list_timeline)
        create_btn.Bind(wx.EVT_BUTTON, on_create_list)
        delete_btn.Bind(wx.EVT_BUTTON, on_delete_list)
        manage_btn.Bind(wx.EVT_BUTTON, on_manage_members)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [lists_listbox, open_btn, create_btn, delete_btn, manage_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def _manage_list_members(self, parent, lst):
        dlg = wx.Dialog(parent, title=f"Members of '{lst['title']}'", size=(500, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        members_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 250))
        sizer.Add(members_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        members = []
        try:
            members = self.mastodon.list_accounts(lst['id'], limit=80)
            for acc in members:
                display = acc.get('display_name') or acc.get('username', '')
                members_listbox.Append(f"{display} (@{acc.get('acct', '')})")
        except Exception: pass
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(panel, label="&Add User")
        remove_btn = wx.Button(panel, label="&Remove User")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        btn_sizer.Add(add_btn, 0, wx.ALL, 5)
        btn_sizer.Add(remove_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_add_user(e):
            search_dlg = wx.TextEntryDialog(dlg, "Search for user to add:", "Add to List")
            if search_dlg.ShowModal() == wx.ID_OK:
                query = search_dlg.GetValue().strip()
                if query:
                    try:
                        results = self.mastodon.account_search(query, limit=10)
                        if results:
                            choices = [f"{a.get('display_name', '')} (@{a.get('acct', '')})" for a in results]
                            sel_dlg = wx.SingleChoiceDialog(dlg, "Select user:", "Add to List", choices)
                            if sel_dlg.ShowModal() == wx.ID_OK:
                                acc = results[sel_dlg.GetSelection()]
                                self.mastodon.list_accounts_add(lst['id'], [acc['id']])
                                members.append(acc)
                                members_listbox.Append(f"{acc.get('display_name', '')} (@{acc.get('acct', '')})")
                            sel_dlg.Destroy()
                        else:
                            wx.MessageBox("No users found.", "Search")
                    except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
            search_dlg.Destroy()
        
        def on_remove_user(e):
            sel = members_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            acc = members[sel]
            try:
                self.mastodon.list_accounts_delete(lst['id'], [acc['id']])
                members.pop(sel)
                members_listbox.Delete(sel)
            except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
        
        add_btn.Bind(wx.EVT_BUTTON, on_add_user)
        remove_btn.Bind(wx.EVT_BUTTON, on_remove_user)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [members_listbox, add_btn, remove_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_followed_hashtags(self, event):
        dlg = wx.Dialog(self, title="Followed Hashtags", size=(500, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        tags_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 250))
        sizer.Add(tags_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        followed_tags = []
        try:
            followed_tags = self.mastodon.followed_tags()
            for tag in followed_tags:
                tags_listbox.Append(f"#{tag['name']}")
        except Exception: pass
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        open_btn = wx.Button(panel, label="&Open Timeline")
        follow_btn = wx.Button(panel, label="&Follow New Hashtag")
        unfollow_btn = wx.Button(panel, label="&Unfollow")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
        btn_sizer.Add(open_btn, 0, wx.ALL, 3)
        btn_sizer.Add(follow_btn, 0, wx.ALL, 3)
        btn_sizer.Add(unfollow_btn, 0, wx.ALL, 3)
        btn_sizer.Add(close_btn, 0, wx.ALL, 3)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_open_tag(e):
            sel = tags_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            tag_name = followed_tags[sel]['name']
            timeline_key = f"hashtag:{tag_name}"
            self.timelines_data[timeline_key] = []
            if timeline_key not in self.timeline_nodes:
                node = self.timeline_tree.AppendItem(self.root, f"#{tag_name}")
                self.timeline_nodes[timeline_key] = node
            dlg.Close()
            self.timeline_tree.SelectItem(self.timeline_nodes[timeline_key])
            threading.Thread(target=self.load_timeline, args=(timeline_key,), daemon=True).start()
        
        def on_follow_new(e):
            tag_dlg = wx.TextEntryDialog(dlg, "Hashtag to follow (without #):", "Follow Hashtag")
            if tag_dlg.ShowModal() == wx.ID_OK:
                tag_name = tag_dlg.GetValue().strip().lstrip('#')
                if tag_name:
                    try:
                        tag = self.mastodon.hashtag_follow(tag_name)
                        followed_tags.append(tag)
                        tags_listbox.Append(f"#{tag['name']}")
                    except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
            tag_dlg.Destroy()
        
        def on_unfollow(e):
            sel = tags_listbox.GetSelection()
            if sel == wx.NOT_FOUND: return
            try:
                self.mastodon.hashtag_unfollow(followed_tags[sel]['name'])
                followed_tags.pop(sel)
                tags_listbox.Delete(sel)
            except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
        
        open_btn.Bind(wx.EVT_BUTTON, on_open_tag)
        tags_listbox.Bind(wx.EVT_LISTBOX_DCLICK, on_open_tag)
        follow_btn.Bind(wx.EVT_BUTTON, on_follow_new)
        unfollow_btn.Bind(wx.EVT_BUTTON, on_unfollow)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [tags_listbox, open_btn, follow_btn, unfollow_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_view_media(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        attachments = source.get('media_attachments', [])
        if not attachments:
            wx.MessageBox("This post has no media attachments.", "No Media")
            return
        
        dlg = wx.Dialog(self, title=f"Media Attachments ({len(attachments)})", size=(600, 450))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        media_listbox = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 200))
        for i, att in enumerate(attachments):
            atype = att.get('type', 'unknown').capitalize()
            desc = att.get('description') or 'No description'
            media_listbox.Append(f"[{atype}] {desc}")
        sizer.Add(media_listbox, 1, wx.EXPAND | wx.ALL, 10)
        
        desc_label = wx.StaticText(panel, label="Description:")
        desc_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 80))
        sizer.Add(desc_label, 0, wx.LEFT | wx.RIGHT, 10)
        sizer.Add(desc_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        def on_media_sel(e):
            sel = media_listbox.GetSelection()
            if sel != wx.NOT_FOUND:
                desc_text.SetValue(attachments[sel].get('description') or 'No description provided')
        media_listbox.Bind(wx.EVT_LISTBOX, on_media_sel)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        open_btn = wx.Button(panel, label="Open in &Browser")
        copy_url_btn = wx.Button(panel, label="&Copy URL")
        close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="C&lose")
        btn_sizer.Add(open_btn, 0, wx.ALL, 5)
        btn_sizer.Add(copy_url_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_open_media(e):
            sel = media_listbox.GetSelection()
            if sel != wx.NOT_FOUND:
                url = attachments[sel].get('url') or attachments[sel].get('remote_url', '')
                if url: webbrowser.open(url)
        
        def on_copy_url(e):
            sel = media_listbox.GetSelection()
            if sel != wx.NOT_FOUND:
                url = attachments[sel].get('url') or attachments[sel].get('remote_url', '')
                if pyperclip: pyperclip.copy(url)
                elif wx.TheClipboard.Open():
                    wx.TheClipboard.SetData(wx.TextDataObject(url))
                    wx.TheClipboard.Close()
        
        open_btn.Bind(wx.EVT_BUTTON, on_open_media)
        media_listbox.Bind(wx.EVT_LISTBOX_DCLICK, on_open_media)
        copy_url_btn.Bind(wx.EVT_BUTTON, on_copy_url)
        
        if media_listbox.GetCount() > 0:
            media_listbox.SetSelection(0)
            on_media_sel(None)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [media_listbox, desc_label, desc_text, open_btn, copy_url_btn, close_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_view_boosters(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        try:
            accounts = self.mastodon.status_reblogged_by(source['id'])
            if not accounts:
                wx.MessageBox("No one has boosted this post yet.", "Boosters")
                return
            self._show_account_list(f"Boosted by ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_favouriters(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        try:
            accounts = self.mastodon.status_favourited_by(source['id'])
            if not accounts:
                wx.MessageBox("No one has favourited this post yet.", "Favouriters")
                return
            self._show_account_list(f"Favourited by ({len(accounts)})", accounts)
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_view_edit_history(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        try:
            history = self.mastodon.status_history(source['id'])
            if not history or len(history) < 2:
                wx.MessageBox("This post has no edit history.", "Edit History")
                return
            dlg = wx.Dialog(self, title=f"Edit History ({len(history)} versions)", size=(600, 400))
            panel = wx.Panel(dlg)
            sizer = wx.BoxSizer(wx.VERTICAL)
            versions_list = wx.ListBox(panel, style=wx.LB_SINGLE, size=(-1, 150))
            for i, ver in enumerate(history):
                label = f"Version {len(history) - i}"
                if ver.get('created_at'):
                    label += f" - {ver['created_at'].strftime('%Y-%m-%d %H:%M')}"
                versions_list.Append(label)
            sizer.Add(versions_list, 0, wx.EXPAND | wx.ALL, 10)
            
            content_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
            sizer.Add(wx.StaticText(panel, label="Content:"), 0, wx.LEFT | wx.RIGHT, 10)
            sizer.Add(content_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
            
            def on_version_sel(e):
                sel = versions_list.GetSelection()
                if sel != wx.NOT_FOUND:
                    ver = history[sel]
                    text = strip_html((ver.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
                    if ver.get('spoiler_text'):
                        text = f"CW: {ver['spoiler_text']}\n\n{text}"
                    content_text.SetValue(text)
            versions_list.Bind(wx.EVT_LISTBOX, on_version_sel)
            
            close_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Close")
            sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
            panel.SetSizer(sizer)
            
            if versions_list.GetCount() > 0:
                versions_list.SetSelection(0)
                on_version_sel(None)
            
            if is_windows_dark_mode():
                dc = wx.Colour(40, 40, 40)
                lt = wx.WHITE
                WxMswDarkMode().enable(dlg)
                dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
                for w in [versions_list, content_text, close_btn]:
                    w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
            
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e: wx.MessageBox(f"Error: {e}", "Error")

    def on_mute_conversation(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        try:
            if source.get('muted'):
                self.mastodon.status_unmute(source['id'])
                source['muted'] = False
                wx.MessageBox("Conversation unmuted.", "Mute")
            else:
                self.mastodon.status_mute(source['id'])
                source['muted'] = True
                wx.MessageBox("Conversation muted. You will no longer receive notifications from this thread.", "Mute")
        except Exception as e: wx.MessageBox(f"Error: {e}", "Mute Error")

    def on_report(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        display = account.get('display_name') or account.get('username', '')
        
        dlg = wx.Dialog(self, title=f"Report {display}", size=(500, 350))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label=f"Report {display} (@{account.get('acct', '')})"), 0, wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label="&Reason (optional):"), 0, wx.LEFT | wx.RIGHT, 10)
        reason_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 100))
        sizer.Add(reason_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        include_post = wx.CheckBox(panel, label="Include this specific post in the report")
        include_post.SetValue(True)
        sizer.Add(include_post, 0, wx.LEFT | wx.RIGHT, 10)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        submit_btn = wx.Button(panel, label="&Submit Report")
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        btn_sizer.Add(submit_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        panel.SetSizer(sizer)
        
        def on_submit(e):
            comment = reason_text.GetValue().strip()
            status_ids = [source['id']] if include_post.IsChecked() else []
            try:
                self.mastodon.report(account['id'], status_ids=status_ids, comment=comment)
                wx.MessageBox("Report submitted successfully.", "Report")
                dlg.Close()
            except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error")
        
        submit_btn.Bind(wx.EVT_BUTTON, on_submit)
        
        if is_windows_dark_mode():
            dc = wx.Colour(40, 40, 40)
            lt = wx.WHITE
            WxMswDarkMode().enable(dlg)
            dlg.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
            for w in [reason_text, include_post, submit_btn, cancel_btn]:
                w.SetBackgroundColour(dc); w.SetForegroundColour(lt)
        
        dlg.ShowModal()
        dlg.Destroy()

    def on_dm_user(self, event):
        status, _ = self.get_selected_status()
        if not status: return
        source = status.get('reblog') or status
        account = source['account']
        display = account.get('display_name') or account.get('username', '')
        acct = account.get('acct', '')
        
        dialog = wx.Dialog(self, title=f"Direct Message to {display}", size=(500, 300))
        panel = wx.Panel(dialog)
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(wx.StaticText(panel, label=f"Send a direct &message to {display} (@{acct}):"), 0, wx.ALL, 10)
        dm_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
        dm_text.SetValue(f"@{acct} ")
        dm_text.SetInsertionPointEnd()
        vbox.Add(dm_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        send_btn = wx.Button(panel, label="&Send")
        cancel_btn = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        btn_sizer.Add(send_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        vbox.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(vbox)
        
        def do_send(e):
            text = dm_text.GetValue().strip()
            if not text: return wx.MessageBox("Message cannot be empty.", "Error", wx.OK | wx.ICON_ERROR)
            try:
                self.mastodon.status_post(text, visibility="direct")
                if send_dmsnd: send_dmsnd.play()
                elif dmsnd: dmsnd.play()
                dialog.Close()
            except Exception as ex: wx.MessageBox(f"Error: {ex}", "Error", wx.OK | wx.ICON_ERROR)
        
        send_btn.Bind(wx.EVT_BUTTON, do_send)
        dialog.ShowModal()
        dialog.Destroy()

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
            self._update_media_file_widgets()
        dlg.Destroy()

    def on_remove_media(self, event):
        sel = self.media_list.GetSelection()
        if sel == wx.NOT_FOUND: return
        self.media_files.pop(sel)
        self.media_list.Delete(sel)
        self.alt_text_input.SetValue("")
        self._update_media_file_widgets()

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
        self.add_media_button.Show(show)
        self.add_media_button.Enable(show)
        self.media_sizer.Show(show)
        # Only show file-dependent widgets if there are files
        has_files = show and len(self.media_files) > 0
        for widget in self.media_file_widgets:
            widget.Show(has_files)
            widget.Enable(has_files)
        self.panel.Layout()

    def _update_media_file_widgets(self):
        has_files = len(self.media_files) > 0
        for widget in self.media_file_widgets:
            widget.Show(has_files)
            widget.Enable(has_files)
        self.panel.Layout()

    def on_toggle_schedule(self, event):
        show = self.schedule_toggle.IsChecked()
        for widget in self.schedule_widgets:
            widget.Show(show)
        self.panel.Layout()

    def on_post_activated(self, event): self.show_post_details()

    def on_post(self, event):
        status_text = self.toot_input.GetValue().strip()
        spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
        visibility = self.privacy_values[self.privacy_choice.GetSelection()]
        language = self.language_codes[self.language_choice.GetSelection()] or None
        poll_data = None
        if self.poll_toggle.IsChecked():
            options = [opt.GetValue().strip() for opt in self.poll_option_inputs if opt.GetValue().strip()]
            if len(options) < 2: return wx.MessageBox("A poll must have at least two options.", "Poll Error", wx.OK | wx.ICON_ERROR)
            poll_data = {'options': options, 'expires_in': self.poll_duration_seconds[self.poll_duration_choice.GetSelection()], 'multiple': self.poll_multiple_choice.IsChecked()}
        if not status_text and not poll_data and not self.media_files:
            if errorsnd: errorsnd.play()
            return wx.MessageBox("Cannot post empty status.", "Error", wx.OK | wx.ICON_ERROR)
        scheduled_at = None
        if self.schedule_toggle.IsChecked():
            date_str = self.schedule_date_input.GetValue().strip()
            time_str = self.schedule_time_input.GetValue().strip()
            if not date_str or not time_str:
                return wx.MessageBox("Please enter both date (YYYY-MM-DD) and time (HH:MM) for scheduling.", "Schedule Error", wx.OK | wx.ICON_ERROR)
            try:
                from datetime import datetime, timezone
                scheduled_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                return wx.MessageBox("Invalid date/time format. Use YYYY-MM-DD and HH:MM.", "Schedule Error", wx.OK | wx.ICON_ERROR)
        try:
            media_ids = []
            for mf in self.media_files:
                media = self.mastodon.media_post(mf["path"], description=mf.get("alt_text") or None)
                media_ids.append(media)
            self.mastodon.status_post(status_text, spoiler_text=spoiler, visibility=visibility, poll=poll_data, media_ids=media_ids if media_ids else None, language=language, scheduled_at=scheduled_at)
            if scheduled_at:
                wx.MessageBox(f"Post scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M')} UTC.", "Scheduled")
            elif tootsnd: tootsnd.play()
            self.toot_input.SetValue(""); self.cw_input.SetValue(""); self.cw_toggle.SetValue(False); self.on_toggle_cw(None)
            self.media_files.clear(); self.media_list.Clear(); self.alt_text_input.SetValue("")
            self.media_toggle.SetValue(False); self.on_toggle_media(None)
            self.schedule_toggle.SetValue(False); self.on_toggle_schedule(None)
            self.schedule_date_input.SetValue(""); self.schedule_time_input.SetValue("")
            if poll_data:
                self.poll_toggle.SetValue(False); [opt.SetValue("") for opt in self.poll_option_inputs]; self.poll_duration_choice.SetSelection(5); self.poll_multiple_choice.SetValue(False); self.on_toggle_poll(None)
        except Exception as e:
            if errorsnd: errorsnd.play()
            wx.MessageBox(f"Error: {e}", "Post Error")

    def on_key_press(self, event):
        kc = event.GetKeyCode()
        ctrl = event.ControlDown()
        shift = event.ShiftDown()
        alt = event.AltDown()
        focus = self.FindFocus()
        in_text = isinstance(focus, wx.TextCtrl)

        # Global: Ctrl+N focuses compose box
        if ctrl and not shift and not alt and kc == ord('N'):
            self.toot_input.SetFocus()
            return

        # Post submission: Enter with modifier in text input
        if kc == wx.WXK_RETURN and in_text and event.HasAnyModifiers():
            self.on_post(event)
            return

        # All other shortcuts only active outside text inputs
        if in_text:
            event.Skip()
            return

        # Ctrl only (no shift, no alt)
        if ctrl and not shift and not alt:
            ctrl_map = {
                ord('R'): self.on_reply,
                ord('Q'): self.on_quote,
                ord('F'): self.on_favourite,
                ord('E'): self.on_edit_post,
                ord('G'): self.on_view_thread,
                ord('L'): self.on_follow_user,
                ord('B'): self.on_block_user,
                ord('D'): self.on_dm_user,
                ord('U'): self.on_open_user_timeline,
                ord('C'): self.on_copy_post,
                ord('T'): self.on_explore,
                ord('I'): self.on_instance_info,
            }
            if kc in ctrl_map:
                ctrl_map[kc](event)
                return
            if kc == ord('/') or kc == wx.WXK_NUMPAD_DIVIDE:
                self.on_search(event)
                return
            if kc == ord('['):
                self.on_view_followers(event)
                return
            if kc == ord(']'):
                self.on_view_following(event)
                return

        # Ctrl+Shift (no alt)
        if ctrl and shift and not alt:
            ctrl_shift_map = {
                ord('R'): self.on_boost,
                ord('F'): self.on_favourite,
                ord('L'): self.on_follow_user,
                ord('B'): self.on_block_user,
                ord('U'): self.on_view_profile,
            }
            if kc in ctrl_shift_map:
                ctrl_shift_map[kc](event)
                return

        # Ctrl+Alt (no shift)
        if ctrl and alt and not shift:
            if kc == ord('K'):
                self.on_view_favourites_timeline(event)
                return
            if kc == ord('B'):
                self.on_view_bookmarks_timeline(event)
                return

        # Alt only (no ctrl)
        if alt and not ctrl:
            if not shift:
                alt_map = {
                    ord('B'): self.on_bookmark,
                    ord('V'): lambda e: self.show_post_details(),
                    ord('W'): self.on_open_post_url,
                }
                if kc in alt_map:
                    alt_map[kc](event)
                    return
                if kc == ord('P'):
                    self.timeline_tree.SelectItem(self.timeline_nodes.get("federated", self.timeline_tree.GetSelection()))
                    return
                if kc == ord('L'):
                    self.timeline_tree.SelectItem(self.timeline_nodes.get("local", self.timeline_tree.GetSelection()))
                    return
                if kc == wx.WXK_PAGEUP:
                    self.load_older_posts()
                    return
            if shift:
                if kc == ord('B'):
                    self.on_bookmark(event)
                    return

        # Delete key
        if kc == wx.WXK_DELETE and focus == self.posts_list:
            self.delete_selected_post()
            return

        event.Skip()

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
                elif key.startswith("hashtag:"): data = self.mastodon.timeline_hashtag(key.split(":", 1)[1], max_id=last_id, limit=40)
                elif key.startswith("list:"): data = self.mastodon.timeline_list(key.split(":", 1)[1], max_id=last_id, limit=40)
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
        is_own = self.me and status.get("account", {}).get("id") == self.me.get("id")
        if is_own:
            if usersnd: usersnd.play()
            # Add to sent timeline
            self.timelines_data.setdefault("sent", []).insert(0, status)
            if self.timeline_tree.GetSelection() == self.timeline_nodes.get("sent"):
                row, avatar_url = self.row_from_status(status)
                if row:
                    self.posts_list.Insert(row, 0, avatar_url)
                    self.queue_avatar_download(avatar_url)
        else:
            if status.get("visibility") == "direct":
                dmsnd and dmsnd.play()
            else:
                newtootsnd and newtootsnd.play()

        # Add to home timeline (stream_user delivers home timeline posts)
        self.timelines_data["home"].insert(0, status)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["home"]:
            row, avatar_url = self.row_from_status(status)
            if row:
                self.posts_list.Insert(row, 0, avatar_url)
                self.queue_avatar_download(avatar_url)

        # Add to any open user timelines for this account
        account_id = status.get('account', {}).get('id')
        if account_id:
            user_key = f"user:{account_id}"
            if user_key in self.timeline_nodes:
                self.timelines_data.setdefault(user_key, []).insert(0, status)
                if self.timeline_tree.GetSelection() == self.timeline_nodes[user_key]:
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

        # Route mentions to the mentions timeline as statuses
        if ntype == "mention" and notification.get("status"):
            mention_status = notification["status"]
            self.timelines_data.setdefault("mentions", []).insert(0, mention_status)
            if self.timeline_tree.GetSelection() == self.timeline_nodes.get("mentions"):
                row, avatar_url = self.row_from_status(mention_status)
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
            elif timeline.startswith("hashtag:"): data = self.mastodon.timeline_hashtag(timeline.split(":", 1)[1], limit=40)
            elif timeline.startswith("list:"): data = self.mastodon.timeline_list(timeline.split(":", 1)[1], limit=40)
            else: data = []
            
            old_count = len(self.timelines_data.get(timeline, []))
            self.timelines_data[timeline] = data
            
            # Play search_updated sound when a search timeline refreshes with new results
            if timeline.startswith("search:") and len(data) > old_count:
                if search_updatedsnd: wx.CallAfter(lambda: search_updatedsnd.play())

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

        # Detect quote posts
        quote_obj = source_obj.get('quote')
        if quote_obj:
            quoted_status = quote_obj.get('quoted_status') if isinstance(quote_obj, dict) else getattr(quote_obj, 'quoted_status', None)
            if quoted_status:
                quoted_author = quoted_status.get('account', {})
                quoted_display = quoted_author.get('display_name') or quoted_author.get('username') or ''
                quoted_handle = quoted_author.get('acct', '')
                quoted_content = strip_html((quoted_status.get('content', '') or '').replace('<br />', '\n').replace('<br>', '\n').replace('</p>', '\n\n')).strip()
                # Strip server-prepended RE: <url> or QT: <url> from content
                quoted_url = quoted_status.get('url') or quoted_status.get('uri') or ''
                if quoted_url:
                    content_cell = re.sub(r'^(?:RE|QT):\s*' + re.escape(quoted_url) + r'\s*', '', content_cell).strip()
                    content_cell = content_cell.rstrip().removesuffix(quoted_url).rstrip()
                content_cell = f"quoting {quoted_display} (@{quoted_handle}): \"{quoted_content}\". {author_cell} added \"{content_cell}\""

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
        try:
            dlg = PostDetailsDialog(self, self.mastodon, status, self.me, votesnd=votesnd)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            wx.MessageBox(f"Error opening post details: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def get_app_name(self, status_obj):
        if not status_obj: return 'Unknown'
        app = status_obj.get('application') or {}
        return app.get('name', 'Unknown') if isinstance(app, dict) else str(app or 'Unknown')

    def format_time(self, created_at):
        if not created_at: return ''
        return singularize_time(get_time_ago(created_at))