import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_builder as builder
import titan_garage as garage

GOOD_DRAFT = {
    "title": "Boost gauge install", "summary": "Fit a 52 mm boost gauge.", "difficulty": "medium", "time": "2 hours",
    "parts": [
        {"name": "52 mm boost gauge", "kind": "gauge", "qty": 1, "price_gbp": 35},
        {"name": "Vacuum hose", "kind": "hose", "qty": 2, "price_gbp": 6},
        {"name": "T-piece", "kind": "connector", "price_gbp": 4},
        {"name": "Fused add-a-circuit", "kind": "fuse", "price_gbp": 0},
        {"name": "Mystery", "kind": "flux-capacitor"},
    ],
    "tools": ["Trim tools", "Multimeter"],
    "connections": [
        {"from": 1, "to": 2, "how": "pipe", "label": "boost"},
        {"from": 2, "to": 3, "how": "pipe"},
        {"from": 3, "to": 2, "how": "pipe"},          # duplicate in reverse
        {"from": 1, "to": 9, "how": "wire"},          # unknown part
        {"from": 4, "to": 1, "how": "teleport"},      # unknown kind of link
    ],
    "steps": [
        {"title": "Disconnect the battery", "text": "Disconnect the negative terminal.", "parts": [4], "warning": "Airbags"},
        {"title": "Run the hose", "text": "Tee the vacuum hose into the boost line.", "parts": [2, 3, 3, 42]},
        {"title": "Fit the gauge", "text": "Mount the 52 mm boost gauge in a pod.", "parts": []},
    ],
    "safety": ["Disconnect the battery first."],
}


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        builder._reset_for_tests(Path(self.tmp.name) / "guides.json")
        garage._reset_for_tests(Path(self.tmp.name) / "garage.json")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- the checked guides that ship with Titan ----
    def test_builtin_guides_are_valid_and_linked(self):
        items = {i["id"] for i in garage.project("e87")["items"]}
        checked = [g for g in builder.list_guides() if g["source"] == "checked"]
        self.assertGreaterEqual(len(checked), 2)
        for meta in checked:
            guide = builder.get_guide(meta["id"])
            ids = [p["id"] for p in guide["parts"]]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate part ids in {meta['id']}")
            spots = [tuple(p["at"]) for p in guide["parts"]]
            self.assertEqual(len(spots), len(set(spots)), f"two parts share a grid spot in {meta['id']}")
            for part in guide["parts"]:
                self.assertIn(part["kind"], builder.KINDS, part["id"])
                if "item" in part:
                    self.assertIn(part["item"], items, f"{meta['id']}:{part['id']} links to unknown item")
            for link in guide["links"]:
                self.assertIn(link["a"], ids); self.assertIn(link["b"], ids)
                self.assertIn(link["how"], builder.LINKS)
            for step in guide["steps"]:
                self.assertTrue(step["parts"] and set(step["parts"]) <= set(ids), step["title"])
                self.assertIn(step["who"], builder.WHO)

    def test_guide_ids_are_safe(self):
        self.assertIsNone(builder.get_guide("../titan_profile"))
        self.assertIsNone(builder.get_guide("E87 FRONT"))
        self.assertIsNone(builder.get_guide("no-such-guide"))

    # ---- asking ----
    def test_how_to_questions_are_recognised(self):
        cases = {
            "how do I wire a boost gauge to my car?": "wire a boost gauge to my car",
            "Titan, show me how to fit a head unit": "fit a head unit",
            "walk me through replacing the radiator": "replacing the radiator",
            "how can I build an arduino weather station": "build an arduino weather station",
            "draw me a blueprint for a raspberry pi car dashboard": "raspberry pi car dashboard",
            "help me install a dash cam": "install a dash cam",
        }
        for question, task in cases.items():
            self.assertEqual(builder.match_request(question), task, question)
        for other in ("how are you", "how do I feel about this", "what's the weather", "show me the news"):
            self.assertIsNone(builder.match_request(other), other)

    # ---- cleaning what the model returns ----
    def test_draft_is_cleaned(self):
        guide = builder.clean_draft(GOOD_DRAFT, "fit a boost gauge")
        self.assertEqual([p["id"] for p in guide["parts"]], ["p1", "p2", "p3", "p4", "p5"])
        self.assertEqual(guide["parts"][4]["kind"], "box")           # unknown kind -> plain box
        self.assertIsNone(guide["parts"][3]["price"])                # £0 means unknown
        self.assertEqual(guide["parts"][1]["qty"], 2)
        self.assertEqual([(l["a"], l["b"], l["how"]) for l in guide["links"]],
                         [("p1", "p2", "pipe"), ("p2", "p3", "pipe"), ("p4", "p1", "fits")])
        self.assertEqual(guide["steps"][0]["warn"], "Airbags")
        self.assertEqual(guide["steps"][1]["parts"], ["p2", "p3"])  # deduped, unknown dropped
        self.assertEqual(guide["steps"][2]["parts"], ["p1"])        # found by name in the text
        self.assertIn("AI draft", guide["disclaimer"])

    def test_dangerous_steps_are_flagged(self):
        raw = dict(GOOD_DRAFT, steps=[
            {"title": "Run the hose", "text": "Attach the hose adapter to the fuel rail.", "parts": [2]},
            {"title": "Wire it", "text": "Connect the gauge light to the dimmer wire.", "parts": [1, 2, 3, 4, 5]},
            {"title": "Tidy", "text": "Clip the cable away from the curtain airbag.", "parts": [4]},
        ])
        guide = builder.clean_draft(raw, "x")
        self.assertIn("fuel system", guide["steps"][0]["danger"])
        self.assertNotIn("danger", guide["steps"][1])
        self.assertIn("airbag", guide["steps"][2]["danger"])
        self.assertEqual(guide["flagged"], 2)
        self.assertLessEqual(len(guide["steps"][1]["parts"]), 3)     # "every part" lists are trimmed
        self.assertIsNone(builder.danger_of("swap the brake light bulb"))
        self.assertIsNotNone(builder.danger_of("bleed the brakes"))

    def test_drafting_falls_back_to_the_chat_model(self):
        calls = []

        class Res:
            def read(self): return builder.json.dumps({"message": {"content": builder.json.dumps(GOOD_DRAFT)}}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout):
            model = builder.json.loads(req.data)["model"]
            calls.append(model)
            if model == builder.MODEL:
                raise builder.urllib.error.HTTPError(req.full_url, 404, "model not found", {}, None)
            return Res()

        with mock.patch.object(builder, "MODEL", "llama3.2:3b"), mock.patch.object(builder, "CHAT_MODEL", "qwen2.5-coder:3b"), \
             mock.patch.object(builder.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(builder._ask_model("build a shelf")["title"], "Boost gauge install")
        self.assertEqual(calls, ["llama3.2:3b", "qwen2.5-coder:3b"])

    def test_unusable_drafts_are_rejected(self):
        for raw in (None, [], {"parts": [{"name": "x", "kind": "box"}], "steps": [{"title": "a", "text": "b", "parts": [1]}]},
                    {"parts": GOOD_DRAFT["parts"], "steps": []}):
            with self.assertRaises(ValueError):
                builder.clean_draft(raw, "x")

    # ---- drafting end to end (model mocked) ----
    def test_handle_drafts_in_background_and_opens_builder(self):
        with mock.patch.object(builder, "_ask_model", return_value=GOOD_DRAFT), \
             mock.patch.object(builder.skills, "push_alert"):
            reply = builder.handle("how do I fit a boost gauge")
            self.assertIn("Drafting a blueprint for fit a boost gauge", reply)
            ui = builder.take_ui()
            self.assertEqual(ui["open"], "builder")
            job = builder._jobs.get(ui["guide"])
            if job:
                job.join(5)
        guide = builder.get_guide(ui["guide"])
        self.assertEqual(guide["state"], "ready")
        self.assertEqual(guide["source"], "ai")
        self.assertEqual(guide["title"], "Boost gauge install")
        self.assertEqual(builder.list_guides()[-1]["id"], ui["guide"])
        # asking again opens the same blueprint instead of drafting another
        self.assertIn("again", builder.handle("how do I fit a boost gauge?"))
        self.assertEqual(builder.take_ui()["guide"], ui["guide"])
        self.assertTrue(builder.delete_guide(ui["guide"]))
        self.assertIsNone(builder.get_guide(ui["guide"]))

    def test_model_failure_is_reported(self):
        with mock.patch.object(builder, "_ask_model", side_effect=builder.urllib.error.URLError("down")):
            gid = builder.start_draft("wire a relay", wait=True)
        guide = builder.get_guide(gid)
        self.assertEqual(guide["state"], "failed")
        self.assertIn("Ollama", guide["error"])
        with mock.patch.object(builder, "_ask_model", return_value={"nonsense": True}):
            gid = builder.start_draft("wire a horn", wait=True)
        self.assertIn("didn't make sense", builder.get_guide(gid)["error"])

    def test_drafts_interrupted_by_a_restart_are_marked_failed(self):
        with mock.patch.object(builder, "_draft"):
            gid = builder.start_draft("build a shelf")
        builder._reset_for_tests(builder.STORE_FILE)
        self.assertEqual(builder.get_guide(gid)["state"], "failed")

    def test_only_recent_drafts_are_kept(self):
        with mock.patch.object(builder, "_draft"):
            ids = [builder.start_draft(f"build thing number {n}") for n in range(builder.MAX_STORED + 3)]
        kept = [g["id"] for g in builder.list_guides() if g["source"] == "ai"]
        self.assertEqual(len(kept), builder.MAX_STORED)
        self.assertNotIn(ids[0], kept)

    def test_printable_parts_open_as_guides(self):
        garage.handle("design a 52mm gauge pod")
        gid = garage.take_ui()["guide"]
        guide = builder.get_guide(gid)
        self.assertTrue(guide["printable"])
        self.assertEqual(guide["source"], "printable")
        self.assertTrue(all(p["kind"] == "printed" and "shape" in p for p in guide["parts"]))
        self.assertIn(gid, [g["id"] for g in builder.list_guides()])

    def test_car_questions_get_car_context(self):
        seen = {}

        class Res:
            def __init__(self, body): self.body = body
            def read(self): return self.body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout):
            seen["prompt"] = builder.json.loads(req.data)["messages"][0]["content"]
            seen["format"] = builder.json.loads(req.data)["format"]
            return Res(builder.json.dumps({"message": {"content": builder.json.dumps(GOOD_DRAFT)}}).encode())

        with mock.patch.object(builder.urllib.request, "urlopen", fake_urlopen):
            builder._ask_model("fit a boost gauge to my bmw")
            self.assertIn("E87", seen["prompt"])
            self.assertEqual(seen["format"]["required"], builder.SCHEMA["required"])
            builder._ask_model("build a bird box")
            self.assertNotIn("E87", seen["prompt"])

    def test_open_builder_command(self):
        self.assertIn("builder", builder.handle("open the builder").lower())
        self.assertEqual(builder.take_ui(), {"open": "builder"})
        self.assertIsNone(builder.handle("what's the time"))


if __name__ == "__main__":
    unittest.main()
