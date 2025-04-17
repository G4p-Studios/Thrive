import wx
import webbrowser
import os
import sounddevice as sd
import wave
from mastodon import Mastodon
from wx import FileDialog

CLIENT_ID_FILE = "thrive_clientcred.secret"
ACCESS_TOKEN_FILE = "thrive_usercred.secret"

class ThriveApp(wx.App):
	def OnInit(self):
		self.frame = AuthFrame(None, title="Thrive - Login")
		self.frame.Show()
		return True

class AuthFrame(wx.Frame):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs, size=(400, 250))

		self.panel = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)

		self.instance_label = wx.StaticText(self.panel, label="Mastodon Instance URL:")
		self.instance_input = wx.TextCtrl(self.panel, value="https://tweesecake.social")

		self.username_label = wx.StaticText(self.panel, label="Username:")
		self.username_input = wx.TextCtrl(self.panel)

		self.auth_button = wx.Button(self.panel, label="Authenticate")
		self.auth_button.Bind(wx.EVT_BUTTON, self.on_authenticate)

		vbox.Add(self.instance_label, 0, wx.ALL, 5)
		vbox.Add(self.instance_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.username_label, 0, wx.ALL, 5)
		vbox.Add(self.username_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.auth_button, 0, wx.ALL | wx.CENTER, 5)

		self.panel.SetSizer(vbox)

	def on_authenticate(self, event):
		instance_url = self.instance_input.GetValue().strip()
		username = self.username_input.GetValue().strip()

		if instance_url and username:
			Mastodon.create_app(
				"Thrive",
				api_base_url=instance_url,
				to_file=CLIENT_ID_FILE,
				scopes=["read", "write", "follow", "push"]
			)

			self.mastodon = Mastodon(client_id=CLIENT_ID_FILE, api_base_url=instance_url)
			auth_url = self.mastodon.auth_request_url(scopes=["read", "write", "follow", "push"])
			webbrowser.open(auth_url)

			wx.MessageBox("Please authorize Thrive in your browser and enter the provided access code.", "Authorization", wx.OK | wx.ICON_INFORMATION)
			self.access_code_dialog()
		else:
			wx.MessageBox("Please enter both instance URL and username!", "Error", wx.OK | wx.ICON_ERROR)

	def access_code_dialog(self):
		dialog = wx.TextEntryDialog(self, "Enter the authorization code:", "Access Code")
		if dialog.ShowModal() == wx.ID_OK:
			access_code = dialog.GetValue().strip()
			try:
				self.mastodon.log_in(
					code=access_code,
					to_file=ACCESS_TOKEN_FILE,
					scopes=["read", "write", "follow", "push"]
				)
				ThriveFrame(None, title="Thrive - Mastodon Client", mastodon=self.mastodon).Show()
				self.Close()
			except Exception as e:
				wx.MessageBox(f"Authentication failed: {e}", "Error", wx.OK | wx.ICON_ERROR)
		dialog.Destroy()

class ThriveFrame(wx.Frame):
	def __init__(self, *args, mastodon=None, **kwargs):
		super().__init__(*args, **kwargs, size=(800, 600))
		self.mastodon = mastodon
		self.media_files = []
		self.media_alt_text = {}

		self.panel = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)

		self.toot_input = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(780, 100))

		self.cw_toggle = wx.CheckBox(self.panel, label="Add Content Warning")
		self.cw_toggle.Bind(wx.EVT_CHECKBOX, self.on_toggle_cw)

		self.cw_input = wx.TextCtrl(self.panel, size=(780, 50))
		self.cw_input.Hide()

		self.attach_button = wx.Button(self.panel, label="Attach Media")
		self.attach_button.Bind(wx.EVT_BUTTON, self.on_attach_media)

		self.record_button = wx.Button(self.panel, label="Record Audio")
		self.record_button.Bind(wx.EVT_BUTTON, self.on_record_audio)

		self.post_button = wx.Button(self.panel, label="Post")
		self.post_button.Bind(wx.EVT_BUTTON, self.on_post)

		vbox.Add(self.toot_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.cw_toggle, 0, wx.ALL, 5)
		vbox.Add(self.cw_input, 0, wx.ALL | wx.EXPAND, 5)
		vbox.Add(self.attach_button, 0, wx.ALL, 5)
		vbox.Add(self.record_button, 0, wx.ALL, 5)
		vbox.Add(self.post_button, 0, wx.ALL | wx.CENTER, 5)

		self.panel.SetSizer(vbox)

	def on_toggle_cw(self, event):
		if self.cw_toggle.IsChecked():
			self.cw_input.Show()
		else:
			self.cw_input.Hide()
		self.panel.Layout()

	def on_attach_media(self, event):
		with wx.FileDialog(self, "Select media file(s)", wildcard="Media files (*.png;*.jpg;*.jpeg;*.mp3;*.wav)|*.png;*.jpg;*.jpeg;*.mp3;*.wav", style=wx.FD_OPEN | wx.FD_MULTIPLE) as dialog:
			if dialog.ShowModal() == wx.ID_OK:
				for file in dialog.GetPaths():
					self.media_files.append(file)
					alt_text_dialog = wx.TextEntryDialog(self, f"Enter alt text for {os.path.basename(file)} (optional):", "Alt Text")
					if alt_text_dialog.ShowModal() == wx.ID_OK:
						self.media_alt_text[file] = alt_text_dialog.GetValue().strip()
					alt_text_dialog.Destroy()

	def on_record_audio(self, event):
		wx.MessageBox("Audio recording not implemented yet.", "Info", wx.OK | wx.ICON_INFORMATION)

	def on_post(self, event):
		if not self.mastodon:
			wx.MessageBox("Not authenticated!", "Error", wx.OK | wx.ICON_ERROR)
			return

		status = self.toot_input.GetValue().strip()
		content_warning = self.cw_input.GetValue().strip() if self.cw_toggle.IsChecked() else None

		media_ids = []

		if self.media_files:
			for media_file in self.media_files:
				try:
					alt_text = self.media_alt_text.get(media_file, "").strip()
					media = self.mastodon.media_post(media_file, description=alt_text if alt_text else None)
					media_ids.append(media['id'])
				except Exception as e:
					wx.MessageBox(f"Error uploading {media_file}: {e}", "Error", wx.OK | wx.ICON_ERROR)
					return

		if status or media_ids:
			try:
				self.mastodon.status_post(status, spoiler_text=content_warning, media_ids=media_ids)
				wx.MessageBox("Toot posted successfully!", "Success", wx.OK | wx.ICON_INFORMATION)

				# Reset UI
				self.toot_input.SetValue("")
				self.cw_input.SetValue("")
				self.cw_input.Hide()
				self.cw_toggle.SetValue(False)
				self.media_files.clear()
				self.media_alt_text.clear()
			except Exception as e:
				wx.MessageBox(f"Error posting toot: {e}", "Error", wx.OK | wx.ICON_ERROR)
		else:
			wx.MessageBox("Cannot post an empty toot!", "Error", wx.OK | wx.ICON_ERROR)

if __name__ == "__main__":
	app = ThriveApp()
	app.MainLoop()
