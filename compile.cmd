@echo off
python versionfile.py
pyinstaller --windowed --version-file=vdata.txt mastodon/thrive.py