import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
USER_AGENT = "get-latest/1.0 (+https://github.com/rudu1/get-latest)"
BLUESKY_BASE = "https://api.bsky.app/xrpc"
REDDIT_BASE = "https://www.reddit.com"
HN_BASE = "https://hacker-news.firebaseio.com/v0"
GDELT_DOC_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
WIKIMEDIA_FEATURED_BASE = "https://api.wikimedia.org/feed/v1/wikipedia/en/featured"
BLUESKY_HOT_FEED = "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot"

CACHE = {}
CACHE_LOCK = threading.Lock()

STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "any", "are",
    "around", "been", "before", "being", "between", "both", "but", "can",
    "could", "did", "does", "doing", "from", "for", "get", "got", "had",
    "has", "have", "here", "how", "https", "into", "its", "just", "latest",
    "more", "most", "new", "not", "now", "off", "out", "over", "people",
    "really", "said", "same", "search", "should", "some", "such", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "thing", "those", "through", "today", "topic", "trending", "very", "want",
    "what", "when", "where", "which", "while", "who", "why", "with", "would", "your",
    "open", "thread", "full", "discussion", "story", "post", "shared", "link",
    "reddit", "bluesky", "hacker", "news",
}


def load_env_file():
    if not ENV_PATH.exists():
        return

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")


def fetch_json(url, headers=None, timeout=25):
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def post_json(url, payload, headers=None, timeout=45):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)

    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=request_headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def cached(key, ttl_seconds, producer):
    now = time.time()
    with CACHE_LOCK:
        cached_item = CACHE.get(key)
        if cached_item and now - cached_item["created_at"] < ttl_seconds:
            return cached_item["value"]

    value = producer()

    with CACHE_LOCK:
        CACHE[key] = {"created_at": now, "value": value}

    return value


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def trim_text(text, limit=220):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit - 3].rstrip()}..."


def tokenise(text):
    return re.findall(r"[a-z0-9][a-z0-9+.#-]*", text.lower())


def iso_to_epoch(value):
    if not value:
        return 0

    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def relative_time(epoch_seconds):
    if not epoch_seconds:
        return "Time unknown"

    seconds_ago = max(0, int(time.time() - epoch_seconds))
    if seconds_ago < 60:
        return "Just now"
    if seconds_ago < 3600:
        return f"{seconds_ago // 60}m ago"
    if seconds_ago < 86400:
        return f"{seconds_ago // 3600}h ago"
    return f"{seconds_ago // 86400}d ago"


def build_bluesky_url(handle, uri):
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def normalise_bluesky_post(post, bucket):
    record = post.get("record", {})
    author = post.get("author", {})
    handle = author.get("handle", "unknown.bsky.social")
    created_epoch = iso_to_epoch(record.get("createdAt") or post.get("indexedAt"))
    text = trim_text(record.get("text", ""), 260)

    return {
        "id": f"bsky:{post.get('uri')}",
        "source": "Bluesky",
        "bucket": bucket,
        "title": trim_text(record.get("text", "").split("\n", 1)[0], 92) or "Bluesky post",
        "snippet": text or "Open the post to view the full discussion.",
        "author": author.get("displayName") or handle,
        "community": f"@{handle}",
        "score": post.get("likeCount", 0),
        "score_label": "likes",
        "discussions": post.get("replyCount", 0),
        "discussions_label": "replies",
        "extra": post.get("repostCount", 0),
        "extra_label": "reposts",
        "url": build_bluesky_url(handle, post.get("uri", "")),
        "discussion_url": build_bluesky_url(handle, post.get("uri", "")),
        "created_epoch": created_epoch,
        "relative_time": relative_time(created_epoch),
        "image_url": "",
    }


def fetch_bluesky_hot(limit=5):
    feed = quote(BLUESKY_HOT_FEED, safe=":/")
    url = f"{BLUESKY_BASE}/app.bsky.feed.getFeed?feed={feed}&limit={limit}"
    data = fetch_json(url)
    items = []

    for entry in data.get("feed", []):
        post = entry.get("post")
        if post:
            items.append(normalise_bluesky_post(post, "Hot on Bluesky"))

    return items


