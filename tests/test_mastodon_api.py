import os
import sys
import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MASTODON_DIR = os.path.join(PROJECT_ROOT, "Mastodon")
sys.path.insert(0, MASTODON_DIR)

from mastodon_api import (
	SUPPORTED_NOTIFICATION_TYPES,
	collections_from_response,
	fetch_account_statuses,
	fetch_notification_policy,
	fetch_notifications,
	search_v2,
	update_current_profile,
	update_notification_policy,
)


class FakeMastodon:
	def __init__(self):
		self.calls = []

	def _Mastodon__api_request(self, method, endpoint, params={}, files={}, headers={}, access_token_override=None, base_url_override=None, do_ratelimiting=True, use_json=False, parse=True, return_response_object=False, skip_error_check=False, lang_override=None, override_type=None, force_pagination=False):
		self.calls.append((method, endpoint, params, use_json))
		return {"ok": True}


class MastodonApiTests(unittest.TestCase):
	def test_notifications_include_supported_types_for_fallbacks(self):
		api = FakeMastodon()

		fetch_notifications(api, limit=40, types=["mention"])

		method, endpoint, params, use_json = api.calls[0]
		self.assertEqual(method, "GET")
		self.assertEqual(endpoint, "/api/v1/notifications")
		self.assertEqual(params["limit"], 40)
		self.assertEqual(params["types"], ["mention"])
		self.assertEqual(params["supported_types"], SUPPORTED_NOTIFICATION_TYPES)
		self.assertFalse(use_json)

	def test_notification_policy_helpers_use_v2_policy_endpoint(self):
		api = FakeMastodon()

		fetch_notification_policy(api)
		update_notification_policy(api, {"for_bots": "filter"})

		self.assertEqual(api.calls[0], ("GET", "/api/v2/notifications/policy", {}, False))
		self.assertEqual(api.calls[1], ("PATCH", "/api/v2/notifications/policy", {"for_bots": "filter"}, False))

	def test_search_v2_can_resolve_collection_urls(self):
		api = FakeMastodon()

		search_v2(api, "https://example.social/collections/1", resolve=True)

		method, endpoint, params, use_json = api.calls[0]
		self.assertEqual(method, "GET")
		self.assertEqual(endpoint, "/api/v2/search")
		self.assertEqual(params["q"], "https://example.social/collections/1")
		self.assertEqual(params["resolve"], True)
		self.assertFalse(use_json)

	def test_account_statuses_can_exclude_direct_profile_posts(self):
		api = FakeMastodon()

		fetch_account_statuses(api, "123", exclude_direct=True, limit=20)

		self.assertEqual(api.calls[0][1], "/api/v1/accounts/123/statuses")
		self.assertEqual(api.calls[0][2]["exclude_direct"], True)
		self.assertEqual(api.calls[0][2]["limit"], 20)

	def test_profile_update_flattens_field_attributes(self):
		api = FakeMastodon()

		update_current_profile(
			api,
			{
				"display_name": "Alex",
				"fields_attributes": [{"name": "Site", "value": "https://example.com"}],
				"attribution_domains": ["example.com"],
			},
		)

		method, endpoint, params, use_json = api.calls[0]
		self.assertEqual(method, "PATCH")
		self.assertEqual(endpoint, "/api/v1/profile")
		self.assertEqual(params["display_name"], "Alex")
		self.assertEqual(params["fields_attributes[0][name]"], "Site")
		self.assertEqual(params["fields_attributes[0][value]"], "https://example.com")
		self.assertEqual(params["attribution_domains"], ["example.com"])
		self.assertFalse(use_json)

	def test_collections_response_accepts_documented_and_typo_keys(self):
		self.assertEqual(collections_from_response({"collections": [1, 2]}), [1, 2])
		self.assertEqual(collections_from_response({"collections:": [3]}), [3])
		self.assertEqual(collections_from_response([4]), [4])


if __name__ == "__main__":
	unittest.main()
