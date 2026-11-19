import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_screen as screen


class ScreenTests(unittest.TestCase):
    def test_requests_are_recognised(self):
        cases = {
            "where's the export button?": "the export button",
            "Titan, point to the wifi settings": "the wifi settings",
            "show me where the layers panel is": "the layers panel",
            "where do I click to add a layer": "add a layer",
            "where is the save icon on my screen": "the save icon",
            "find the bluetooth toggle on the screen": "the bluetooth toggle",
        }
        for text, target in cases.items():
            self.assertEqual(screen.match_request(text), target, text)
        for other in ("where is the nearest garage", "what's the weather", "how do I wire a boost gauge",
                      "where are my keys", "show me the news"):
            self.assertIsNone(screen.match_request(other), other)

    def test_answers_are_parsed(self):
        text, point = screen.parse_answer("Click File at the top left, then Export.\nPOINT: 41, 22")
        self.assertEqual(text, "Click File at the top left, then Export.")
        self.assertEqual(point, (41.0, 22.0))
        self.assertEqual(screen.parse_answer("It's the gear. POINT: (980, 15)")[1], (980.0, 15.0))
        self.assertEqual(screen.parse_answer('Here. {"bbox_2d": [100, 200, 300, 400]}')[1], (200.0, 300.0))
        text, point = screen.parse_answer("I can't see it; try the menu.\nPOINT: none")
        self.assertIsNone(point)
        self.assertIn("menu", text)
        self.assertIsNone(screen.parse_answer("POINT: 1500, 20")[1])     # off the grid
        self.assertEqual(screen.parse_answer("POINT: 500, 500")[0], "Here it is.")

    def test_grid_maps_to_pixels(self):
        self.assertEqual(screen.to_screen((0, 0), 1920, 1080), (0, 0))
        self.assertEqual(screen.to_screen((1000, 1000), 1920, 1080), (1919, 1079))
        self.assertEqual(screen.to_screen((500, 500), 1920, 1080), (960, 540))

    def test_look_and_point_end_to_end(self):
        said = []
        with mock.patch.object(screen, "wait_for_other_app"), \
             mock.patch.object(screen, "capture", return_value=("abc", (1920, 1080))), \
             mock.patch.object(screen, "ask_vision", return_value="Top right, the gear icon.\nPOINT: 975, 30"), \
             mock.patch.object(screen, "show_pointer") as pointer:
            result = screen.look_and_point("where's the settings icon?", announce=said.append)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pixel"], (1871, 32))
        pointer.assert_called_once_with(1871, 32, "Top right, the gear icon.")
        self.assertEqual(said, ["Top right, the gear icon."])

    def test_missing_vision_model_is_explained(self):
        said = []
        err = screen.urllib.error.HTTPError("u", 404, "not found", {}, None)
        with mock.patch.object(screen, "wait_for_other_app"), \
             mock.patch.object(screen, "capture", return_value=("abc", (100, 100))), \
             mock.patch.object(screen, "ask_vision", side_effect=err), \
             mock.patch.object(screen, "show_pointer") as pointer:
            result = screen.look_and_point("where's x?", announce=said.append)
        self.assertFalse(result["ok"])
        self.assertIn("ollama pull", said[0])
        pointer.assert_not_called()

    def test_handle_runs_in_background_and_refuses_overlap(self):
        with mock.patch.object(screen.threading, "Thread") as thread:
            reply = screen.handle("where's the export button?")
        self.assertIn("the export button", reply)
        thread.return_value.start.assert_called_once()
        self.assertIsNone(screen.handle("tell me a joke"))
        screen._busy.acquire()
        try:
            self.assertIn("still looking", screen.handle("point to the bin"))
        finally:
            screen._busy.release()


if __name__ == "__main__":
    unittest.main()
