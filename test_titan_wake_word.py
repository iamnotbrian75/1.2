import unittest

from dashboard.server import is_wake_word


class TitanWakeWordTests(unittest.TestCase):
    def test_requires_titan_as_a_recognized_word(self):
        for text in ("", "hello there", "tighten", "I heard a tone"):
            with self.subTest(text=text):
                self.assertFalse(is_wake_word(text))

    def test_matches_titan_with_case_and_punctuation(self):
        for text in ("titan", "Titan!", "Hey, Titan.", "I said Titan yesterday"):
            with self.subTest(text=text):
                self.assertTrue(is_wake_word(text))

    def test_does_not_match_titan_as_part_of_another_word(self):
        self.assertFalse(is_wake_word("titanic"))


if __name__ == "__main__":
    unittest.main()
