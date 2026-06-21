import os
import sys
import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MASTODON_DIR = os.path.join(PROJECT_ROOT, "Mastodon")
sys.path.insert(0, MASTODON_DIR)

from post_rendering import build_status_body_html, status_plain_text


class PostRenderingTests(unittest.TestCase):
	def test_preserves_post_html_links(self):
		status = {
			"content": '<p>Hello <a href="https://example.com">example</a></p>',
			"account": {"display_name": "Alex", "acct": "alex@example.com"},
		}

		body = build_status_body_html(status)

		self.assertIn('<a href="https://example.com">example</a>', body)
		self.assertIn("<h1", body)

	def test_renders_quoted_status_as_separate_article(self):
		status = {
			"content": "<p>Main post</p>",
			"quote": {
				"quoted_status": {
					"content": "<p>Quoted body</p>",
					"account": {"display_name": "Quoter", "acct": "quoter@example.com"},
				}
			},
		}

		body = build_status_body_html(status)

		self.assertIn("Main post", body)
		self.assertIn("Quoted post by Quoter (@quoter@example.com)", body)
		self.assertIn("Quoted body", body)

	def test_escapes_app_generated_labels(self):
		status = {
			"content": "<p>Post</p>",
			"spoiler_text": '<script>alert("x")</script>',
			"quote": {
				"quoted_status": {
					"content": "<p>Quoted</p>",
					"account": {"display_name": "<b>Name</b>", "acct": "user@example.com"},
				}
			},
		}

		body = build_status_body_html(status)

		self.assertIn("&lt;script&gt;", body)
		self.assertIn("&lt;b&gt;Name&lt;/b&gt;", body)
		self.assertNotIn('<script>alert("x")</script>', body)

	def test_removes_server_prepended_quote_url(self):
		quoted_url = "https://social.example/@quoted/123"
		status = {
			"content": f'<p>RE: <a href="{quoted_url}">{quoted_url}</a> My response</p>',
			"quote": {
				"quoted_status": {
					"url": quoted_url,
					"content": "<p>Quoted</p>",
					"account": {"display_name": "Quoted", "acct": "quoted@example.com"},
				}
			},
		}

		body = build_status_body_html(status)

		main_article = body.split('<article class="quoted-post"', 1)[0]
		self.assertIn("My response", main_article)
		self.assertNotIn(quoted_url, main_article)

	def test_plain_text_matches_copy_and_edit_needs(self):
		status = {"content": "<p>First<br />Second</p><p>Third</p>"}

		self.assertEqual(status_plain_text(status), "First\nSecond\n\nThird")


if __name__ == "__main__":
	unittest.main()
