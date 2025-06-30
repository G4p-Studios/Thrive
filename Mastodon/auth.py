import wx
import webbrowser
from mastodon import Mastodon
from utils import save_user_data
from main_frame import ThriveFrame

class AuthFrame(wx.Frame):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs, size=(400, 250))
		self.panel = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)

		self.instance_label = wx.StaticText(self.panel, label="Mastodon Instance URL:")
		self.instance_input = wx.TextCtrl(self.panel, value="https://mastodon.social")

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
		if not instance_url or not username:
			wx.MessageBox("Please enter both instance URL and username.", "Error")
			return

		try:
			client_id, client_secret = Mastodon.create_app(
				"Thrive",
				api_base_url=instance_url,
				scopes=["read", "write", "follow"]
			)

			self.mastodon = Mastodon(
				client_id=client_id,
				client_secret=client_secret,
				api_base_url=instance_url
			)

			auth_url = self.mastodon.auth_request_url(scopes=["read", "write", "follow"])
			webbrowser.open(auth_url)

			code = wx.GetTextFromUser("Enter the authorization code from the browser:", "Enter Code")
			self.mastodon.log_in(code=code, scopes=["read", "write", "follow"])

			save_user_data({
				"client_id": client_id,
				"client_secret": client_secret,
				"access_token": self.mastodon.access_token,
				"instance_url": instance_url,
				"username": username
			})

			ThriveFrame(None, title="Thrive Mastodon Client", mastodon=self.mastodon).Show()
			self.Close()

		except Exception as e:
			wx.MessageBox(f"Authentication failed: {e}", "Error")
