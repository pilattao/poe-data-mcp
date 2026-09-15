import json
import re
import time
import urllib.request
import urllib.parse

_HEADERS = {
    "User-Agent": "poe-data-mcp/0.3 (+https://github.com/pilattao/poe_mcp_suite)",
    "Accept": "application/json",
}

_LINK_PATTERNS = {
    "pobb.in": re.compile(r"https?://pobb\.in/\S+"),
    "poedb.tw": re.compile(r"https?://poedb\.tw/\S+PathOfBuilding\?id=\S+"),
    "pastebin": re.compile(r"https?://pastebin\.com/\S+"),
    "mobalytics": re.compile(r"https?://mobalytics\.gg/(?:poe|poe-2)/\S+"),
    "maxroll": re.compile(r"https?://maxroll\.gg/(?:poe|poe2)/\S+"),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+|https?://youtu\.be/[\w-]+"),
}


def _to_json_url(url: str) -> str:
    """Convert a Reddit post URL to its JSON API equivalent."""
    parsed = urllib.parse.urlsplit(url.strip())
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
            or parsed.hostname not in {'reddit.com', 'www.reddit.com', 'old.reddit.com', 'm.reddit.com'}):
        raise ValueError('Use a public Reddit post URL without credentials.')
    path = parsed.path.rstrip('/')
    if not re.search(r'/comments/[A-Za-z0-9]+(?:[/.]|$)', path):
        raise ValueError('Use a Reddit post URL containing /comments/<id>.')
    if not path.endswith('.json'):
        path += '.json'
    return urllib.parse.urlunsplit(('https', 'www.reddit.com', path, parsed.query, ''))


def _extract_links(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for label, pattern in _LINK_PATTERNS.items():
        matches = [m.rstrip(".,)>\"'") for m in pattern.findall(text)]
        if matches:
            found[label] = list(dict.fromkeys(matches))
    return found


def _collect_comments(node, results: list, max_depth: int = 2, depth: int = 0) -> None:
    if depth > max_depth:
        return
    if not isinstance(node, dict):
        return
    kind = node.get("kind", "")
    d = node.get("data", {})
    if kind == "t1" and d.get("body") not in (None, "[deleted]", "[removed]", ""):
        results.append((d.get("score", 0), d.get("author", "?"), d["body"]))
    replies = d.get("replies", {})
    if isinstance(replies, dict):
        for child in replies.get("data", {}).get("children", []):
            _collect_comments(child, results, max_depth, depth + 1)


def fetch_reddit_post(url: str, num_comments: int = 10) -> str:
    """Fetch a Reddit post and its top comments.

    Returns the post title, body, top comments by score, and any
    Path of Exile build links (pobb.in, poedb.tw, pastebin, Mobalytics,
    YouTube) found in the post or comments.

    Args:
        url: Reddit post URL (any form: www, old, or mobile reddit).
        num_comments: Number of top-scored comments to include (default 10).
    """
    if not 0 <= num_comments <= 50:
        raise ValueError('num_comments must be between 0 and 50.')
    json_url = _to_json_url(url)
    if not urllib.parse.urlsplit(json_url).query:
        json_url += '?limit=50'

    try:
        req = urllib.request.Request(json_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return (
                f"Reddit denied this public request (403 Forbidden). "
                f"Post availability could not be verified. URL attempted: {json_url}"
            )
        if e.code == 404:
            return f"Reddit post not found (404). Check the URL: {url}"
        return f"HTTP {e.code} fetching Reddit post: {url}"
    except Exception as e:
        return f"Failed to fetch Reddit post: {e}"

    if not isinstance(data, list) or len(data) < 2:
        return "Unexpected Reddit API response format."

    # Post
    post_children = data[0].get("data", {}).get("children", [])
    if not post_children:
        return "No post data found."
    post = post_children[0].get("data", {})

    title = post.get("title", "Untitled")
    body = post.get("selftext", "").strip()
    score = post.get("score", 0)
    author = post.get("author", "?")
    subreddit = post.get("subreddit", "?")
    post_url = post.get("url", "")
    cross_post_url = ""

    # Check if cross-post — follow it for the body if this one is empty
    if not body and post.get("crosspost_parent_list"):
        cp = post["crosspost_parent_list"][0]
        body = cp.get("selftext", "").strip()
        cross_post_url = f"https://www.reddit.com{cp.get('permalink','')}"

    # Comments
    all_comments: list[tuple[int, str, str]] = []
    for child in data[1].get("data", {}).get("children", []):
        _collect_comments(child, all_comments)
    all_comments.sort(key=lambda x: -x[0])
    top_comments = all_comments[:num_comments]

    # Extract links from post + all comments
    all_text = body + "\n" + "\n".join(c[2] for c in all_comments)
    links = _extract_links(all_text)

    # Build output
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append(f"r/{subreddit} · u/{author} · {score} pts")
    lines.append(f"Source: {url}")
    if cross_post_url:
        lines.append(f"Original post: {cross_post_url}")
    lines.append("")

    if links:
        lines.append("## Build Links Found")
        for label, urls in links.items():
            for u in urls:
                lines.append(f"- **{label}:** {u}")
        lines.append("")

    if body:
        lines.append("## Post")
        lines.append(body)
        lines.append("")

    if top_comments:
        lines.append(f"## Top Comments (by score, showing {len(top_comments)})")
        for score_c, auth_c, text_c in top_comments:
            lines.append(f"\n**u/{auth_c}** [{score_c} pts]")
            lines.append(text_c[:1500] + ("…" if len(text_c) > 1500 else ""))

    return "\n".join(lines)
