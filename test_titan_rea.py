import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_rea

CORE_SPEC = importlib.util.spec_from_file_location("titan_rea_test_core", ROOT / "T.v1.1.py")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)


class TitanReaTests(unittest.TestCase):
    def setUp(self):
        core._pending_website_analysis = False

    def test_page_url_allows_public_https_and_normalizes_default_port(self):
        url, origin = titan_rea._validate_page_url("https://8.8.8.8:443/docs")

        self.assertEqual(url, "https://8.8.8.8/docs")
        self.assertEqual(origin, "https://8.8.8.8")

    def test_page_url_allows_explicit_loopback_development_site(self):
        url, origin = titan_rea._validate_page_url("http://localhost:3000/app")

        self.assertEqual(url, "http://localhost:3000/app")
        self.assertEqual(origin, "http://localhost:3000")

    def test_page_url_rejects_unsupported_or_sensitive_urls(self):
        for value in (
            "file:///C:/private/index.html",
            "ftp://example.com/file",
            "https://user:password@example.com/",
            "https://example.com/?token=secret",
            "https://example.com/#account",
            "http://192.168.1.20/",
            "http://10.0.0.1/",
        ):
            with self.subTest(value=value), self.assertRaises(titan_rea.ReaInspectionError):
                titan_rea._validate_page_url(value)

    def test_page_url_rejects_domains_that_resolve_to_private_addresses(self):
        private_result = [
            (None, None, None, "", ("192.168.1.15", 80)),
        ]
        with patch.object(titan_rea.socket, "getaddrinfo", return_value=private_result):
            with self.assertRaises(titan_rea.ReaInspectionError):
                titan_rea._validate_page_url("http://dev.example.test/")

    def test_evidence_redacts_query_values_before_model_context(self):
        listing = {
            "targets": [
                {
                    "target_id": "target-1",
                    "type": "page",
                    "title": "Example",
                    "url": "https://example.com/",
                    "origin": "https://example.com",
                }
            ]
        }
        page = {
            "browser": {"product": "Chrome"},
            "target": listing["targets"][0],
            "dom": {"total_nodes": 1, "nodes": [{"node_name": "HTML"}]},
            "scripts": {
                "total": 1,
                "items": [{"url": "https://example.com/app.js?token=secret", "type": "script"}],
            },
            "network": {
                "prior_activity_available": False,
                "requests": [
                    {
                        "url": "https://user:password@example.com/api?token=secret",
                        "method": "GET",
                        "status": 200,
                        "mime_type": "application/json",
                        "resource_type": "XHR",
                    }
                ]
            },
            "completeness": {"status": "complete"},
            "limitations": [],
        }

        output = titan_rea._public_evidence(listing, {"normalized_result": page})

        serialized = str(output)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("password", serialized)
        self.assertIn("https://example.com/app.js", serialized)
        self.assertIn("https://example.com/api", serialized)
        self.assertIn("prior_activity_available", serialized)
        self.assertLessEqual(len(json.dumps(output)), titan_rea.MAX_INSPECTION_CHARS)
        self.assertEqual(
            titan_rea._clean_url("******example.com/api?token=secret#account"),
            "******example.com/api",
        )

    def test_explicit_request_uses_local_model_only_to_summarize_rea_evidence(self):
        with patch.object(core.rea, "inspect_website", return_value='{"page":{"title":"Example"}}') as inspect:
            with patch.object(core.persona, "llm_chat", return_value=("The page has one title.", "ok")) as chat:
                reply = core.run_rea_website("https://example.com/")

        inspect.assert_called_once_with("https://example.com/")
        self.assertIn("untrusted data", chat.call_args.args[0])
        self.assertEqual(chat.call_args.kwargs["timeout"], 240)
        self.assertEqual(reply, "The page has one title.")

    def test_router_requires_an_explicit_rea_inspection_request(self):
        self.assertEqual(
            core.match_local("Use REA to reverse engineer this webpage https://8.8.8.8/docs"),
            ("rea_website", "https://8.8.8.8/docs"),
        )
        self.assertEqual(
            core.match_local("I'd like to reverse engineer a website"),
            ("rea_website", ""),
        )
        self.assertEqual(
            core.match_local("Could you help me analyze this site?"),
            ("rea_website", ""),
        )
        self.assertEqual(
            core.match_local("Reverse engineer Instagram"),
            ("rea_website", "https://www.instagram.com"),
        )
        self.assertIsNone(core.match_local("Tell me about https://8.8.8.8/docs"))

    def test_missing_analysis_target_is_saved_and_site_name_answers_follow_up(self):
        with patch.object(core.rea, "_validate_page_url", return_value=("https://www.instagram.com/", "https://www.instagram.com")) as validate:
            with patch.object(core.rea, "inspect_website", return_value='{"page":{"title":"Instagram"}}') as inspect:
                with patch.object(core.persona, "llm_chat", return_value=("REA found an Instagram page.", "ok")):
                    question, _ = core.titan_handle("I'd like to reverse engineer a website")
                    reply, _ = core.titan_handle("Instagram")

        self.assertIn("Which website", question)
        validate.assert_called_once_with("https://www.instagram.com")
        inspect.assert_called_once_with("https://www.instagram.com/")
        self.assertEqual(reply, "REA found an Instagram page.")
        self.assertFalse(core._pending_website_analysis)

    def test_pending_analysis_can_be_cancelled_without_opening_site(self):
        core._pending_website_analysis = True

        reply, _ = core.titan_handle("cancel")

        self.assertIn("cancelled", reply)
        self.assertFalse(core._pending_website_analysis)

    def test_explicit_open_changes_task_instead_of_becoming_analysis(self):
        core._pending_website_analysis = True
        with patch.object(core.skills, "run_skill", return_value="Opening Instagram.") as open_site:
            reply, _ = core.titan_handle("open instagram")

        open_site.assert_called_once_with("open", "open instagram")
        self.assertEqual(reply, "Opening Instagram.")
        self.assertFalse(core._pending_website_analysis)

    def test_invalid_pending_target_keeps_analysis_waiting_for_a_valid_site(self):
        core._pending_website_analysis = True
        with patch.object(
            core.rea,
            "_validate_page_url",
            side_effect=titan_rea.ReaInspectionError("private hosts are not allowed"),
        ):
            with patch.object(core.rea, "inspect_website") as inspect:
                reply, _ = core.titan_handle("http://192.168.1.20/")

        self.assertIn("private hosts are not allowed", reply)
        self.assertTrue(core._pending_website_analysis)
        inspect.assert_not_called()

    def test_model_router_accepts_the_explicit_rea_command(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"message": {"content": json.dumps({"command": "rea_website", "argument": ""})}}
        ).encode("utf-8")
        with patch.object(core.urllib.request, "urlopen", return_value=response):
            routed, kind = core.ask_ollama("I want help understanding this site's structure")

        self.assertEqual((routed, kind), (("rea_website", ""), "ok"))


if __name__ == "__main__":
    unittest.main()
