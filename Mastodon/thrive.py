import wx
from auth import AuthFrame
from utils import load_user_data
from main_frame import ThriveFrame
from mastodon import Mastodon

class ThriveApp(wx.App):
    def OnInit(self):
        user_data = load_user_data()
        if user_data and "access_token" in user_data:
            try:
                mastodon = Mastodon(
                    client_id=user_data["client_id"],
                    client_secret=user_data["client_secret"],
                    access_token=user_data["access_token"],
                    api_base_url=user_data["instance_url"]
                )
                self.frame = ThriveFrame(None, title="Thrive Mastodon Client", mastodon=mastodon)
            except Exception as e:
                wx.MessageBox(f"Error loading saved session: {e}", "Error")
                self.frame = AuthFrame(None, title="Thrive Login")
        else:
            self.frame = AuthFrame(None, title="Thrive Login")
        self.frame.Show()
        return True

if __name__ == "__main__":
    app = ThriveApp()
    app.MainLoop()
