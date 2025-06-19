import wx
import os
from easysettings import EasySettings
import main_frame
class SettingsDialog(wx.Dialog):
    def __init__(self, parent, on_save_callback=None):
        super().__init__(parent, title="Settings", size=(400, 200))
        self.conf = EasySettings("thrive.ini")
        self.on_save_callback = on_save_callback

        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        soundpack_label = wx.StaticText(panel, label="Select Sound Pack:")
        vbox.Add(soundpack_label, 0, wx.ALL | wx.EXPAND, 5)

        self.soundpack_choice = wx.Choice(panel)
        self.load_soundpacks()
        vbox.Add(self.soundpack_choice, 0, wx.ALL | wx.EXPAND, 5)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        save_button = wx.Button(panel, label="&Save")
        cancel_button = wx.Button(panel, label="&Cancel", id=wx.ID_CANCEL)
        hbox.Add(save_button, 0, wx.ALL, 5)
        hbox.Add(cancel_button, 0, wx.ALL, 5)

        vbox.Add(hbox, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        save_button.Bind(wx.EVT_BUTTON, self.on_save)

        panel.SetSizer(vbox)

    def load_soundpacks(self):
        self.soundpacks = []
        self.folder_map = {}
        if os.path.isdir("sounds"):
            for name in os.listdir("sounds"):
                path = os.path.join("sounds", name)
                if os.path.isdir(path) and name.startswith("Mastodon-"):
                    short = name.replace("Mastodon-", "")
                    self.soundpacks.append(short)
                    self.folder_map[short] = name
        if not self.soundpacks:
            self.soundpacks.append("default")
            self.folder_map["default"] = "Mastodon-default"

        self.soundpack_choice.AppendItems(self.soundpacks)

        current = self.conf.get("soundpack", "default")
        if current in self.soundpacks:
            self.soundpack_choice.SetStringSelection(current)
        else:
            self.soundpack_choice.SetSelection(0)

    def on_save(self, event):
        selected = self.soundpack_choice.GetStringSelection()
        self.conf.setsave("soundpack", selected)
        main_frame.folder = "Mastodon-" + main_frame.soundpack
        if self.on_save_callback:
            self.on_save_callback()
        wx.MessageBox("Settings saved.", "Settings Saved")
        self.EndModal(wx.ID_OK)
