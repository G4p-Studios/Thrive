import wx
from utils import strip_html

class ViewProfileDialog(wx.Dialog):
	def __init__(self, parent, account):
		display_name = account.get("display_name", "")
		acct = account.get("acct", "")
		title = f"Profile for {display_name} ({acct})"
		super().__init__(parent, title=title, size=(600, 400))

		username = account.get("username", "")
		bio = strip_html(account.get("note", ""))
		followers = account.get("followers_count", 0)
		following = account.get("following_count", 0)
		statuses = account.get("statuses_count", 0)

		created = account.get("created_at")
		created_at = created.strftime("%B %d, %Y") if created else "Unknown"

		last_post = account.get("last_status_at", "") or "Unknown"
		website = account.get("url", "")

		info = f"""Display Name: {display_name}
Username: {acct}
Bio: {bio}
Followers: {followers}
Friends: {following}
Posts: {statuses}
Created: {created_at}
Last post: {last_post}
Website: {website}"""

		self.text = wx.TextCtrl(self, value=info, style=wx.TE_MULTILINE | wx.TE_READONLY)
		self.close_button = wx.Button(self, label="&Close",id=wx.ID_CANCEL)
		self.close_button.Bind(wx.EVT_CLOSE, lambda e: self.Close())

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.text, 1, wx.ALL | wx.EXPAND, 10)
		sizer.Add(self.close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

		self.SetSizer(sizer)
		self.text.SetFocus()