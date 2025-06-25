import wx
import threading
import os
from datetime import datetime
from mastodon import StreamListener # Import the StreamListener
from utils import strip_html, get_time_ago
from post_dialog import PostDetailsDialog
from settings_dialog import SettingsDialog
from sound_lib import stream
from sound_lib import output as o
from sound_lib.main import BassError
from easysettings import EasySettings

try:
    conf = EasySettings("thrive.ini")
    soundpack = conf.get("soundpack", "default")
    folder = "Mastodon-" + soundpack
    tootsnd = stream.FileStream(file="sounds/" + folder + "/send_toot.wav")
except BassError:
    tootsnd = None
try:
    replysnd = stream.FileStream(file="sounds/" + folder + "/send_reply.wav")
except BassError:
    replysnd = None
try:
    boostsnd = stream.FileStream(file="sounds/" + folder + "/send_boost.wav")
except BassError:
    boostsnd = None
try:
    favsnd = stream.FileStream(file="sounds/" + folder + "/favorite.wav")
except BassError:
    favsnd = None
try:
    unfavsnd = stream.FileStream(file="sounds/" + folder + "/unfavorite.wav")
except BassError:
    unfavsnd = None
try:
    newtootsnd = stream.FileStream(file="sounds/" + folder + "/new_toot.wav")
except BassError:
    newtootsnd = None
try:
    dmsnd = stream.FileStream(file="sounds/" + folder + "/new_dm.wav")
except BassError:
    dmsnd = None
try:
    mentionsnd = stream.FileStream(file="sounds/" + folder + "/new_mention.wav")
except BassError:
    mentionsnd = None

# --- NEW: Custom Stream Listener ---
class CustomStreamListener(StreamListener):
    def __init__(self, frame):
        super().__init__()
        # Store a reference to the main frame to call its methods
        self.frame = frame

    def on_update(self, status):
        """A new status has appeared!"""
        # This runs in a background thread, so we use CallAfter for the UI update
        wx.CallAfter(self.frame.add_new_post, status)
        visibility = status.get("visibility")
        mentions = status.get("mentions", [])
        is_dm = visibility == "direct"
        is_mention = any(user['id'] == self.frame.me['id'] for user in mentions)

        if is_dm and dmsnd:
            dmsnd.play()
        elif is_mention and mentionsnd:
            mentionsnd.play()
        elif newtootsnd:
            newtootsnd.play()

    def on_delete(self, status_id):
        """A status has been deleted."""
        wx.CallAfter(self.frame.handle_post_deletion, status_id)

