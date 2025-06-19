import wx
import os
from utils import strip_html
from profile_dialog import ViewProfileDialog
from sound_lib import stream
from sound_lib import output as o
from sound_lib.main import BassError
from easysettings import EasySettings
import main_frame
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

		content = strip_html(self.status["content"])
		self.reply_users=""
		me=self.me['acct']
		for i in content.split(" "):
			if i.startswith("@") and i!="@"+me: self.reply_users+=i+" "
		if self.account.acct!=me: self.reply_users="@"+self.account.acct+" "+self.reply_users
		self.content_box = wx.TextCtrl(self, value=content, style=wx.TE_MULTILINE | wx.TE_READONLY)

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
		self.details_box = wx.TextCtrl(self, value=detail_text, style=wx.TE_MULTILINE | wx.TE_READONLY)

		self.reply_button = wx.Button(self, label="&Reply")
		self.boost_button = wx.Button(self, label="Unboost" if self.status["reblogged"] else "&Boost")
		self.fav_button = wx.Button(self, label="Unfavourite" if self.status["favourited"] else "&Favourite")
		self.profile_button = wx.Button(self, label=f"View &Profile of {display_name}")
		self.take_down_button = wx.Button(self, label="&Take down")
		self.close_button = wx.Button(self, id=wx.ID_CANCEL, label="&Close")

		self.reply_button.Bind(wx.EVT_BUTTON, self.reply)
		self.boost_button.Bind(wx.EVT_BUTTON, self.toggle_boost)
		self.fav_button.Bind(wx.EVT_BUTTON, self.toggle_fav)
		self.take_down_button.Bind(wx.EVT_BUTTON, self.on_take_down)
		self.profile_button.Bind(wx.EVT_BUTTON, lambda e: ViewProfileDialog(self, self.account).ShowModal())

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.content_box, 1, wx.ALL | wx.EXPAND, 5)
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
		self.SetSizer(sizer)
		
		# Set up the accelerator table for keyboard shortcuts
		self.setup_accelerators()

	def setup_accelerators(self):
		accel_entries = []
		# Shortcut for Escape key to close the dialog
		accel_entries.append((wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_CANCEL))
		
		# Conditionally add the Alt+T shortcut for the "Take down" button
		if self.status['account']['id'] == self.me['id']:
			take_down_id = wx.NewIdRef()
			accel_entries.append((wx.ACCEL_ALT, ord('T'), take_down_id))
			self.Bind(wx.EVT_MENU, self.on_take_down, id=take_down_id)

		accel_tbl = wx.AcceleratorTable(accel_entries)
		self.SetAcceleratorTable(accel_tbl)

	def on_take_down(self, event):
		confirm = wx.MessageBox("Are you sure you want to take down this post? It will be removed from Mastodon. This action cannot be undone.", "Confirm Deletion", wx.YES_NO | wx.ICON_WARNING, self)
		if confirm == wx.YES:
			try:
				self.mastodon.status_delete(self.status['id'])
				self.Parent.update_posts()
				self.Close()
			except Exception as e:
				wx.MessageBox(f"Error deleting post: {e}", "Error", wx.OK | wx.ICON_ERROR, self)

	def reply(self, event):
		dialog = wx.Dialog(self, title="Reply to Post", size=(500, 300))
		panel = wx.Panel(dialog)
		vbox = wx.BoxSizer(wx.VERTICAL)

		label = wx.StaticText(panel, label="&Reply message:")
		self.reply_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
		self.reply_text.SetValue(self.reply_users.strip() + " " if self.reply_users.strip() else "")
		self.reply_text.SetInsertionPoint(len(self.reply_text.GetValue()))

		privacy_label = wx.StaticText(panel, label="P&rivacy:")
		self.reply_privacy_choice = wx.Choice(panel, choices=self.privacy_options)
		original_visibility = self.status.get("visibility", "public")
		try:
			default_index = self.privacy_values.index(original_visibility)
			self.reply_privacy_choice.SetSelection(default_index)
		except ValueError:
			self.reply_privacy_choice.SetSelection(0)

		send_button = wx.Button(panel, label="Send Reply")
		cancel_button = wx.Button(panel, id=wx.ID_CANCEL, label="Cancel")

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
				self.boost_button.SetLabel("Boost")
			else:
				if main_frame.boostsnd:
					main_frame.boostsnd.play()
				self.mastodon.status_reblog(self.status["id"])
				self.boost_button.SetLabel("Unboost")
			self.status["reblogged"] = not self.status["reblogged"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Boost Error")

	def toggle_fav(self, event):
		try:
			if self.status["favourited"]:
				if main_frame.unfavsnd:
					main_frame.unfavsnd.play()
				self.mastodon.status_unfavourite(self.status["id"])
				self.fav_button.SetLabel("Favourite")
			else:
				if main_frame.favsnd:
					main_frame.favsnd.play()
				self.mastodon.status_favourite(self.status["id"])
				self.fav_button.SetLabel("Unfavourite")
			self.status["favourited"] = not self.status["favourited"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Favourite Error")