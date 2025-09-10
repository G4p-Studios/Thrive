import wx
import threading
from datetime import datetime
from mastodon import StreamListener
from utils import strip_html, get_time_ago
from post_dialog import PostDetailsDialog
from settings_dialog import SettingsDialog
from sound_lib import stream
from sound_lib.main import BassError
from easysettings import EasySettings

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

# --- Custom Stream Listener ---
class CustomStreamListener(StreamListener):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def on_update(self, status):
        wx.CallAfter(self.frame.add_new_post, status)
        # Sounds: DM, mention, or generic new toot
        visibility = status.get("visibility")
        mentions = status.get("mentions", [])
        is_dm = visibility == "direct"
        is_mention = any(u.get('id') == getattr(self.frame, 'me', {}).get('id') for u in mentions)
        if is_dm and dmsnd:
            dmsnd.play()
        elif is_mention and mentionsnd:
            mentionsnd.play()
        elif newtootsnd:
            newtootsnd.play()

    def on_delete(self, status_id):
        wx.CallAfter(self.frame.handle_post_deletion, status_id)

    def on_notification(self, notification):
        wx.CallAfter(self.frame.add_notification, notification)

    def on_status_update(self, status):
        # Fired when a status is edited
        wx.CallAfter(self.frame.handle_status_update, status)


