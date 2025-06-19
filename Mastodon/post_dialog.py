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
	def __init__(self, parent, mastodon, status):
		account = status["account"]
		display_name = account.get("display_name", "")
		acct = account.get("acct", "")
		super().__init__(parent, title=f"View Post from {display_name} ({acct}) dialog", size=(600, 500))
		self.mastodon = mastodon
		self.status = status["reblog"] if status.get("reblog") else status
		self.account = account

		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

		content = strip_html(self.status["content"])
		self.reply_users=""
		me=self.mastodon.me().acct
		for i in content.split(" "):
			if i.startswith("@") and i!="@"+me: self.reply_users+=i+" "
		if account.acct!=me: self.reply_users=account.acct+" "+self.reply_users
		self.content_box = wx.TextCtrl(self, value=content, style=wx.TE_MULTILINE | wx.TE_READONLY)

		app = self.status.get("application")
		source = app["name"] if app and "name" in app else "Unknown"
		time_posted = self.status["created_at"].astimezone().strftime("%I:%M %p")
		boosts = self.status.get("reblogs_count", 0)
		favs = self.status.get("favourites_count", 0)
		replies = self.status.get("replies_count", 0)
		visibility = self.status.get("visibility", "unknown")
		language = self.status.get("language", "unknown")

		detail_text = f"""Posted: {time_posted}
From: {source}
Boosted {boosts} times
Favorited {favs} times.
{replies} replies
Visibility: {visibility}
Language: {language}"""
		self.details_box = wx.TextCtrl(self, value=detail_text, style=wx.TE_MULTILINE | wx.TE_READONLY)

		self.reply_button = wx.Button(self, label="&Reply")
		self.boost_button = wx.Button(self, label="Unboost" if self.status["reblogged"] else "&Boost")
		self.fav_button = wx.Button(self, label="Unfavourite" if self.status["favourited"] else "&Favourite")
		self.profile_button = wx.Button(self, label=f"View &Profile of {display_name}")
		self.close_button = wx.Button(self, label="&Close",id=wx.ID_CANCEL)

		self.reply_button.Bind(wx.EVT_BUTTON, self.reply)
		self.boost_button.Bind(wx.EVT_BUTTON, self.toggle_boost)
		self.fav_button.Bind(wx.EVT_BUTTON, self.toggle_fav)
		self.profile_button.Bind(wx.EVT_BUTTON, lambda e: ViewProfileDialog(self, self.account).ShowModal())
		self.close_button.Bind(wx.EVT_CLOSE, lambda e: self.Close())

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.content_box, 1, wx.ALL | wx.EXPAND, 5)
		sizer.Add(self.details_box, 0, wx.ALL | wx.EXPAND, 5)

		btns = wx.BoxSizer(wx.HORIZONTAL)
		btns.Add(self.reply_button, 0, wx.ALL, 5)
		btns.Add(self.boost_button, 0, wx.ALL, 5)
		btns.Add(self.fav_button, 0, wx.ALL, 5)
		btns.AddStretchSpacer()
		btns.Add(self.profile_button, 0, wx.ALL, 5)
		btns.Add(self.close_button, 0, wx.ALL, 5)

		sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 5)
		self.SetSizer(sizer)

	def on_key(self, event):
		if event.GetKeyCode() == wx.WXK_ESCAPE:
			self.Close()
		else:
			event.Skip()

	def reply(self, event):
		dialog = wx.Dialog(self, title="Reply to Post", size=(500, 250))
		panel = wx.Panel(dialog)
		vbox = wx.BoxSizer(wx.VERTICAL)

		label = wx.StaticText(panel, label="Reply message:")
		self.reply_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
		self.reply_text.SetValue(("@"+self.reply_users if self.reply_users!="" else ""))
		self.reply_text.SetInsertionPoint(len(self.reply_text.GetValue()))

		send_button = wx.Button(panel, label="Send Reply")
		cancel_button = wx.Button(panel, label="Cancel",id=wx.ID_CANCEL)

		send_button.Bind(wx.EVT_BUTTON, lambda e: self.send_reply(dialog, self.reply_text.GetValue()))
		cancel_button.Bind(wx.EVT_CLOSE, lambda e: dialog.Close())

		vbox.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		vbox.Add(self.reply_text, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

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
			self.mastodon.status_post(text, in_reply_to_id=self.status["id"])
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
				main_frame.boostsnd.play()
				self.mastodon.status_reblog(self.status["id"])
				self.boost_button.SetLabel("Unboost")
			self.status["reblogged"] = not self.status["reblogged"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Boost Error")

	def toggle_fav(self, event):
		try:
			if self.status["favourited"]:
				main_frame.unfavsnd.play()
				self.mastodon.status_unfavourite(self.status["id"])
				self.fav_button.SetLabel("Favourite")
			else:
				main_frame.favsnd.play()
				self.mastodon.status_favourite(self.status["id"])
				self.fav_button.SetLabel("Unfavourite")
			self.status["favourited"] = not self.status["favourited"]
		except Exception as e:
			wx.MessageBox(f"Error: {e}", "Favourite Error")
