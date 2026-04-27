VAULT_PATH = "../caio-os"

RSS_SOURCES = [
    "https://hnrss.org/newest?q=AI+model+OR+AI+tool+OR+LLM&points=20",
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_claude.xml",
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_research.xml",
    "https://simonwillison.net/atom/everything/",
]

SOURCES = [
    "https://news.smol.ai",
    "https://aibreakfast.beehiiv.com/",
    "https://techcrunch.com/category/artificial-intelligence/",
]

# link_type: "internal" = same-domain links, "external" = off-site links
# url_contains / url_pattern: filter which links to keep
SOURCE_CONFIG = {
    "news.smol.ai":            {"link_type": "internal", "url_pattern": r"/issues/\d{2}-"},
    "aibreakfast.beehiiv.com": {"link_type": "internal", "url_contains": "/p/"},
    "techcrunch.com":          {"link_type": "internal", "url_pattern": r"/\d{4}/\d{2}/"},
}
