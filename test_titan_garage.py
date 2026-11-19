import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_garage as garage


class GarageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        garage._reset_for_tests(Path(self.tmp.name) / "garage.json")
        garage.take_ui()

    def tearDown(self):
        self.tmp.cleanup()

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

    def test_open_garage_opens_the_front_end_blueprint_once(self):
        garage.handle("open the garage")
        self.assertEqual(garage.take_ui(), {"open": "builder", "guide": "e87-front-end"})
        self.assertEqual(garage.take_ui(), {})
        garage.handle("show me the head unit")
        self.assertEqual(garage.take_ui(), {"open": "builder", "guide": "e87-titandrive"})

    def test_design_custom_parts_are_printable(self):
        reply = garage.handle("design a 60mm gauge pod")
        self.assertIn("60 mm gauge pod", reply)
        ui = garage.take_ui()
        spec = garage.custom_models()[ui["guide"]]
        self.assertTrue(spec["printable"])
        cup = next(p for p in spec["parts"] if p["id"] == "cup")
        self.assertAlmostEqual(cup["shape"]["r_in"], 30.3)
        garage.handle("design an enclosure 100 by 70 by 35")
        enclosure = garage.custom_models()[garage.take_ui()["guide"]]
        lid = next(p for p in enclosure["parts"] if p["id"] == "lid")
        self.assertEqual(len(lid["shape"]["holes"]), 4)
        self.assertEqual((lid["shape"]["w"], lid["shape"]["h"]), (100.0, 70.0))

    def test_how_to_questions_are_left_for_the_builder(self):
        self.assertIsNone(garage.handle("how do i make a box for my arduino"))

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
