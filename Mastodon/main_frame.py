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
import re

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
		# Columns: Author, Content, Time, Client (reordered)
		self.InsertColumn(0, "Author", width=160)
		self.InsertColumn(1, "Content", width=360)
		self.InsertColumn(2, "Time", width=120)
		self.InsertColumn(3, "Client", width=120)

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
        super().__init__(*args, **kwargs, size=(800, 600))

        self.mastodon = mastodon
        self.me = self.mastodon.me() if self.mastodon else None
        self.timelines_data = {"home": [], "sent": [], "notifications": [], "mentions": []}
        self.privacy_options = ["Public", "Unlisted", "Followers-only", "Direct"]
        self.privacy_values = ["public", "unlisted", "private", "direct"]

        # --- UI setup ---
        self.panel = wx.Panel(self)
        menubar = wx.MenuBar()
        settings_menu = wx.Menu()
        settings_item = settings_menu.Append(wx.ID_ANY, "&Settings...\tAlt-S", "Open Settings")
        self.Bind(wx.EVT_MENU, self.open_settings, settings_item)
        menubar.Append(settings_menu, "&Settings")
        self.SetMenuBar(menubar)

        # Add Refresh menu
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
        # Use the SysListViewAdapter (wx.ListCtrl) so screen readers see a table
        self.posts_list = SysListViewAdapter(self.panel)

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
        self.timeline_tree.SelectItem(self.timeline_nodes["home"])
        # Load initial Home timeline so posts are visible right away
        self.load_timeline("home")

        # Preload all other timelines in background so switching is instant
        for key in ["sent", "notifications", "mentions"]:
            threading.Thread(target=lambda k=key: self.load_timeline(k), daemon=True).start()
        self.start_streaming()

    # --- Settings ---
    def open_settings(self, event):
        dlg = SettingsDialog(self, on_save_callback=self.load_sounds)
        dlg.ShowModal()
        dlg.Destroy()

    # --- UI Handlers ---
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
        status = self.timelines_data["home"][selection] if selection < len(self.timelines_data["home"]) else None
        if not status or status.get('account', {}).get('id') != (self.me or {}).get('id'):
            wx.MessageBox("Stop trying to take down other people's posts.", "Error", wx.OK | wx.ICON_ERROR)
            return
        if status.get("reblog"):
            confirm = wx.MessageBox("Are you sure you want to unboost this post?", "Confirm Unboost", wx.YES_NO | wx.ICON_QUESTION)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_unreblog(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error unboosting: {e}", "Error", wx.OK | wx.ICON_ERROR)
        else:
            confirm = wx.MessageBox("Are you sure you want to take down this post? It will be removed from Mastodon.", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING)
            if confirm == wx.YES:
                try:
                    self.mastodon.status_delete(status['id'])
                except Exception as e:
                    wx.MessageBox(f"Error deleting: {e}", "Error", wx.OK | wx.ICON_ERROR)

    # --- Streaming ---
    def start_streaming(self):
        if not self.mastodon:
            return
        listener = CustomStreamListener(self)
        threading.Thread(target=self.mastodon.stream_user, args=(listener,), daemon=True).start()

    def add_new_post(self, status):
        display = self.format_status_for_display(status)
        if not display:
            return
        self.timelines_data["home"].insert(0, status)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["home"]:
            row = self.row_from_status(status)
            self.posts_list.Insert(row, 0)

    def add_notification(self, notification):
        display = self.format_notification_for_display(notification)
        if not display:
            return
        self.timelines_data["notifications"].insert(0, notification)
        if self.timeline_tree.GetSelection() == self.timeline_nodes["notifications"]:
            row = self.row_from_notification(notification)
            self.posts_list.Insert(row, 0)

    def handle_status_update(self, status):
        # Update in home, sent, mentions timelines
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

    # --- Timeline loading ---
    def load_timeline(self, timeline):
        self.posts_list.Clear()
        if timeline == "home":
            statuses = self.mastodon.timeline_home(limit=40)
            self.timelines_data["home"] = statuses
            for s in statuses:
                row = self.row_from_status(s)
                if row:
                    self.posts_list.Append(row)
        elif timeline == "sent":
            statuses = self.mastodon.account_statuses(self.me["id"], limit=40)
            statuses = [s for s in statuses if not s.get("reblog")]
            self.timelines_data["sent"] = statuses
            for s in statuses:
                row = self.row_from_status(s)
                if row:
                    self.posts_list.Append(row)
        elif timeline == "notifications":
            notifs = self.mastodon.notifications(limit=40)
            self.timelines_data["notifications"] = notifs
            for n in notifs:
                row = self.row_from_notification(n)
                if row:
                    self.posts_list.Append(row)
        elif timeline == "mentions":
            notifs = self.mastodon.notifications(types=["mention"], limit=40)
            statuses = [n["status"] for n in notifs if n.get("status")]
            self.timelines_data["mentions"] = statuses
            for s in statuses:
                row = self.row_from_status(s)
                if row:
                    self.posts_list.Append(row)

    def on_timeline_selected(self, event):
        for key, node in self.timeline_nodes.items():
            if event.GetItem() == node:
                # Just redraw from buffer (already preloaded & updated by stream)
                self.posts_list.Clear()
                for s in self.timelines_data[key]:
                    if key == "notifications":
                        row = self.row_from_notification(s)
                    else:
                        row = self.row_from_status(s)
                    if row:
                        self.posts_list.Append(row)
                break

    def on_refresh(self, event):
        current_item = self.timeline_tree.GetSelection()
        for key, node in self.timeline_nodes.items():
            if current_item == node:
                self.load_timeline(key)
                break

    def on_refresh(self, event):
        current_item = self.timeline_tree.GetSelection()
        for key, node in self.timeline_nodes.items():
            if current_item == node:
                self.load_timeline(key)
                break

    def on_toggle_cw(self, event):
        show = self.cw_toggle.IsChecked()
        self.cw_input.Show(show)
        self.cw_label.Show(show)
        self.panel.Layout()

    def on_post(self, event):
        if not self.mastodon:
            wx.MessageBox("Not connected to a server.", "Error")
            return
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
        if not self.mastodon:
            return
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        current_item = self.timeline_tree.GetSelection()
        key = next((k for k, v in self.timeline_nodes.items() if v == current_item), None)
        if not key or key == "notifications":
            return  # can't delete from notifications list
        status = self.timelines_data[key][selection]
        my_id = (self.me or {}).get('id')
        if status.get('account', {}).get('id') != my_id:
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

    def conf(self):
        return EasySettings("thrive.ini")

    def load_sounds(self):
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
        if status.get("reblog"):
            boost = status["reblog"]
            user = status["account"].get("display_name") or status["account"].get("username")
            original_user = boost["account"].get("display_name") or boost["account"].get("username")
            handle = boost["account"].get("acct", "")
            content = strip_html(boost.get("content", "")).strip()
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
            content = strip_html(status.get("content", "")).strip()
            app = status.get("application") or {}
            source = app.get("name") if isinstance(app, dict) else "Unknown"
            if status.get("spoiler_text"):
                display = f"{user}: Content warning: {status['spoiler_text']}. Press enter on this post to see the text."
            else:
                display = f"{user}: {content}"
            display += f" — {singularize_time(get_time_ago(status.get('created_at')))}, {source}"
            return display

    # Helpers to construct table rows: [time, client, author, content]
    def row_from_status(self, status):
        # Build columns (Author, Content, Time, Client) with boost info in Content
        if not status:
            return None

        is_boost = bool(status.get('reblog'))
        source_obj = status['reblog'] if is_boost else status
        # Author column should show the account that posted (the booster)
        author_cell = status['account'].get('display_name') or status['account'].get('username')

        # Content column: for boosts include 'boosting Original (handle): <content>'
        if is_boost:
            original = source_obj['account'].get('display_name') or source_obj['account'].get('username')
            handle = source_obj['account'].get('acct', '')
            # prefer spoiler text when present on the boosted post
            if source_obj.get('spoiler_text'):
                content_body = f"CW: {source_obj['spoiler_text']} (press Enter to view)"
            else:
                content_body = strip_html(source_obj.get('content', '')).strip()
            content_cell = f"boosting {original} ({handle}): {content_body}"
        else:
            # prefer spoiler text when present on the normal post
            if status.get('spoiler_text'):
                content_cell = f"CW: {status['spoiler_text']} (press Enter to view)"
            else:
                content_cell = strip_html(status.get('content', '')).strip()

        time_cell = self.format_time(source_obj.get('created_at')) or ''
        client_cell = self.get_app_name(source_obj) or ''

        return [author_cell or '', content_cell or '', time_cell, client_cell]

    def row_from_notification(self, notification):
        ntype = notification.get('type')
        account = notification.get('account', {})
        user = account.get('display_name') or account.get('username') or 'Unknown'
        status = notification.get('status') or {}

        time_cell = self.format_time(status.get('created_at')) if status else ''
        client_cell = self.get_app_name(status) if status else ''
        content = strip_html(status.get('content', '')).strip() if status else ''

        if ntype == 'favourite':
            return [f"{user} favorited", content, time_cell or '', client_cell or '']
        if ntype == 'reblog':
            return [f"{user} boosted", content, time_cell or '', client_cell or '']
        if ntype == 'mention':
            return [f"{user} mentioned you", content, time_cell or '', client_cell or '']
        return [f"{user}: {ntype}", '', '', '']

    def show_post_details(self):
        selection = self.posts_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        current_item = self.timeline_tree.GetSelection()
        if current_item == self.timeline_nodes["notifications"]:
            raw = self.timelines_data["notifications"][selection]
            status = raw.get("status") if isinstance(raw, dict) else None
            if not status:
                wx.MessageBox("This notification has no associated post to open.", "No Post", wx.OK | wx.ICON_INFORMATION)
                return
        else:
            key = next((k for k,v in self.timeline_nodes.items() if v == current_item), None)
            if not key:
                return
            status = self.timelines_data[key][selection]
        dlg = PostDetailsDialog(self, self.mastodon, status, self.me)
        dlg.ShowModal()
        dlg.Destroy()

    def get_app_name(self, status_or_boost):
        """Return the application/client name for a status-like object."""
        if not status_or_boost:
            return 'Unknown'
        app = status_or_boost.get('application') or {}
        if isinstance(app, dict):
            return app.get('name', 'Unknown')
        # some libraries may return a string
        return str(app) or 'Unknown'

    def format_time(self, created_at):
        """Return a human-friendly time string with singular units fixed."""
        if not created_at:
            return ''
        return singularize_time(get_time_ago(created_at))
