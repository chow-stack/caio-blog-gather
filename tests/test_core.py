import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest.config import load_config
from digest.email import render_html
from digest.feeds import is_junk
from digest.generator import call_model, validate_digest
from digest.state import load_seen, save_seen


ROOT = Path(__file__).parents[1]


class ConfigTests(unittest.TestCase):
    def test_project_config_loads_enabled_sources(self):
        config = load_config(ROOT / "config.toml")
        self.assertTrue(config["sources"])
        self.assertTrue(all(source.get("enabled", True) for source in config["sources"]))

    def test_feed_links_must_be_web_urls(self):
        self.assertTrue(is_junk("Bad link", "javascript:alert(1)"))
        self.assertFalse(is_junk("Good article", "https://example.com/article"))


class StateTests(unittest.TestCase):
    def test_legacy_state_migrates_and_old_entries_are_pruned(self):
        now = datetime(2026, 9, 8, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text(json.dumps({"urls": ["https://example.com/legacy"]}))
            seen = load_seen(path, now)
            seen["https://example.com/old"] = (now - timedelta(days=90)).isoformat()
            save_seen(path, seen, ["https://example.com/new"], now, retention_days=60)
            saved = json.loads(path.read_text())["seen"]
            self.assertIn("https://example.com/legacy", saved)
            self.assertIn("https://example.com/new", saved)
            self.assertNotIn("https://example.com/old", saved)


class EmailTests(unittest.TestCase):
    def test_template_escapes_generated_content(self):
        config = load_config(ROOT / "config.toml")
        digest = {
            "tldr": ["<script>bad()</script>"],
            "top_stories": [],
            "important_developments": [],
            "builder_tools": [],
            "market_signals": [],
            "other_notable": [],
        }
        html = render_html(
            ROOT / "templates" / "email.html",
            digest,
            {
                "digest_name": config["digest"]["name"],
                "tagline": config["digest"]["tagline"],
                "date_label": "Tuesday, September 8",
                "preheader": digest["tldr"][0],
                "theme": config["theme"],
                "sections": config["sections"],
                "section_order": ["top_stories", "important_developments", "builder_tools", "market_signals", "other_notable"],
                "article_total": 0,
                "candidate_total": 0,
                "show_candidate_count": False,
                "show_model": True,
                "models_used": ["gemini-test-model"],
            },
        )
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>bad()", html)
        self.assertIn("Model: gemini-test-model", html)


class DigestValidationTests(unittest.TestCase):
    def test_unknown_links_are_removed_and_missing_articles_restored(self):
        scraped = [
            {
                "title": "Trusted title",
                "url": "https://example.com/trusted",
                "source": "Trusted source",
            }
        ]
        digest = {
            "top_stories": [
                {
                    "title": "Unsafe",
                    "url": "javascript:alert(1)",
                    "source": "Fake",
                    "what": "Unsafe",
                }
            ]
        }
        validated, missing = validate_digest(digest, scraped, max_top_stories=4)
        self.assertEqual(missing, 1)
        self.assertEqual(validated["top_stories"], [])
        self.assertEqual(validated["other_notable"][0]["url"], scraped[0]["url"])


class ModelTests(unittest.TestCase):
    def test_model_calls_use_chat_api(self):
        class Response:
            text = '{"ok": true}'

        class Chat:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)
                return Response()

        class Chats:
            def __init__(self):
                self.created = []

            def create(self, **kwargs):
                chat = Chat()
                self.created.append((kwargs, chat))
                return chat

        class Client:
            def __init__(self):
                self.chats = Chats()

        client = Client()
        text, model = call_model(client, "Return JSON", ["gemini-test-model"])

        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(model, "gemini-test-model")
        create_args, chat = client.chats.created[0]
        self.assertEqual(create_args["model"], "gemini-test-model")
        self.assertEqual(create_args["config"].response_mime_type, "application/json")
        self.assertEqual(chat.messages, ["Return JSON"])


if __name__ == "__main__":
    unittest.main()
