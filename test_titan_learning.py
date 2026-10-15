import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_persona as persona

CORE_SPEC = importlib.util.spec_from_file_location("titan_learning_test_core", ROOT / "T.v1.1.py")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)


class TitanLearningTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_brain_dir = persona.BRAIN_DIR
        persona.set_user(None)
        persona.BRAIN_DIR = Path(self.temporary_directory.name)
        persona.set_user("Brian")

    def tearDown(self):
        persona.set_user(None)
        persona.BRAIN_DIR = self.original_brain_dir
        self.temporary_directory.cleanup()

    def test_normal_conversation_is_not_persisted(self):
        persona.remember("What is a Python function?", "A reusable block of code.")
        self.assertEqual(persona.learning_records(), [])

    def test_explicit_correction_is_saved_and_used_only_when_relevant(self):
        persona.remember("Write a short Python example.", "An overly long example.")
        reply, keep_running = core.titan_handle(
            "that was wrong because keep Python examples to one small runnable snippet"
        )
        self.assertTrue(keep_running)
        self.assertIn("doesn't change my model weights", reply)
        record = persona.learning_records()[0]
        self.assertEqual(record["request"], "Write a short Python example.")
        self.assertEqual(record["rating"], 1)
        self.assertIn("one small runnable snippet", persona.system_prompt("Write a Python example."))
        self.assertNotIn("one small runnable snippet", persona.system_prompt("What is the weather?"))

    def test_rating_is_explicitly_stored(self):
        persona.remember("Explain a loop.", "A short explanation.")
        core.titan_handle("rate that 5/5 because the answer was clear")
        record = persona.learning_records()[0]
        self.assertEqual(record["rating"], 5)
        self.assertEqual(record["feedback"], "the answer was clear")

    def test_long_exchange_is_saved_with_bounded_context(self):
        persona.remember("Explain " + "Python " * 60, "A detailed answer. " * 60)
        result = persona.record_feedback("Please be more concise.")
        self.assertIn("I saved that explicit feedback", result)
        record = persona.learning_records()[0]
        self.assertEqual(len(record["request"]), 300)
        self.assertEqual(len(record["reply"]), 700)
        self.assertTrue(record["request"].endswith("..."))
        self.assertTrue(record["reply"].endswith("..."))

    def test_sensitive_feedback_is_rejected(self):
        persona.remember("What is a Python function?", "A reusable block of code.")
        result = persona.record_feedback("My password is not for saving.")
        self.assertIn("won't save", result)
        self.assertEqual(persona.learning_records(), [])

    def test_sensitive_request_is_rejected(self):
        persona.remember("My password is abc123xyz.", "I cannot help store passwords.")
        result = persona.record_feedback("Please remember this answer.")
        self.assertIn("won't save", result)
        self.assertEqual(persona.learning_records(), [])

    def test_feedback_is_isolated_and_can_be_forgotten(self):
        persona.remember("Explain loops.", "A concise explanation.")
        persona.record_feedback("Please include a tiny example.", rating=4)
        persona.set_user("Alicia")
        self.assertEqual(persona.learning_records(), [])
        persona.set_user("Brian")
        result, _ = core.titan_handle("forget my feedback")
        self.assertIn("Removed 1", result)
        self.assertEqual(persona.learning_records(), [])

    def test_explicit_evaluation_runs_and_is_recorded(self):
        reply, keep_running = core.titan_handle("run a Titan learning evaluation")
        self.assertTrue(keep_running)
        self.assertIn("8/8 passed", reply)
        record = persona.learning_records()[0]
        self.assertEqual(record["type"], "evaluation")
        self.assertEqual(record["passed"], 8)

    def test_last_answer_can_be_presented_for_feedback(self):
        persona.remember("Explain loops.", "A concise explanation.")
        reply, _ = core.titan_handle("evaluate your last answer")
        self.assertIn("A concise explanation.", reply)
        self.assertIn("rate that 1/5", reply)


if __name__ == "__main__":
    unittest.main()
