SUPPORTED_NOTIFICATION_TYPES = [
	"mention",
	"status",
	"reblog",
	"follow",
	"follow_request",
	"favourite",
	"poll",
	"update",
	"added_to_collection",
	"collection_update",
]


def api_request(mastodon, method, endpoint, params=None, use_json=False):
	params = {key: value for key, value in (params or {}).items() if value is not None}
	return mastodon._Mastodon__api_request(method, endpoint, params, use_json=use_json)


def collection_from_response(response):
	if isinstance(response, dict):
		return response.get("collection", response)
	return response


def collections_from_response(response):
	if isinstance(response, dict):
		return response.get("collections") or response.get("collections:") or []
	return response or []


def fetch_notifications(mastodon, **params):
	params["supported_types"] = SUPPORTED_NOTIFICATION_TYPES
	return api_request(mastodon, "GET", "/api/v1/notifications", params)


def fetch_notification_policy(mastodon):
	return api_request(mastodon, "GET", "/api/v2/notifications/policy")


def update_notification_policy(mastodon, policy):
	return api_request(mastodon, "PATCH", "/api/v2/notifications/policy", policy)


def search_v2(mastodon, query, **params):
	params["q"] = query
	return api_request(mastodon, "GET", "/api/v2/search", params)


def fetch_account_statuses(mastodon, account_id, exclude_direct=False, **params):
	params["exclude_direct"] = exclude_direct
	return api_request(mastodon, "GET", f"/api/v1/accounts/{account_id}/statuses", params)


def fetch_current_profile(mastodon):
	return api_request(mastodon, "GET", "/api/v1/profile")


def update_current_profile(mastodon, profile):
	params = {}
	for key, value in profile.items():
		if key == "fields_attributes":
			for index, field in enumerate(value):
				params[f"fields_attributes[{index}][name]"] = field.get("name", "")
				params[f"fields_attributes[{index}][value]"] = field.get("value", "")
		elif key == "attribution_domains":
			params["attribution_domains"] = value
		else:
			params[key] = value
	return api_request(mastodon, "PATCH", "/api/v1/profile", params)


def fetch_account_collections(mastodon, account_id, limit=80, offset=0):
	response = api_request(
		mastodon,
		"GET",
		f"/api/v1/accounts/{account_id}/collections",
		{"limit": limit, "offset": offset},
	)
	return collections_from_response(response)


def fetch_account_in_collections(mastodon, account_id, limit=80, offset=0):
	response = api_request(
		mastodon,
		"GET",
		f"/api/v1/accounts/{account_id}/in_collections",
		{"limit": limit, "offset": offset},
	)
	return collections_from_response(response)


def fetch_collection(mastodon, collection_id):
	return api_request(mastodon, "GET", f"/api/v1/collections/{collection_id}")


def create_collection(mastodon, collection):
	return collection_from_response(api_request(mastodon, "POST", "/api/v1/collections", collection, use_json=True))


def update_collection(mastodon, collection_id, collection):
	return collection_from_response(api_request(mastodon, "PATCH", f"/api/v1/collections/{collection_id}", collection, use_json=True))


def delete_collection(mastodon, collection_id):
	return api_request(mastodon, "DELETE", f"/api/v1/collections/{collection_id}")


def add_collection_account(mastodon, collection_id, account_id):
	return api_request(
		mastodon,
		"POST",
		f"/api/v1/collections/{collection_id}/items",
		{"account_id": account_id},
		use_json=True,
	)


def remove_collection_item(mastodon, collection_id, item_id):
	return api_request(mastodon, "DELETE", f"/api/v1/collections/{collection_id}/items/{item_id}")


def revoke_collection_item(mastodon, collection_id, item_id):
	return api_request(mastodon, "POST", f"/api/v1/collections/{collection_id}/items/{item_id}/revoke")


def fetch_annual_report_state(mastodon, year):
	return api_request(mastodon, "GET", f"/api/v1/annual_reports/{year}/state")


def generate_annual_report(mastodon, year):
	return api_request(mastodon, "POST", f"/api/v1/annual_reports/{year}/generate")


def fetch_annual_report(mastodon, year):
	return api_request(mastodon, "GET", f"/api/v1/annual_reports/{year}")
