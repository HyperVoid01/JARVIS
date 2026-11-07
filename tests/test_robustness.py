"""The guards against a small model's mistakes, and other pure functions."""

import inspect
import unittest

from support import JarvisTest

from jarvis import tools
from jarvis.brain import _clean, _mostly_foreign, claims_action
from jarvis.tools import devices, files


class BrokenToolCalls(unittest.TestCase):
    params = inspect.signature(files.files).parameters

    def fix(self, args):
        return tools.repair_arguments("files", args, self.params)

    def test_call_nested_in_a_call(self):
        self.assertEqual(self.fix({"name": "files", "arguments": {"action": "list", "app": "documents"}}),
                         {"action": "list", "name": "documents"})

    def test_garbage_in_a_key(self):
        self.assertEqual(self.fix({"app": "documents", "name": "GameTrailer.docx", "action владельц": "open"}),
                         {"name": "GameTrailer.docx", "action": "open"})

    def test_aliases(self):
        self.assertEqual(self.fix({"action": "note", "title": "ideas", "content": "rat boss"}),
                         {"action": "note", "name": "ideas", "text": "rat boss"})


class ModelSlips(unittest.TestCase):
    def test_claimed_actions_without_a_tool_are_caught(self):
        for reply in ('I\'ve added "AddyWetBoss" to your ideas note.', "I've updated your ideas note.",
                      "I've set a timer for ten minutes.", "Spotify is now playing."):
            self.assertTrue(claims_action(reply), reply)
        for reply in ("Your report is in the WIL folder.", "The capital of France is Paris."):
            self.assertFalse(claims_action(reply), reply)

    def test_replies_in_chinese_are_spotted(self):
        self.assertTrue(_mostly_foreign("你是Sooriyan Gramani，就读于Emeris Umhlanga，学习游戏开发，擅长Unity和Blender。"))
        self.assertFalse(_mostly_foreign("Try opening itꦩ in a different app."))
        self.assertFalse(_mostly_foreign("Opened café."))

    def test_stray_glyphs_are_cleaned_from_speech(self):
        self.assertEqual(_clean("Try opening itꦩ in a **different** app."), "Try opening it in a different app.")


class Microphones(unittest.TestCase):
    mics = [{"index": 9, "name": "Microphone Array (Realtek(R) Audio)"}, {"index": 10, "name": "Headset (WH-1000XM5)"}]

    def test_spoken_names(self):
        for said, index in [("headset", 10), ("my handset", 10), ("the sony", 10), ("bluetooth", 10),
                            ("laptop mic", 9), ("weal tek", 9), ("mic two", 10), ("the second one", 10)]:
            device, error = devices.match_device(said, self.mics)
            self.assertIsNotNone(device, f"{said}: {error}")
            self.assertEqual(device["index"], index, said)

    def test_unknown_and_vague(self):
        self.assertIsNone(devices.match_device("webcam", self.mics)[0])
        self.assertIn("which microphone", devices.match_device("microphone", self.mics)[1])


class FastPathRouting(JarvisTest):
    """Commands the fast path must leave to the LLM (or must take)."""

    def test_left_to_the_llm(self):
        for text in ("Open Discord", "Find lofi on YouTube", "What's the capital of France?"):
            self.assertFalse(self.say(text).fast, text)

    def test_taken_by_the_fast_path(self):
        for text in ("Pause", "What time is it?", "Let's get to work", "Open my downloads"):
            self.assertTrue(self.say(text).fast, text)


if __name__ == "__main__":
    unittest.main()