class ThriveFrame(wx.Frame):
    def __init__(self, *args, mastodon=None, **kwargs):
        super().__init__(*args, **kwargs, size=(800, 600))
        self.mastodon = mastodon
        self.me = self.mastodon.me()
        self.status_map = []
        self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
        self.privacy_values = ["public", "unlisted", "private", "direct"]

        self.panel = wx.Panel(self)
        
        menubar = wx.MenuBar()
        settings_menu = wx.Menu()
        settings_item = settings_menu.Append(wx.ID_ANY, "&Settings...	Alt-S", "Open Settings")
        self.Bind(wx.EVT_MENU, self.open_settings, settings_item)
        menubar.Append(settings_menu, "&Settings")
        self.SetMenuBar(menubar)

        vbox = wx.BoxSizer(wx.VERTICAL)

        self.toot_label = wx.StaticText(self.panel, label="&Post:")
        self.toot_input = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(780, 100))

        self.cw_label = wx.StaticText(self.panel, label="Content w&arning title:")
        self.cw_input = wx.TextCtrl(self.panel, size=(780, 30))
        self.cw_toggle = wx.CheckBox(self.panel, label="Add Content &Warning")
        self.cw_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_cw)
        self.cw_input.Hide()
        self.cw_label.Hide()

        self.privacy_label = wx.StaticText(self.panel, label="P&rivacy:")
        self.privacy_choice = wx.Choice(self.panel, choices=self.privacy_options)
        self.privacy_choice.SetSelection(0)

        self.post_button = wx.Button(self.panel, label="Po&st")
        self.post_button.Bind(wx.EVT_BUTTON, self.on_post)

        self.exit_button = wx.Button(self.panel, label="E&xit")
        self.exit_button.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        self.posts_label = wx.StaticText(self.panel, label="Posts &List:")
        self.posts_list = wx.ListBox(self.panel, style=wx.LB_SINGLE, size=(780, 200))

        vbox.Add(self.toot_label, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.toot_input, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.cw_label, 0, wx.LEFT | wx.RIGHT, 5)
        vbox.Add(self.cw_input, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.cw_toggle, 0, wx.ALL, 5)
        vbox.Add(self.privacy_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        vbox.Add(self.privacy_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 5)
        vbox.Add(self.post_button, 0, wx.ALL | wx.CENTER, 5)
        vbox.Add(self.exit_button, 0, wx.ALL | wx.CENTER, 5)
        vbox.Add(self.posts_label, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(self.posts_list, 1, wx.ALL | wx.EXPAND, 5)

        self.panel.SetSizer(vbox)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)

        # --- MODIFIED: Start initial load and then streaming ---
        # The refresh timer is no longer needed
        # self.refresh_timer = wx.Timer(self)
        # self.Bind(wx.EVT_TIMER, self.update_posts)
        # self.refresh_timer.Start(60000)
        self.initial_load_posts()
        self.start_streaming()

    @property
    def conf(self):
        return EasySettings("thrive.ini")

    def load_sounds(self):
        global tootsnd
        try:
            soundpack = self.conf.get("soundpack", "default")
            folder = "mastodon-" + soundpack
            tootsnd = stream.FileStream(file="sounds/" + folder + "/send_toot.wav")
        except BassError:
            tootsnd = None

    def open_settings(self, event):
        dlg = SettingsDialog(self, on_save_callback=self.load_sounds)
        dlg.ShowModal()
        dlg.Destroy()

    def on_key_press(self, event):
        mods = event.HasAnyModifiers()
        if event.GetKeyCode() == wx.WXK_RETURN and self.FindFocus() == self.posts_list:
            self.show_post_details()
        elif event.GetKeyCode() == wx.WXK_DELETE and self.FindFocus() == self.posts_list:
            self.delete_selected_post()
        elif event.GetKeyCode() == wx.WXK_RETURN and self.FindFocus() == self.toot_input and mods:
            self.on_post(event)
        else:
            event.Skip()

    def delete_selected_post(self):
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return

        status = self.status_map[selection]

        if status['account']['id'] != self.me['id']:
            wx.MessageBox("Stop trying to take down other people's posts. I know you probably want to, but it just won't work.", "Error", wx.OK | wx.ICON_ERROR)
            return

        is_reblog = status.get("reblog") is not None

        if is_reblog:
            confirm = wx.MessageBox("Are you sure you want to unboost this post?", "Confirm Unboost", wx.YES_NO | wx.ICON_QUESTION)
            if confirm == wx.YES:
                try:
                    # After unboosting, the stream will send a 'delete' event for the boost,
                    # so we don't need to manually update the list here.
                    self.mastodon.status_unreblog(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error unboosting: {e}", "Error", wx.OK | wx.ICON_ERROR)
        else: # Original post
            confirm = wx.MessageBox("Are you sure you want to take down this post? It will be removed from Mastodon. This action cannot be undone.", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING)
            if confirm == wx.YES:
                try:
                    # The stream will send a 'delete' event, removing it from the list.
                    self.mastodon.status_delete(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error deleting post: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def on_toggle_cw(self, event):
        show = self.cw_toggle.IsChecked()
        self.cw_input.Show(show)
        self.cw_label.Show(show)
        self.panel.Layout()

    def on_post(self, event):
        status = self.toot_input.GetValue().strip()
        spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
        selected_privacy_index = self.privacy_choice.GetSelection()
        visibility = self.privacy_values[selected_privacy_index]
        if not status:
            wx.MessageBox("Cannot post empty status.", "Error")
            return
        try:
            # The stream will pick up our own new post, so we don't need to manually refresh.
            self.mastodon.status_post(status, spoiler_text=spoiler, visibility=visibility)
            if tootsnd:
                tootsnd.play()
            self.toot_input.SetValue("")
            self.cw_input.SetValue("")
            self.cw_toggle.SetValue(False)
            self.on_toggle_cw(None)
            # self.update_posts() # No longer needed
        except Exception as e:
            wx.MessageBox(f"Error: {e}", "Post Error")

    # --- REFACTORED: This method is now only for the initial load ---
    def initial_load_posts(self):
        def fetch_and_update():
            # This logic remains largely the same, but it populates the list once.
            try:
                statuses = self.mastodon.timeline_home(limit=40)
                for s in statuses:
                    if isinstance(s["created_at"], str):
                        s["created_at"] = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
                statuses.sort(key=lambda s: s["created_at"], reverse=True)

                # Use CallAfter to ensure thread-safety, even on initial load
                for status in statuses:
                    display = self.format_status_for_display(status)
                    wx.CallAfter(self.status_map.append, status)
                    wx.CallAfter(self.posts_list.Append, display)

            except Exception as e:
                wx.CallAfter(self.posts_list.Append, f"Error loading posts: {e}")

        threading.Thread(target=fetch_and_update, daemon=True).start()

    # --- NEW: Method to start the streaming thread ---
    def start_streaming(self):
        listener = CustomStreamListener(self)
        # run stream_user in a daemon thread so it doesn't block app exit
        threading.Thread(target=self.mastodon.stream_user, args=(listener,), daemon=True).start()

    # --- NEW: Callback for the stream listener to add a new post ---
    def add_new_post(self, status):
        """Inserts a new post at the top of the list."""
        if isinstance(status["created_at"], str):
            status["created_at"] = datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
        
        display = self.format_status_for_display(status)
        self.status_map.insert(0, status)
        self.posts_list.Insert(display, 0)

    # --- NEW: Callback for the stream listener to handle a deletion ---
    def handle_post_deletion(self, status_id):
        """Finds and removes a deleted post from the list."""
        # We need to find the index of the status to remove it
        index_to_delete = -1
        for i, status in enumerate(self.status_map):
            # A 'delete' event can be for an original post or a boost.
            # The ID we get is for the item that was deleted from the timeline.
            if status['id'] == status_id:
                index_to_delete = i
                break
        
        if index_to_delete != -1:
            del self.status_map[index_to_delete]
            self.posts_list.Delete(index_to_delete)

    # --- NEW: Helper method to format a status consistently ---
    def format_status_for_display(self, status):
        """Generates the display string for a given status dictionary."""
        display = ""
        if status.get("reblog"):
            boost = status["reblog"]
            user = status["account"]["display_name"] or status["account"]["username"]
            original_user = boost["account"]["display_name"] or boost["account"]["username"]
            handle = boost["account"]["acct"]
            content = strip_html(boost["content"]).strip()
            boost_app = boost.get("application")
            boost_source = boost_app["name"] if boost_app and "name" in boost_app else "Unknown"
            if boost["spoiler_text"]:
                display = f"{user}: Content warning: {boost['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: Boosting {original_user} ({handle}): {content}"
            display += f" — {get_time_ago(boost['created_at'])}, {boost_source}"
        else:
            user = status["account"]["display_name"] or status["account"]["username"]
            content = strip_html(status["content"]).strip()
            app_info = status.get("application")
            source = app_info["name"] if app_info and "name" in app_info else "Unknown"
            if status["spoiler_text"]:
                display = f"{user}: Content warning: {status['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: {content}"
            display += f" — {get_time_ago(status['created_at'])}, {source}"
        return display

    def show_post_details(self, event=None):
        selection = self.posts_list.GetSelection()
        if 0 <= selection < len(self.status_map):
            status = self.status_map[selection]
            dlg = PostDetailsDialog(self, self.mastodon, status, self.me)
            dlg.ShowModal()
            dlg.Destroy()