"""'Let's get to work' and workspaces, replayed from real sessions."""

import json
import unittest

from support import JarvisTest

from jarvis.tools import workspaces


class Workspaces(JarvisTest):
    def setUp(self):
        super().setUp()
        self.add_unity_project("Racer")
        self.add_unity_project("RatAttack", git_remote="https://github.com/HyperVoid01/RatAttack.git")

    def saved_items(self, name):
        return [(i["type"], i["target"]) for i in workspaces.load()[name]["items"]]

    def test_first_time_asks_what_to_open_then_saves(self):
        self.assertEqual(self.say("Let's get to work.").reply, "What are we working on, sir?")
        self.assertEqual(self.say("We're working on wrat attack.").reply, "What should I open for RatAttack?")
        r = self.say("Unity, VS Code, the GitHub repo and some music please.")
        self.assertEqual(r.reply, "Saved the RatAttack workspace and opened Unity, VS Code, GitHub and Spotify.")
        self.assertIn(("url", "https://github.com/HyperVoid01/RatAttack"), self.saved_items("RatAttack"))

    def test_saved_workspace_opens_straight_away(self):
        self.say("Let's work on Rat Attack")
        self.say("Unity and Discord")
        self.launched.clear()
        r = self.say("Let's get to work on Rat Attack")
        self.assertEqual(r.reply, "Opening RatAttack: Unity and Discord.")
        self.assertEqual(self.launched, ["Unity:RatAttack", "Discord"])

    def test_same_project_named_twice_opens_unity_once(self):
        self.say("Let's work on Racer.")
        r = self.say("The Racer, Unity project, Music, and the GitHub.")
        self.assertEqual(self.launched.count("Unity:Racer"), 1)
        self.assertEqual(r.reply.count("Unity"), 1)

    def test_misheard_name_corrected_by_answer(self):
        self.say("Let's work on Racer.")
        self.say("Unity and Spotify")
        self.say("Let's get to work.")
        r = self.say("The recent project.")
        self.assertIn("I don't have a workspace called Recent project", r.reply)
        r = self.say("I meant Racer.")
        self.assertEqual(r.reply, "Opening Racer: Unity and Spotify.")
        self.assertNotIn("Recent project", workspaces.load())

    def test_never_mind_cancels_and_later_commands_are_not_answers(self):
        self.say("Let's get to work")
        self.say("never mind")
        self.assertFalse(self.say("open spotify").fast)
        self.assertEqual(workspaces.load(), {})

    def test_misheard_app_is_left_out(self):
        self.say("Let's work on Racer.")
        r = self.say("Unity, Discord and Krita")
        self.assertIn("I couldn't find Krita", r.reply)
        self.assertNotIn(("app", "Krita"), self.saved_items("Racer"))

    def test_folders_and_documents_in_a_workspace(self):
        self.add_file("Documents/WIL/WIL Reflective Report.docx", docx="report")
        self.say("Let's get to work")
        self.say("WIL")
        r = self.say("Discord, the WIL folder and my reflective report")
        self.assertIn("Saved the WIL workspace", r.reply)
        kinds = [t for t, _ in self.saved_items("WIL")]
        self.assertEqual(kinds.count("path"), 2)

    def test_usual_workspace_is_suggested(self):
        import datetime as dt
        self.say("Let's work on Rat Attack")
        self.say("Unity and Discord")
        state = json.loads((self.tmp / "learning.json").read_text())
        now = dt.datetime.now()
        state["workspaces"] = [["RatAttack", now.weekday(), now.hour, (now.date() - dt.timedelta(days=d)).isoformat()]
                               for d in (1, 2, 3)]
        (self.tmp / "learning.json").write_text(json.dumps(state))
        self.assertEqual(self.say("Let's get to work").reply, "What are we working on, sir? RatAttack again?")
        self.assertEqual(self.say("Yes").reply, "Opening RatAttack: Unity and Discord.")


if __name__ == "__main__":
    unittest.main()
