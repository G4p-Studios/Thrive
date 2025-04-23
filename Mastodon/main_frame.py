import wx
import threading
from datetime import datetime
from utils import strip_html, get_time_ago
from post_dialog import PostDetailsDialog

class ThriveFrame(wx.Frame):
	def __init__(self, *args, mastodon=None, **kwargs):
		super().__init__(*args, **kwargs, size=(800, 600))
		self.mastodon = mastodon
		self.status_map = []

		self.panel = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)

		self.toot_label = wx.StaticText(self.panel, label="Post:")
		self.toot_input = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(780, 100))

		self.cw_label = wx.StaticText(self.panel, label="Content warning title:")
		self.cw_input = wx.TextCtrl(self.panel, size=(780, 30))
		self.cw_toggle = wx.CheckBox(self.panel, label="Add Content Warning")
		self.cw_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_cw)
		self.cw_input.Hide()
		self.cw_label.Hide()

		self.post_button = wx.Button(self.panel, label="Post")
		self.post_button.Bind(wx.EVT_BUTTON, self.on_post)

		self.exit_button = wx.Button(self.panel, label="Exit")
		self.exit_button.Bind(wx.EVT_BUTTON, lambda e: self.Close())

		self.posts_label = wx.StaticText(self.panel, label="Posts List:")
		self.posts_list = wx.ListBox(self.panel, style=wx.LB_SINGLE, size=(780, 200))

		vbox.Add(self.toot_label, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.toot_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.cw_label, 0, wx.LEFT | wx.RIGHT, 5)
		vbox.Add(self.cw_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.cw_toggle, 0, wx.ALL, 5)
		vbox.Add(self.post_button, 0, wx.ALL | wx.CENTER, 5)
		vbox.Add(self.exit_button, 0, wx.ALL | wx.CENTER, 5)
		vbox.Add(self.posts_label, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.posts_list, 1, wx.ALL | wx.EXPAND, 5)

		self.panel.SetSizer(vbox)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)

		self.refresh_timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.update_posts)
		self.refresh_timer.Start(60000)  # 60 seconds
		self.update_posts()

	def on_key_press(self, event):
		if event.GetKeyCode() == wx.WXK_RETURN and self.FindFocus() == self.posts_list:
			self.show_post_details()
		else:
			event.Skip()

	def on_toggle_cw(self, event):
		show = self.cw_toggle.IsChecked()
		self.cw_input.Show(show)
		self.cw_label.Show(show)
		self.panel.Layout()

	def on_post(self, event):
		status = self.toot_input.GetValue().strip()
		spoiler = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None
		if not status:
			wx.MessageBox("Cannot post empty status.", "Error")
			return
		try:
			self.mastodon.status_post(status, spoiler_text=spoiler)
			wx.MessageBox("Posted successfully!", "Success")
			self.toot_input.SetValue("")
			self.cw_input.SetValue("")
			self.cw_toggle.SetValue(False)
			self.on_toggle_cw(None)
			self.update_posts()
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Post Error")

	def update_posts(self, event=None):
		def fetch_and_update():
			self.status_map.clear()
			wx.CallAfter(self.posts_list.Clear)

			try:
				statuses = self.mastodon.timeline_home(limit=40)

				# Convert to datetime if needed and sort by newest
				for s in statuses:
					if isinstance(s["created_at"], str):
						s["created_at"] = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
				statuses.sort(key=lambda s: s["created_at"], reverse=True)

				for status in statuses:
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

					wx.CallAfter(self.status_map.append, status)
					wx.CallAfter(self.posts_list.Append, display)

			except Exception as e:
				wx.CallAfter(self.posts_list.Append, f"Error loading posts: {e}")

		threading.Thread(target=fetch_and_update, daemon=True).start()

	def show_post_details(self, event=None):
		selection = self.posts_list.GetSelection()
		if 0 <= selection < len(self.status_map):
			status = self.status_map[selection]
			dlg = PostDetailsDialog(self, self.mastodon, status)
			dlg.ShowModal()
			dlg.Destroy()
