import wx
import os
from easysettings import EasySettings
import main_frame

# --- Dark Mode for MSW ---
try:
    import ctypes
    from ctypes import wintypes

    class WxMswDarkMode:
        """
        Manages dark mode for top-level windows on Microsoft Windows.
        Uses undocumented APIs for immersive dark mode, so it may break.
        """
        _instance = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = super(WxMswDarkMode, cls).__new__(cls)
                try:
                    cls.dwmapi = ctypes.WinDLL("dwmapi")
                    # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 in recent SDKs
                    cls.DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                except (AttributeError, OSError):
                    cls.dwmapi = None
            return cls._instance

        def enable(self, window: wx.Window, enable: bool = True):
            """
            Enable or disable dark mode for a given wx.Window.
            """
            if not self.dwmapi:
                return False

            try:
                hwnd = window.GetHandle()
                value = wintypes.BOOL(enable)
                hr = self.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    self.DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(value),
                    ctypes.sizeof(value)
                )
                # If attribute 20 fails, try older attribute 19 as a fallback
                if hr != 0:
                    self.DWMWA_USE_IMMERSIVE_DARK_MODE = 19
                    hr = self.dwmapi.DwmSetWindowAttribute(
                        hwnd,
                        self.DWMWA_USE_IMMERSIVE_DARK_MODE,
                        ctypes.byref(value),
                        ctypes.sizeof(value)
                    )
                return hr == 0
            except Exception:
                return False

except (ImportError, ModuleNotFoundError):
    # Create a dummy class if ctypes is not available (e.g., non-Windows)
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True):
            return False

# --- End of Dark Mode Class ---

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, on_save_callback=None):
        super().__init__(parent, title="Settings", size=(400, 200))
        self.conf = EasySettings("thrive.ini")
        self.on_save_callback = on_save_callback

        # --- Enable Dark Mode ---
        dark_color = wx.Colour(40, 40, 40)
        light_text_color = wx.WHITE
        
        dark_mode_manager = WxMswDarkMode()
        dark_mode_manager.enable(self)
        
        self.SetBackgroundColour(dark_color)
        
        panel = wx.Panel(self)
        panel.SetBackgroundColour(dark_color)
        vbox = wx.BoxSizer(wx.VERTICAL)

        soundpack_label = wx.StaticText(panel, label="Select Sound Pack:")
        soundpack_label.SetForegroundColour(light_text_color)
        vbox.Add(soundpack_label, 0, wx.ALL | wx.EXPAND, 5)

        self.soundpack_choice = wx.Choice(panel)
        self.soundpack_choice.SetBackgroundColour(dark_color)
        self.soundpack_choice.SetForegroundColour(light_text_color)
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
        # This line seems incorrect as main_frame.soundpack is not defined.
        # It should probably be updating the config that gets read elsewhere.
        # Correcting this would be:
        # main_frame.conf.setsave("soundpack", selected) 
        # However, modifying main_frame directly is avoided. The callback handles it.
        if self.on_save_callback:
            self.on_save_callback()
        wx.MessageBox("Settings saved. Sound changes will take effect on next restart or action.", "Settings Saved")
        self.EndModal(wx.ID_OK)