def fetch_bluesky_search(query, sort, limit=6):
    encoded_query = quote(query)
    url = f"{BLUESKY_BASE}/app.bsky.feed.searchPosts?q={encoded_query}&sort={sort}&limit={limit}"
    data = fetch_json(url)
    bucket = "Bluesky latest" if sort == "latest" else "Bluesky top"
    return [normalise_bluesky_post(post, bucket) for post in data.get("posts", [])]


def normalise_reddit_post(post, bucket):
    created_epoch = int(post.get("created_utc", 0))
    community = post.get("subreddit_name_prefixed") or f"r/{post.get('subreddit', 'reddit')}"
    body = trim_text(post.get("selftext", ""), 240)
    title = trim_text(post.get("title", ""), 100) or "Reddit post"

    return {
        "id": f"reddit:{post.get('name') or post.get('id')}",
        "source": "Reddit",
        "bucket": bucket,
        "title": title,
        "snippet": body or "Open the Reddit thread to read the full conversation.",
        "author": post.get("author", "unknown"),
        "community": community,
        "score": post.get("score", 0),
        "score_label": "upvotes",
        "discussions": post.get("num_comments", 0),
        "discussions_label": "comments",
        "extra": round(post.get("upvote_ratio", 0) * 100),
        "extra_label": "upvote %",
        "url": f"{REDDIT_BASE}{post.get('permalink', '')}",
        "discussion_url": f"{REDDIT_BASE}{post.get('permalink', '')}",
        "created_epoch": created_epoch,
        "relative_time": relative_time(created_epoch),
        "image_url": post.get("thumbnail", "") if isinstance(post.get("thumbnail", ""), str) and post.get("thumbnail", "").startswith("http") else "",
    }


def fetch_reddit_listing(path, limit=5):
    url = f"{REDDIT_BASE}{path}"
    data = fetch_json(url)
    return [normalise_reddit_post(item.get("data", {}), "Reddit popular") for item in data.get("data", {}).get("children", [])[:limit]]


def fetch_reddit_popular(limit=5):
    return fetch_reddit_listing(f"/r/popular/hot.json?limit={limit}&raw_json=1", limit=limit)


def fetch_reddit_search(query, sort, limit=5):
    encoded_query = quote(query)
    url = f"{REDDIT_BASE}/search.json?q={encoded_query}&sort={sort}&limit={limit}&raw_json=1"
    data = fetch_json(url)
    bucket = "Reddit latest" if sort == "new" else "Reddit top"
    return [
        normalise_reddit_post(item.get("data", {}), bucket)
        for item in data.get("data", {}).get("children", [])
    ]


