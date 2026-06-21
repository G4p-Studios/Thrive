import html
import re

from utils import strip_html


HREF_QUOTE = r'["\']'


def html_to_plain_text(content_html):
	processed_html = (content_html or "").replace("<br />", "\n").replace("<br>", "\n").replace("</p>", "\n\n")
	return strip_html(processed_html).strip()


def get_quoted_status(status):
	quote_obj = (status or {}).get("quote")
	if not quote_obj:
		return None
	if isinstance(quote_obj, dict):
		return quote_obj.get("quoted_status")
	return getattr(quote_obj, "quoted_status", None)


def quote_url_for_status(quoted_status):
	if not quoted_status:
		return ""
	return quoted_status.get("url") or quoted_status.get("uri") or ""


def strip_quote_url_from_text(content, quoted_url):
	if not quoted_url:
		return content.strip()
	content = re.sub(r"^(?:RE|QT):\s*" + re.escape(quoted_url) + r"\s*", "", content).strip()
	return content.rstrip().removesuffix(quoted_url).rstrip()


def strip_quote_url_from_html(content_html, quoted_url):
	if not quoted_url:
		return content_html or ""

	plain = html_to_plain_text(content_html)
	cleaned_plain = strip_quote_url_from_text(plain, quoted_url)
	if cleaned_plain == plain:
		return content_html or ""

	cleaned_html = content_html or ""
	cleaned_html = re.sub(r"^\s*(<p\b[^>]*>\s*)?(?:RE|QT):\s*", lambda m: m.group(1) or "", cleaned_html, count=1, flags=re.IGNORECASE)

	link_pattern = (
		r"<a\b[^>]*\bhref="
		+ HREF_QUOTE
		+ re.escape(quoted_url)
		+ HREF_QUOTE
		+ r"[^>]*>.*?</a>"
	)
	cleaned_html = re.sub(link_pattern, "", cleaned_html, count=1, flags=re.IGNORECASE | re.DOTALL)

	for url_text in (quoted_url, html.escape(quoted_url), html.escape(quoted_url, quote=True)):
		cleaned_html = cleaned_html.replace(url_text, "", 1)

	cleaned_html = re.sub(r"<p\b[^>]*>\s*</p>", "", cleaned_html, flags=re.IGNORECASE)
	cleaned_html = re.sub(r"\s+</p>", "</p>", cleaned_html)
	return cleaned_html.strip()


def account_label(account):
	account = account or {}
	display_name = account.get("display_name") or account.get("username") or "Unknown"
	acct = account.get("acct") or ""
	if acct:
		return f"{display_name} (@{acct})"
	return display_name


def build_status_body_html(status):
	status = status or {}
	content_html = status.get("content", "") or ""
	quoted_status = get_quoted_status(status)
	quoted_url = quote_url_for_status(quoted_status)
	content_html = strip_quote_url_from_html(content_html, quoted_url)

	parts = [
		'<article class="post-content" aria-labelledby="post-content-heading">',
		'<h1 id="post-content-heading">Post content</h1>',
	]
	spoiler_text = status.get("spoiler_text")
	if spoiler_text:
		parts.append(f'<p class="content-warning"><strong>Content warning:</strong> {html.escape(spoiler_text)}</p>')
	parts.append(content_html or "<p></p>")
	parts.append("</article>")

	if quoted_status:
		quoted_account = quoted_status.get("account", {})
		quoted_label = html.escape(account_label(quoted_account))
		quoted_content = quoted_status.get("content", "") or ""
		parts.extend(
			[
				'<article class="quoted-post" aria-labelledby="quoted-post-heading">',
				f'<h2 id="quoted-post-heading">Quoted post by {quoted_label}</h2>',
				quoted_content or "<p></p>",
				"</article>",
			]
		)

	return "\n".join(parts)


def status_plain_text(status):
	status = status or {}
	content = html_to_plain_text(status.get("content", ""))
	quoted_status = get_quoted_status(status)
	quoted_url = quote_url_for_status(quoted_status)
	return strip_quote_url_from_text(content, quoted_url)