class ThriveFrame(wx.Frame):
    def __init__(self, *args, **kwargs):
        mastodon = kwargs.pop("mastodon", None)  # don't pass custom kw to wx.Frame
        super().__init__(*args, **kwargs, size=(800, 600))

        self.mastodon = mastodon
        self.me = self.mastodon.me() if self.mastodon else None
        self.status_map = []  # parallel to listbox entries
        self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
        self.privacy_values = ["public", "unlisted", "private", "direct"]

        # --- UI ---
        self.panel = wx.Panel(self)

        menubar = wx.MenuBar()
        settings_menu = wx.Menu()
        settings_item = settings_menu.Append(wx.ID_ANY, "&Settings...\tAlt-S", "Open Settings")
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

        self.timeline_tree = wx.TreeCtrl(self.panel, style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT)
        self.root = self.timeline_tree.AddRoot("Timelines")
        self.timeline_nodes = {
            "home": self.timeline_tree.AppendItem(self.root, "Home"),
            "sent": self.timeline_tree.AppendItem(self.root, "Sent"),
            "notifications": self.timeline_tree.AppendItem(self.root, "Notifications"),
            "mentions": self.timeline_tree.AppendItem(self.root, "Mentions"),
        }
        self.timeline_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_timeline_selected)

        self.posts_list = wx.ListBox(self.panel, style=wx.LB_SINGLE)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(self.timeline_tree, 0, wx.EXPAND | wx.ALL, 5)
        hbox.Add(self.posts_list, 1, wx.EXPAND | wx.ALL, 5)

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
        vbox.Add(hbox, 1, wx.EXPAND, 0)

        self.panel.SetSizer(vbox)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)

        # Default view + start stream
        self.timeline_tree.SelectItem(self.timeline_nodes["home"])
        self.start_streaming()

    # --- Settings + Sounds ---
    def conf(self):
        """Return EasySettings for thrive.ini"""
        return EasySettings("thrive.ini")

    def load_sounds(self):
        """Reload all sound files based on current settings."""
        global tootsnd, replysnd, boostsnd, favsnd, unfavsnd, newtootsnd, dmsnd, mentionsnd
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
        return True

    def open_settings(self, event):
        dlg = SettingsDialog(self, on_save_callback=self.load_sounds)
        dlg.ShowModal()
        dlg.Destroy()

    # --- Streaming ---
    def start_streaming(self):
        if not self.mastodon:
            return
        listener = CustomStreamListener(self)
        threading.Thread(target=self.mastodon.stream_user, args=(listener,), daemon=True).start()

    def add_new_post(self, status):
        # Normalize timestamp
        if isinstance(status.get("created_at"), str):
            status["created_at"] = datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
        display = self.format_status_for_display(status)
        if display:
            self.status_map.insert(0, status)
            self.posts_list.Insert(display, 0)

    def handle_post_deletion(self, status_id):
        for i, s in enumerate(self.status_map):
            if s.get("id") == status_id:
                self.status_map.pop(i)
                self.posts_list.Delete(i)
                break

    def handle_status_update(self, status):
        for i, s in enumerate(self.status_map):
            if s.get("id") == status.get("id"):
                # Normalize timestamp
                if isinstance(status.get("created_at"), str):
                    status["created_at"] = datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
                self.status_map[i] = status
                display = self.format_status_for_display(status)
                if display:
                    self.posts_list.SetString(i, display)
                break

    def add_notification(self, notification):
        display = self.format_notification_for_display(notification)
        if display and self.timeline_tree.GetSelection() == self.timeline_nodes["notifications"]:
            self.status_map.insert(0, notification)
            self.posts_list.Insert(display, 0)

    # --- Timeline loading ---
    def on_timeline_selected(self, event):
        item = event.GetItem()
        for key, node in self.timeline_nodes.items():
            if item == node:
                self.load_timeline(key)
                break

    def load_timeline(self, timeline):
        self.status_map.clear()
        self.posts_list.Clear()

        def fetch():
            try:
                if timeline == "home":
                    statuses = self.mastodon.timeline_home(limit=40)
                elif timeline == "sent":
                    statuses = self.mastodon.account_statuses(self.me["id"], limit=40)
                    statuses = [s for s in statuses if not s.get("reblog")]  # only originals
                elif timeline == "notifications":
                    notifications = self.mastodon.notifications(limit=40)
                    statuses = notifications  # store raw notifications
                elif timeline == "mentions":
                    notifs = self.mastodon.notifications(types=["mention"], limit=40)
                    statuses = [n["status"] for n in notifs if n.get("status")]
                else:
                    statuses = []

                # Normalize / sort
                for s in statuses:
                    if isinstance(s.get("created_at"), str):
                        s["created_at"] = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
                statuses.sort(key=lambda s: s.get("created_at", datetime.min), reverse=True)

                for s in statuses:
                    if timeline == "notifications":
                        display = self.format_notification_for_display(s)
                    else:
                        display = self.format_status_for_display(s)
                    wx.CallAfter(self.status_map.append, s)
                    wx.CallAfter(self.posts_list.Append, display)
            except Exception as e:
                wx.CallAfter(self.posts_list.Append, f"Error loading {timeline}: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    # --- Formatting helpers ---
    def format_notification_for_display(self, notification):
        ntype = notification.get("type")
        account = notification.get("account", {})
        user = account.get("display_name") or account.get("username", "Unknown")

        if ntype == "favourite":
            status = notification.get("status")
            if status:
                if favsnd:
                    favsnd.play()
                return f"{user} favourited your post: {strip_html(status['content']).strip()}"
            return f"{user} favourited one of your posts"
        elif ntype == "reblog":
            status = notification.get("status")
            if status:
                if boostsnd:
                    boostsnd.play()
                return f"{user} boosted your post: {strip_html(status['content']).strip()}"
            return f"{user} boosted one of your posts"
        elif ntype == "mention":
            status = notification.get("status")
            if status:
                if mentionsnd:
                    mentionsnd.play()
                return f"{user} mentioned you: {strip_html(status['content']).strip()}"
            return f"{user} mentioned you"
        elif ntype == "poll":
            status = notification.get("status")
            if status and status.get("poll") and status["poll"].get("expired"):
                if newtootsnd:
                    newtootsnd.play()
                return f"Poll ended in {user}'s post: {strip_html(status['content']).strip()}"
            return f"Poll update from {user}"
        elif ntype == "update":
            status = notification.get("status")
            if status:
                if newtootsnd:
                    newtootsnd.play()
                return f"{user}'s post you interacted with was edited: {strip_html(status['content']).strip()}"
            return f"{user} edited a post"
        else:
            return f"{user}: {ntype} (unhandled)"

    def format_status_for_display(self, status):
        # Handle boosts vs originals, spoiler/CW, and app source
        if status.get("reblog"):
            boost = status["reblog"]
            user = status["account"].get("display_name") or status["account"].get("username")
            original_user = boost["account"].get("display_name") or boost["account"].get("username")
            handle = boost["account"].get("acct", "")
            content = strip_html(boost.get("content", "")).strip()
            boost_app = boost.get("application")
            boost_source = boost_app.get("name") if isinstance(boost_app, dict) else "Unknown"
            if boost.get("spoiler_text"):
                display = f"{user}: Content warning: {boost['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: Boosting {original_user} ({handle}): {content}"
            display += f" — {get_time_ago(boost.get('created_at'))}, {boost_source}"
            return display
        else:
            user = status["account"].get("display_name") or status["account"].get("username")
            content = strip_html(status.get("content", "")).strip()
            app_info = status.get("application")
            source = app_info.get("name") if isinstance(app_info, dict) else "Unknown"
            if status.get("spoiler_text"):
                display = f"{user}: Content warning: {status['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: {content}"
            display += f" — {get_time_ago(status.get('created_at'))}, {source}"
            return display

    # --- Misc UI actions ---
    def on_toggle_cw(self, event):
        show = self.cw_toggle.IsChecked()
        self.cw_input.Show(show)
        self.cw_label.Show(show)
        self.panel.Layout()

    def on_post(self, event):
        status_text = self.toot_input.GetValue().strip()
        spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
        visibility = self.privacy_values[self.privacy_choice.GetSelection()]
        if not status_text:
            wx.MessageBox("Cannot post empty status.", "Error")
            return
        try:
            self.mastodon.status_post(status_text, spoiler_text=spoiler, visibility=visibility)
            if tootsnd:
                tootsnd.play()
            self.toot_input.SetValue("")
            self.cw_input.SetValue("")
            self.cw_toggle.SetValue(False)
            self.on_toggle_cw(None)
        except Exception as e:
            wx.MessageBox(f"Error: {e}", "Post Error")

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
        if status.get('account', {}).get('id') != (self.me or {}).get('id'):
            wx.MessageBox("Stop trying to take down other people's posts. I know you probably want to, but it just won't work.", "Error", wx.OK | wx.ICON_ERROR)
            return
        if status.get("reblog"):
            confirm = wx.MessageBox("Are you sure you want to unboost this post?", "Confirm Unboost", wx.YES_NO | wx.ICON_QUESTION)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_unreblog(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error unboosting: {e}", "Error", wx.OK | wx.ICON_ERROR)
        else:
            confirm = wx.MessageBox("Are you sure you want to take down this post? It will be removed from Mastodon. This action cannot be undone.", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_delete(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error deleting post: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def show_post_details(self):
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return

        # Determine which timeline we're in
        current_item = self.timeline_tree.GetSelection()
        in_notifications = current_item == self.timeline_nodes.get("notifications")

        raw = self.status_map[selection]
        if in_notifications:
            # Notifications can wrap a status in notification['status']
            status = raw.get("status") if isinstance(raw, dict) else None
            if not status:
                wx.MessageBox("This notification has no associated post to open.", "No Post", wx.OK | wx.ICON_INFORMATION)
                return
        else:
            # Home / Sent / Mentions contain statuses directly
            status = raw

        dlg = PostDetailsDialog(self, self.mastodon, status, self.me)
        dlg.ShowModal()
        dlg.Destroy()

    def initial_load_posts(self):
        def fetch_and_update():
            try:
                statuses = self.mastodon.timeline_home(limit=40)
                for s in statuses:
                    if isinstance(s.get("created_at"), str):
                        s["created_at"] = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
                statuses.sort(key=lambda s: s.get("created_at"), reverse=True)
                for status in statuses:
                    display = self.format_status_for_display(status)
                    wx.CallAfter(self.status_map.append, status)
                    wx.CallAfter(self.posts_list.Append, display)
            except Exception as e:
                wx.CallAfter(self.posts_list.Append, f"Error loading posts: {e}")
        threading.Thread(target=fetch_and_update, daemon=True).start()
