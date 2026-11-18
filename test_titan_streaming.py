import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_persona as persona


def ollama_lines(pieces):
    """Fake Ollama /api/chat streaming body: one JSON object per line."""
    lines = [json.dumps({"message": {"content": p}, "done": False}).encode() + b"\n" for p in pieces]
    lines.append(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")
    return lines


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StreamingTests(unittest.TestCase):
    def test_sentences_are_emitted_as_they_complete(self):
        heard = []
        pieces = ["Good after", "noon. The servo needs ", "a 5 volt supply, so use ", "an external one! Anything else?"]
        full = persona._stream_reply(ollama_lines(pieces), heard.append)
        self.assertEqual(full, "".join(pieces))
        self.assertEqual(heard, [
            "Good afternoon. The servo needs a 5 volt supply, so use an external one!",
            "Anything else?",
        ])

    def test_decimal_points_do_not_split_sentences(self):
        heard = []
        persona._stream_reply(ollama_lines(["Python 3.12 is a solid choice for this project.", " Next step?"]), heard.append)
        self.assertEqual(heard[0], "Python 3.12 is a solid choice for this project.")

    def test_markdown_is_cleaned_before_speaking(self):
        heard = []
        persona._stream_reply(ollama_lines(["**Right then**, here is the plan for today. ", "Done."]), heard.append)
        self.assertNotIn("*", " ".join(heard))

    def test_llm_chat_streams_and_returns_full_reply(self):
        heard = []
        body = ollama_lines(["Certainly. I've set that up for you, and it should work nicely now. ", "Cheers."])
        with patch.object(persona.urllib.request, "urlopen", return_value=FakeResponse(body)), \
                patch.object(persona, "system_prompt", return_value="sys"):
            reply, kind = persona.llm_chat("hello", "http://x", "m", on_sentence=heard.append)
        self.assertEqual(kind, "ok")
        self.assertTrue(heard)
        self.assertEqual(" ".join(heard), reply)

    def test_code_requests_are_not_streamed(self):
        heard = []
        body = json.dumps({"message": {"content": "```python\nprint(1)\n```"}}).encode()

        class Whole(FakeResponse):
            def read(self):
                return body

        with patch.object(persona.urllib.request, "urlopen", return_value=Whole([])) as urlopen, \
                patch.object(persona, "system_prompt", return_value="sys"):
            reply, _ = persona.llm_chat("code", "http://x", "m", preserve_formatting=True, on_sentence=heard.append)
        sent = json.loads(urlopen.call_args.args[0].data)
        self.assertFalse(sent["stream"])
        self.assertEqual(heard, [])
        self.assertIn("print(1)", reply)


if __name__ == "__main__":
    unittest.main()
