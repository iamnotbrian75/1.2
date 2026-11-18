import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_knowledge
import titan_persona


class TitanKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.brain_dir = Path(self.temporary_directory.name)
        (self.brain_dir / "Knowledge").mkdir()
        (self.brain_dir / "Projects").mkdir()
        self.original_brain_dir = titan_persona.BRAIN_DIR
        self.original_user = titan_persona.get_user()
        titan_persona.BRAIN_DIR = self.brain_dir
        titan_persona.set_user("Brian")

    def tearDown(self):
        titan_persona.set_user(self.original_user)
        titan_persona.BRAIN_DIR = self.original_brain_dir
        self.temporary_directory.cleanup()

    def test_search_ranks_matching_notes_and_includes_source(self):
        (self.brain_dir / "Knowledge" / "Python.md").write_text(
            "# Python Virtual Environments\n"
            "A virtual environment isolates Python packages between projects.\n"
            "Create one with `python -m venv .venv`.\n",
            encoding="utf-8",
        )
        (self.brain_dir / "Projects" / "Titan AI.md").write_text(
            "# Titan AI\nThe project retrieves relevant Python virtual environment notes.\n",
            encoding="utf-8",
        )

        result = titan_knowledge.search_notes(
            "Explain Python virtual environments", self.brain_dir
        )

        self.assertIn("[Knowledge/Python.md]", result)
        self.assertIn("python -m venv .venv", result)
        self.assertIn("isolates Python packages between projects", result)
        self.assertLess(result.index("[Knowledge/Python.md]"), result.index("[Projects/Titan AI.md]"))

    def test_search_preserves_projects_note_but_skips_personal_root_notes(self):
        (self.brain_dir / "Projects.md").write_text(
            "# Project history\nTitan was built in Python.\n", encoding="utf-8"
        )
        (self.brain_dir / "Brian.md").write_text(
            "# Personal\nPython is Brian's favourite language.\n", encoding="utf-8"
        )

        result = titan_knowledge.search_notes("Python", self.brain_dir)

        self.assertIn("[Projects.md]", result)
        self.assertNotIn("Brian.md", result)

    def test_search_ignores_sections_matching_only_one_word_of_a_specific_query(self):
        (self.brain_dir / "Knowledge" / "Python.md").write_text(
            "# Virtual environments\nPython packages are isolated per project.\n"
            "## Related\n- [[Python]]\n",
            encoding="utf-8",
        )
        (self.brain_dir / "Knowledge" / "AI.md").write_text(
            "# Related\n- [[Python]]\n",
            encoding="utf-8",
        )

        result = titan_knowledge.search_notes(
            "Explain Python virtual environments", self.brain_dir
        )

        self.assertIn("[Knowledge/Python.md]", result)
        self.assertNotIn("[Knowledge/AI.md]", result)
        self.assertNotIn("## Related", result)

    def test_result_respects_note_and_character_limits(self):
        for name in ("One", "Two", "Three"):
            (self.brain_dir / "Knowledge" / f"{name}.md").write_text(
                f"# Python {name}\nPython packages and virtual environments.\n",
                encoding="utf-8",
            )

        result = titan_knowledge.search_notes(
            "Python virtual environments", self.brain_dir, limit=2, max_chars=180
        )

        self.assertLessEqual(len(result), 180)
        self.assertEqual(result.count("[Knowledge/"), 2)

        truncated = titan_knowledge.search_notes(
            "Python virtual environments", self.brain_dir, max_chars=100
        )
        self.assertLessEqual(len(truncated), 100)

    def test_system_prompt_includes_relevant_notes_only_for_a_user_request(self):
        (self.brain_dir / "Knowledge" / "Python.md").write_text(
            "# Python environments\nUse `python -m venv .venv` to create an environment.\n",
            encoding="utf-8",
        )

        prompt = titan_persona.system_prompt("How do Python virtual environments work?")
        unrelated_prompt = titan_persona.system_prompt("What is the weather?")

        self.assertIn("[Knowledge/Python.md]", prompt)
        self.assertNotIn("[Knowledge/Python.md]", unrelated_prompt)

    def test_invalid_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            titan_knowledge.search_notes("Python", self.brain_dir, limit=-1)
        with self.assertRaises(ValueError):
            titan_knowledge.search_notes("Python", self.brain_dir, max_chars=-1)


if __name__ == "__main__":
    unittest.main()
