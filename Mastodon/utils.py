import os
import pickle
import html
import re
from datetime import datetime, timezone
from dateutil import parser # Use dateutil for robust parsing

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
	# If created_at is a string, parse it into a datetime object
	if not isinstance(created_at, datetime):
		if not created_at:
			return ""
		try:
			created_at = parser.parse(created_at)
		except (parser.ParserError, TypeError):
			return "" # Return empty string or handle error appropriately

	now = datetime.now(timezone.utc)
	# Ensure 'now' is offset-aware if 'created_at' is
	if created_at.tzinfo and not now.tzinfo:
		now = now.astimezone()

	diff = (now - created_at).total_seconds()
	if diff < 60:
		return f"{int(diff)} seconds ago"
	elif diff < 3600:
		return f"{int(diff // 60)} minutes ago"
	elif diff < 86400:
		return f"{int(diff // 3600)} hours ago"
	else:
		return f"{int(diff // 86400)} days ago"