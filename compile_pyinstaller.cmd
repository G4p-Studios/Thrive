@echo off
python versionfile.py
pyinstaller --windowed --upx-dir=C:\UPX --version-file=vdata.txt --hidden-import=wx.html2 --collect-submodules wx_accessible_webview mastodon/thrive.py
