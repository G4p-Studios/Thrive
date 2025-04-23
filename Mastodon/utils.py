import os
import pickle
import html
import re
from datetime import datetime, timezone

USER_DATA_FILE = "user.dat"

def save_user_data(data):
	with open(USER_DATA_FILE, "wb") as f:
		pickle.dump(data, f)

def load_user_data():
	if os.path.exists(USER_DATA_FILE):
		with open(USER_DATA_FILE, "rb") as f:
			return pickle.load(f)
	return {}

def strip_html(raw_html):
	clean = re.sub('<[^<]+?>', '', raw_html)
	return html.unescape(clean)

def get_time_ago(created_at):
	now = datetime.now(timezone.utc)
	diff = (now - created_at).total_seconds()
	if diff < 60:
		return f"{int(diff)} seconds ago"
	elif diff < 3600:
		return f"{int(diff // 60)} minutes ago"
	elif diff < 86400:
		return f"{int(diff // 3600)} hours ago"
	else:
		return f"{int(diff // 86400)} days ago"
