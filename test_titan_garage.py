import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_garage as garage

SHAPES = {"box", "cylinder", "sphere", "tube", "plate"}


class GarageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        garage._reset_for_tests(Path(self.tmp.name) / "garage.json")
        garage.take_ui()

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_models_are_valid_and_linked(self):
        items = {i["id"] for i in garage.project("e87")["items"]}
        models = [m for m in garage.list_models() if m["kind"] != "custom"]
        self.assertGreaterEqual(len(models), 2)
        for meta in models:
            spec = garage.get_model(meta["id"])
            ids = {p["id"] for p in spec["parts"]}
            self.assertEqual(len(ids), len(spec["parts"]), f"duplicate part ids in {meta['id']}")
            for part in spec["parts"]:
                self.assertIn(part["shape"]["type"], SHAPES, part["id"])
                if "item" in part:
                    self.assertIn(part["item"], items, f"{meta['id']}:{part['id']} links to unknown item")
            flows = {f["id"] for f in spec.get("flows", [])}
            for flow in spec.get("flows", []):
                self.assertGreaterEqual(len(flow["path"]), 2)
                self.assertTrue(all(len(p) == 3 for p in flow["path"]))
            for step in spec["steps"]:
                self.assertTrue(set(step["parts"]) <= ids, f"{meta['id']} step '{step['title']}' names unknown parts")
                self.assertTrue(set(step.get("off", [])) <= ids, f"{meta['id']} step '{step['title']}' removes unknown parts")
                self.assertTrue(set(step.get("flows", [])) <= flows, f"{meta['id']} step '{step['title']}' names unknown flows")
                self.assertIn(step["who"], {"you", "mechanic"})

    def test_asset_names_and_uploads_are_validated(self):
        garage.ASSETS_DIR = Path(self.tmp.name) / "assets"
        self.assertIsNone(garage.safe_asset_name("../../titan_profile.json"))
        self.assertIsNone(garage.safe_asset_name("model.exe"))
        self.assertEqual(garage.safe_asset_name(r"C:\x\BMW E87 (front).glb"), "BMW E87 (front).glb")
        self.assertIsNotNone(garage.save_asset("fake.glb", b"not a glb")[1])
        self.assertIsNotNone(garage.save_asset("big.obj", b"")[1])
        name, error = garage.save_asset("e87.glb", b"glTF" + b"\0" * 64)
        self.assertEqual((name, error), ("e87.glb", None))
        self.assertEqual([a["name"] for a in garage.list_assets()], ["e87.glb"])
        self.assertIsNone(garage.asset_path("missing.glb"))

    def test_override_attaches_asset_and_keeps_only_real_parts(self):
        garage.ASSETS_DIR = Path(self.tmp.name) / "assets"
        garage.save_asset("e87.glb", b"glTF" + b"\0" * 64)
        self.assertIsNotNone(garage.set_override("e87-front-end", {"asset": "nope.glb"}))
        self.assertIsNone(garage.set_override("e87-front-end", {
            "asset": "e87.glb", "mesh_map": {"0:Bumper": "bumper", "1:Thing": "not_a_part"},
            "rotate": [0, 90, 0], "scale": 1.5}))
        spec = garage.get_model("e87-front-end")
        self.assertEqual(spec["asset"], "e87.glb")
        self.assertEqual(spec["mesh_map"], {"0:Bumper": "bumper"})
        self.assertEqual((spec["asset_rotate"], spec["asset_scale"]), ([0, 90, 0], 1.5))
        self.assertIsNotNone(garage.set_override("e87-front-end", {"rotate": [0, 0, 999]}))
        garage.set_override("e87-front-end", {"asset": None})
        self.assertNotIn("asset", garage.get_model("e87-front-end"))

    def test_model_ids_are_safe(self):
        self.assertIsNone(garage.get_model("../titan_profile"))
        self.assertIsNone(garage.get_model("E87 FRONT"))

    def test_status_queries_and_updates(self):
        self.assertIn("1M-style front bumper", garage.handle("what do i own"))
        self.assertIn("Satin gunmetal", garage.handle("what am i considering"))
        self.assertIn("Radiator", garage.handle("what needs checking"))
        self.assertIn("Crumpled silver section", garage.handle("what needs checking"))
        self.assertIn("considering", garage.handle("mark the bumper as considering").lower())
        garage.handle("mark the radiator as checked")
        radiator = next(i for i in garage.project()["items"] if i["id"] == "radiator")
        self.assertEqual(radiator["status"], "fitted")

    def test_add_item_and_price(self):
        reply = garage.handle("add satin gunmetal wrap film to my garage as considering for £120")
        self.assertIn("£120", reply)
        garage.handle("the head unit costs £185")
        unit = next(i for i in garage.project()["items"] if i["id"] == "head_unit")
        self.assertEqual(unit["price"], 185.0)
        self.assertIn("£", garage.handle("garage budget"))

    def test_next_moves_in_order(self):
        reply = garage.handle("what are my next moves")
        self.assertLess(reply.index("1."), reply.index("2."))
        self.assertIn("VIN", reply)
        self.assertIn("mechanic", reply)

    def test_open_garage_sets_ui_once(self):
        garage.handle("open the garage")
        self.assertEqual(garage.take_ui(), {"open": "garage", "model": "e87-front-end"})
        self.assertEqual(garage.take_ui(), {})

    def test_design_custom_parts_are_printable(self):
        reply = garage.handle("design a 60mm gauge pod")
        self.assertIn("60 mm gauge pod", reply)
        ui = garage.take_ui()
        spec = garage.get_model(ui["model"])
        self.assertTrue(spec["printable"])
        cup = next(p for p in spec["parts"] if p["id"] == "cup")
        self.assertAlmostEqual(cup["shape"]["r_in"], 30.3)
        garage.handle("design an enclosure 100 by 70 by 35")
        enclosure = garage.get_model(garage.take_ui()["model"])
        lid = next(p for p in enclosure["parts"] if p["id"] == "lid")
        self.assertEqual(len(lid["shape"]["holes"]), 4)
        self.assertEqual((lid["shape"]["w"], lid["shape"]["h"]), (100.0, 70.0))

    def test_other_text_is_ignored(self):
        self.assertIsNone(garage.handle("what's the weather"))
        self.assertIsNone(garage.handle("tell me a joke"))

    def test_data_saved_to_disk(self):
        garage.handle("mark the intake as fitted")
        saved = json.loads(garage.STORE_FILE.read_text(encoding="utf-8"))
        intake = next(i for i in saved["projects"]["e87"]["items"] if i["id"] == "intake")
        self.assertEqual(intake["status"], "fitted")


if __name__ == "__main__":
    unittest.main()
