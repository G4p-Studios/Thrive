@echo off
nuitka --standalone --windows-console-mode=disable --include-module=wx.html2 --include-package=wx_accessible_webview --company-name="G4p Studios" --product-name="Thrive" --file-version=0.0.10.0 --product-version=0.0.10.0 --file-description="Thrive" --copyright="Copyright 2025 G4p Studios." mastodon/thrive.py