def normalise_news_article(article, bucket):
    title = trim_text(article.get("title", ""), 110) or "News article"
    source_domain = article.get("domain", "")
    source_name = source_domain.replace("www.", "") if source_domain else "news"
    created_epoch = iso_to_epoch(article.get("seendate", "").replace("Z", "+00:00")) if False else 0

    try:
        created_epoch = int(datetime.strptime(article.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        created_epoch = 0

    return {
        "id": f"news:{article.get('url')}",
        "source": "News",
        "bucket": bucket,
        "title": title,
        "snippet": trim_text(article.get("title", ""), 220) or "Open the article to read the full story.",
        "author": source_name,
        "community": source_name,
        "score": 0,
        "score_label": "rank",
        "discussions": 0,
        "discussions_label": "comments",
        "extra": article.get("sourcecountry", "global"),
        "extra_label": "region",
        "url": article.get("url", ""),
        "discussion_url": article.get("url", ""),
        "created_epoch": created_epoch,
        "relative_time": relative_time(created_epoch),
        "image_url": article.get("socialimage", ""),
    }


def normalise_google_news_item(item, bucket):
    source = item.find("source")
    source_url = source.attrib.get("url", "") if source is not None else ""
    source_name = (source.text or "Google News").strip() if source is not None else "Google News"
    article_url = item.findtext("link", "").strip()
    title = trim_text(item.findtext("title", ""), 110) or "News article"
    pub_date = item.findtext("pubDate", "")
    created_epoch = 0

    try:
        created_epoch = int(datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        created_epoch = 0

    favicon = ""
    if source_url:
        favicon = f"https://www.google.com/s2/favicons?domain_url={quote(source_url, safe=':/?=&')}&sz=128"

    return {
        "id": f"news-rss:{article_url}",
        "source": "News",
        "bucket": bucket,
        "title": title,
        "snippet": title,
        "author": source_name,
        "community": source_name,
        "score": 0,
        "score_label": "rank",
        "discussions": 0,
        "discussions_label": "comments",
        "extra": "publisher",
        "extra_label": "type",
        "url": article_url,
        "discussion_url": article_url,
        "created_epoch": created_epoch,
        "relative_time": relative_time(created_epoch),
        "image_url": favicon,
    }


def fetch_news_search(query, limit=5):
    encoded_query = quote(query)
    url = f"{GDELT_DOC_BASE}?query={encoded_query}&mode=artlist&maxrecords={limit}&format=json&sort=DateDesc"
    try:
        data = fetch_json(url, timeout=45)
        articles = [
            normalise_news_article(article, "News coverage")
            for article in data.get("articles", [])
            if article.get("url")
        ]
        if articles:
            return articles
    except Exception:
        pass

    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
    request = Request(rss_url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"})
    with urlopen(request, timeout=30) as response:
        xml_text = response.read().decode("utf-8", errors="replace")
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item")
    return [normalise_google_news_item(item, "News coverage") for item in items[:limit]]


def fetch_hn_story_ids(story_type):
    return cached(
        ("hn_ids", story_type),
        90,
        lambda: fetch_json(f"{HN_BASE}/{story_type}.json"),
    )


def fetch_hn_item(item_id):
    return cached(
        ("hn_item", item_id),
        600,
        lambda: fetch_json(f"{HN_BASE}/item/{item_id}.json"),
    )


def fetch_hn_items(item_ids):
    stories = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(fetch_hn_item, item_id): item_id for item_id in item_ids}
        for future in as_completed(futures):
            try:
                item = future.result()
                if item and item.get("type") == "story" and item.get("title"):
                    stories.append(item)
            except Exception:
                continue
    return stories


def normalise_hn_story(story, bucket, match_score=0):
    created_epoch = int(story.get("time", 0))
    story_url = story.get("url") or f"https://news.ycombinator.com/item?id={story.get('id')}"
    text = strip_html(story.get("text", ""))
    domain = urlparse(story_url).netloc or "news.ycombinator.com"
    snippet = trim_text(text, 240) or f"Link shared on {domain}."

    return {
        "id": f"hn:{story.get('id')}",
        "source": "Hacker News",
        "bucket": bucket,
        "title": story.get("title", "Untitled Hacker News story"),
        "snippet": snippet,
        "author": story.get("by", "unknown"),
        "community": "news.ycombinator.com",
        "score": story.get("score", 0),
        "score_label": "points",
        "discussions": story.get("descendants", 0),
        "discussions_label": "comments",
        "extra": story.get("by", "unknown"),
        "extra_label": "author",
        "url": story_url,
        "discussion_url": f"https://news.ycombinator.com/item?id={story.get('id')}",
        "created_epoch": created_epoch,
        "relative_time": relative_time(created_epoch),
        "match_score": match_score,
        "image_url": "",
    }


def fetch_hn_top(limit=5):
    story_ids = fetch_hn_story_ids("topstories")[:limit * 3]
    stories = fetch_hn_items(story_ids)
    stories.sort(key=lambda item: item.get("score", 0), reverse=True)
    return [normalise_hn_story(story, "HN front page") for story in stories[:limit]]


def match_score(query, story):
    query_norm = " ".join(query.lower().split())
    haystack = " ".join(
        part for part in [
            story.get("title", ""),
            strip_html(story.get("text", "")),
            story.get("url", ""),
        ] if part
    ).lower()

    score = 0
    if query_norm and query_norm in haystack:
        score += 8

    query_tokens = [token for token in tokenise(query_norm) if token not in STOPWORDS]
    matched_tokens = 0
    for token in query_tokens:
        if token in haystack:
            matched_tokens += 1
            score += 3 if len(token) > 2 else 1

    if query_tokens and matched_tokens == len(query_tokens):
        score += 4

    return score


def fetch_hn_matches(query, latest_limit=4, top_limit=4):
    new_story_ids = fetch_hn_story_ids("newstories")[:120]
    top_story_ids = fetch_hn_story_ids("topstories")[:100]

    new_stories = fetch_hn_items(new_story_ids)
    top_stories = fetch_hn_items(top_story_ids)

    latest_matches = []
    for story in new_stories:
        score = match_score(query, story)
        if score >= 4:
            latest_matches.append((score, story))

    front_page_matches = []
    for story in top_stories:
        score = match_score(query, story)
        if score >= 4:
            front_page_matches.append((score, story))

    latest_matches.sort(key=lambda entry: (entry[0], entry[1].get("time", 0)), reverse=True)
    front_page_matches.sort(key=lambda entry: (entry[0], entry[1].get("score", 0)), reverse=True)

    latest_items = [
        normalise_hn_story(story, "HN latest match", match_score=score)
        for score, story in latest_matches[:latest_limit]
    ]
    front_page_items = [
        normalise_hn_story(story, "HN front-page match", match_score=score)
        for score, story in front_page_matches[:top_limit]
    ]

    return latest_items, front_page_items


def dedupe_items(items):
    seen = set()
    unique_items = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique_items.append(item)
    return unique_items


def interleave_groups(groups, limit):
    merged = []
    max_len = max((len(group) for group in groups), default=0)

    for index in range(max_len):
        for group in groups:
            if index < len(group):
                merged.append(group[index])
                if len(merged) == limit:
                    return merged

    return merged[:limit]


def extract_keywords(items, query=""):
    query_tokens = set(tokenise(query))
    counts = Counter()

    for item in items:
        combined = f"{item['title']} {item['snippet']}".lower()
        for token in tokenise(combined):
            if token in STOPWORDS or token in query_tokens:
                continue
            if len(token) < 3 and not token.isdigit():
                continue
            if token.startswith("http") or token.startswith("www"):
                continue
            counts[token] += 1

    return [token for token, _ in counts.most_common(5)]


def clean_topic_candidate(value):
    candidate = re.sub(r"[^\w\s+#.]", " ", value or "")
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        return ""

    lowered = candidate.lower()
    if lowered in STOPWORDS:
        return ""

    words = candidate.split()
    if len(words) == 1 and (len(words[0]) < 3 or words[0].lower() in STOPWORDS):
        return ""

    return candidate


def infer_topic_from_title(title):
    compact = re.sub(r"\s+", " ", (title or "")).strip()
    if not compact:
        return ""

    compact = re.sub(r"^\[[^\]]+\]\s*", "", compact)
    compact = re.sub(r"^(Show HN|Ask HN|Tell HN|Launch HN)\s*[:\-]\s*", "", compact, flags=re.IGNORECASE)
    compact = compact.strip()

    for separator in ("|", ":", " - ", " — ", " – "):
        if separator in compact:
            candidate = compact.split(separator, 1)[0].strip(" -|:—–")
            cleaned = clean_topic_candidate(candidate)
            if 1 <= len(cleaned.split()) <= 4:
                return cleaned

    phrase_matches = re.findall(
        r"(?:[A-Z]{2,}(?:\s+\d+)?|[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[0-9]+|AI|US|UK|EU|VI)){0,3})",
        compact,
    )
    for phrase in phrase_matches:
        normalized = clean_topic_candidate(phrase)
        if normalized:
            return normalized

    tokens = re.findall(r"[A-Za-z0-9+#.]+", compact)
    significant = [token for token in tokens if token.lower() not in STOPWORDS and (len(token) > 2 or any(ch.isdigit() for ch in token))]
    if not significant:
        return ""

    if not any(token[0].isupper() or any(ch.isdigit() for ch in token) for token in significant[:3]):
        return ""

    return clean_topic_candidate(" ".join(significant[:3]).strip())


def extract_top_topics(items, max_topics=5):
    topics = []
    seen = set()

    for item in items:
        topic = infer_topic_from_title(item.get("title", ""))
        normalized = " ".join(topic.lower().split())
        if not topic or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        topics.append(topic)
        if len(topics) == max_topics:
            break

    if len(topics) < max_topics:
        for keyword in extract_keywords(items):
            topic = keyword.title()
            normalized = topic.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            topics.append(topic)
            if len(topics) == max_topics:
                break

    return topics[:max_topics]


def fetch_world_topics(limit=5):
    def producer():
        today_utc = datetime.now(timezone.utc).date()

        for days_back in range(0, 3):
            target_day = today_utc - timedelta(days=days_back)
            date_path = target_day.strftime("%Y/%m/%d")
            url = f"{WIKIMEDIA_FEATURED_BASE}/{date_path}"

            try:
                data = fetch_json(url)
                articles = data.get("mostread", {}).get("articles", [])
                topics = []

                for article in articles:
                    title = article.get("normalizedtitle") or article.get("title")
                    if not title or title.startswith("Special:") or title.lower().startswith("list of"):
                        continue
                    topics.append(title.replace("_", " "))
                    if len(topics) == limit:
                        return topics
            except Exception:
                continue

        return []

    return cached(("world_topics",), 1800, producer)


def build_local_summary(topic, items):
    source_breakdown = Counter(item["source"] for item in items)
    themes = extract_keywords(items, query=topic)
    theme_text = ", ".join(themes[:3]) if themes else "the same topic from different angles"

    if topic.lower() == "live trends":
        headline = f"Across the live feeds, {theme_text} is leading the conversation"
    else:
        headline = f"{topic} is surfacing as a mixed, fast-moving conversation"

    summary = (
        f"I found {len(items)} live items across {len(source_breakdown)} sources. "
        f"The strongest recurring themes are {theme_text}, with Bluesky providing social chatter, "
        f"Reddit adding community discussion, news outlets adding reporting, and Hacker News adding link-driven conversation."
    )

    suggested_queries = []
    for theme in themes[:3]:
        if topic.lower() == "live trends":
            suggested_queries.append(theme)
        else:
            suggested_queries.append(f"{topic} {theme}")

    return {
        "available": False,
        "headline": headline,
        "summary": summary,
        "sentiment": "mixed",
        "themes": themes[:4],
        "suggested_queries": suggested_queries[:3],
        "caveats": [
            "This is a local fallback digest built from the fetched items.",
            "Add OPENAI_API_KEY in .env for a richer AI synthesis.",
        ],
        "provider_status": "Fallback summary active",
    }


def extract_response_text(response):
    if response.get("output_text"):
        return response["output_text"]

    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("text"):
                parts.append(content["text"])

    return "\n".join(parts).strip()


def parse_model_json(raw_text):
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```$", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    return json.loads(cleaned)


def generate_ai_summary(topic, items):
    compact_items = [
        {
            "source": item["source"],
            "bucket": item["bucket"],
            "title": item["title"],
            "snippet": item["snippet"],
            "author": item["author"],
            "community": item["community"],
            "score": item["score"],
            "discussion_count": item["discussions"],
            "age": item["relative_time"],
        }
        for item in items[:10]
    ]

    prompt = f"""
You analyze live online discussion and reporting from Bluesky, Reddit, News sites, and Hacker News.
Return strict JSON only with these keys:
- headline: short sentence
- summary: 2-3 sentences
- sentiment: one of positive, negative, mixed, neutral
- themes: array of 3-5 short phrases
- suggested_queries: array of 2-4 follow-up search queries
- caveats: array of 1-2 short caveats

Rules:
- Use only the provided items.
- Do not invent facts.
- If the evidence is thin or skewed toward one source, say so in caveats.

Topic: {topic}
Items:
{json.dumps(compact_items, ensure_ascii=False)}
""".strip()

    response = post_json(
        "https://api.openai.com/v1/responses",
        {
            "model": OPENAI_MODEL,
            "input": prompt,
            "max_output_tokens": 500,
        },
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
    )

    parsed = parse_model_json(extract_response_text(response))
    return {
        "available": True,
        "headline": str(parsed.get("headline", "AI summary ready")),
        "summary": str(parsed.get("summary", "")),
        "sentiment": str(parsed.get("sentiment", "mixed")),
        "themes": [str(item) for item in parsed.get("themes", [])][:5],
        "suggested_queries": [str(item) for item in parsed.get("suggested_queries", [])][:4],
        "caveats": [str(item) for item in parsed.get("caveats", [])][:2],
        "provider_status": f"OpenAI summary via {OPENAI_MODEL}",
    }


def build_summary(topic, items):
    fallback = build_local_summary(topic, items)

    if not OPENAI_API_KEY:
        return fallback

    try:
        return generate_ai_summary(topic, items)
    except Exception as error:
        fallback["provider_status"] = f"Falling back after AI error: {error}"
        return fallback


def build_trending_payload():
    warnings = []

    bluesky_items = []
    reddit_items = []
    hacker_news_items = []

    try:
        bluesky_items = fetch_bluesky_hot(limit=4)
    except Exception as error:
        warnings.append(f"Bluesky unavailable: {error}")

    try:
        reddit_items = fetch_reddit_popular(limit=4)
    except Exception as error:
        warnings.append(f"Reddit unavailable: {error}")

    try:
        hacker_news_items = fetch_hn_top(limit=4)
    except Exception as error:
        warnings.append(f"Hacker News unavailable: {error}")

    items = dedupe_items(interleave_groups([bluesky_items, reddit_items, hacker_news_items], limit=10))
    summary = build_summary("Live trends", items)
    breakdown = Counter(item["source"] for item in items)
    top_topics = fetch_world_topics(limit=5) or extract_top_topics(items, max_topics=5)

    return {
        "topic": "Live trends",
        "ai_enabled": bool(OPENAI_API_KEY),
        "summary": summary,
        "items": items,
        "top_topics": top_topics,
        "source_breakdown": breakdown,
        "warnings": warnings,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_search_payload(query):
    warnings = []

    news_items = []
    bluesky_latest = []
    bluesky_top = []
    reddit_latest = []
    reddit_top = []
    hn_latest = []
    hn_top = []

    try:
        news_items = fetch_news_search(query, limit=5)
    except Exception as error:
        warnings.append(f"News search unavailable: {error}")

    try:
        bluesky_latest = fetch_bluesky_search(query, sort="latest", limit=5)
        bluesky_top = fetch_bluesky_search(query, sort="top", limit=4)
    except Exception as error:
        warnings.append(f"Bluesky search unavailable: {error}")

    try:
        reddit_latest = fetch_reddit_search(query, sort="new", limit=5)
        reddit_top = fetch_reddit_search(query, sort="top", limit=4)
    except Exception as error:
        warnings.append(f"Reddit search unavailable: {error}")

    try:
        hn_latest, hn_top = fetch_hn_matches(query, latest_limit=4, top_limit=3)
    except Exception as error:
        warnings.append(f"Hacker News search unavailable: {error}")

    items = dedupe_items(interleave_groups(
        [news_items, bluesky_latest, reddit_latest, hn_latest, bluesky_top, reddit_top, hn_top],
        limit=18,
    ))

    summary = build_summary(query, items)
    breakdown = Counter(item["source"] for item in items)
    top_topics = fetch_world_topics(limit=5) or extract_top_topics(items, max_topics=5)

    return {
        "topic": query,
        "ai_enabled": bool(OPENAI_API_KEY),
        "summary": summary,
        "items": items,
        "top_topics": top_topics,
        "source_breakdown": breakdown,
        "warnings": warnings,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/trending":
            payload = cached(("api_trending",), 120, build_trending_payload)
            return self.send_json(200, payload)

        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not query:
                return self.send_json(400, {"error": "Missing q query parameter"})

            payload = cached(("api_search", query.lower()), 120, lambda: build_search_payload(query))
            return self.send_json(200, payload)

        return super().do_GET()

    def send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Serving Get Latest at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
