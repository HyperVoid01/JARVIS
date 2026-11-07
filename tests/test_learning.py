"""Getting to know the user: daily question, remember offers, reminders, About Me."""

import datetime as dt
import json
import unittest

from support import JarvisTest

from jarvis import learning
from jarvis.tools import files, memory


class DailyQuestion(JarvisTest):
    daily_questions = True

    def test_asked_once_a_day_after_a_reply_that_worked(self):
        r = self.say("Open my downloads.")
        self.assertTrue(r.extra.startswith("By the way, sir,"))
        self.say("No thanks")
        self.assertEqual(self.say("Open my desktop").extra, "")   # not twice in a day

    def test_answer_is_saved_and_read_back(self):
        self.say("Open my downloads.")   # asks about the next deadline
        r = self.say("The WIL demo on Monday")
        self.assertTrue(r.reply.startswith("Noted: your next big deadline is the WIL demo on Monday"))
        self.assertTrue(any("next big deadline" in f for f in self.facts()))

    def test_not_now_skips_and_stop_turns_it_off(self):
        self.say("Open my downloads.")
        self.assertEqual(self.say("Not now").reply, "No problem, sir. Another time.")
        self.assertIn("I'll stop asking", self.say("Stop asking me questions").reply)
        state = json.loads((self.tmp / "learning.json").read_text())
        state.pop("last_question")
        (self.tmp / "learning.json").write_text(json.dumps(state))
        self.assertEqual(self.say("Open my desktop").extra, "")

    def test_answers_are_sanity_checked(self):
        self.say("Ask me something")                       # deadline
        self.say("The WIL demo on Monday")
        self.say("Ask me something")                       # main project
        self.say("Rat Attack, I think")
        self.assertEqual(self.say("Ask me something").reply, "When do you usually start working?")
        r = self.say("Rat Attack, I think")
        self.assertIn("I didn't catch a time", r.reply)
        self.assertFalse(any("start working" in f for f in self.facts()))

    def test_names_keep_their_capitals(self):
        self.say("Ask me something")
        self.say("The WIL demo on Monday")
        self.say("Ask me something")
        self.say("Rat Attack, I think")
        self.assertIn("Sooriyan's main project this week is Rat Attack", self.facts())

    def test_a_command_passes_through_a_waiting_question(self):
        self.say("Ask me something")
        r = self.say("Open my desktop")
        self.assertEqual(r.reply, "Opened Desktop.")
        self.assertEqual(self.facts(), [])


class RememberOffers(JarvisTest):
    def test_passing_mention_offers_to_remember(self):
        r = self.say("I've got a job interview next Tuesday")
        self.assertEqual(r.extra, "Want me to remember that?")
        r = self.say("Yes please")
        self.assertTrue(r.reply.startswith("Noted: you've got a job interview on Tuesday"))
        self.assertTrue(any("job interview" in f for f in self.facts()))

    def test_no_saves_nothing(self):
        self.say("I've got a job interview next Tuesday")
        self.say("No")
        self.assertEqual(self.facts(), [])

    def test_questions_are_not_offered(self):
        self.assertEqual(self.say("Do I have a demo on Friday?").extra, "")


class QuestionTiming(JarvisTest):
    daily_questions = True

    def test_no_question_when_starting_work(self):
        self.add_unity_project("RatAttack")
        self.say("Let's work on Rat Attack")
        r = self.say("Unity and Discord")
        self.assertEqual(r.extra, "")

    def test_a_question_back_is_not_an_answer(self):
        self.say("Open my downloads.")   # asks about the next deadline
        r = self.say("When do I finish my degree?")
        self.assertFalse(r.fast)
        self.assertEqual(self.facts(), [])


class Reminders(JarvisTest):
    def remember_in(self, days: int, what="my WIL demo is on"):
        day = (dt.date.today() + dt.timedelta(days=days)).strftime("%A")
        self.say(f"Remember that {what} {day}")

    def age_facts(self):
        data = json.loads((self.tmp / "memory.json").read_text())
        for f in data["facts"]:
            f["saved"] = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="minutes")
        (self.tmp / "memory.json").write_text(json.dumps(data))

    def test_not_reminded_the_day_it_was_saved(self):
        self.remember_in(2)
        self.assertNotIn("reminder", self.say("Open my desktop").reply)

    def test_reminded_once_a_day_when_close(self):
        self.remember_in(2)
        self.age_facts()
        r = self.say("Open my desktop")
        self.assertIn("Just a reminder, sir: your WIL demo is on", r.reply)
        self.assertIn("That's in 2 days.", r.reply)
        self.assertNotIn("reminder", self.say("Open my downloads").reply)

    def test_far_away_deadlines_are_not_mentioned(self):
        self.remember_in(6)
        self.age_facts()
        self.assertIsNone(learning.due_reminder())


class AboutMe(JarvisTest):
    def setUp(self):
        super().setUp()
        learning.ensure_about_dir()
        (learning.about_dir() / "About Sooriyan.txt").write_text(
            "Name: Sooriyan Gramani.\n\n"
            "Student number: ST10443023. Studying Game Development at Emeris Umhlanga until December 2026, "
            "when he finishes his degree.\n\n"
            "Skills: C# and Unity, AI systems, procedural generation.\n\n"
            "Games Sooriyan has made: Rat Attack, Scuba Defense, Sands of the Sphinx.", encoding="utf-8")

    def lookup(self, question):
        r = self.say(question)
        self.assertEqual(r.prefetched[0][0], "about_me", question)
        return r.prefetched[0][2]

    def test_specific_questions_find_the_right_paragraph(self):
        self.assertIn("ST10443023", self.lookup("What's my student number?"))
        self.assertIn("finishes his degree", self.lookup("When do I finish my degree?"))
        self.assertIn("Scuba Defense", self.lookup("What games have I made?"))

    def test_general_questions_get_an_overview_in_second_person(self):
        for q in ("Tell me about myself.", "Who am I?", "What do you know about me?"):
            result = self.lookup(q)
            self.assertIn("Name: Sooriyan Gramani", result)
            self.assertIn('say "you" and "your"', result)

    def test_readme_is_created_and_ignored(self):
        self.assertTrue((learning.about_dir() / "README.txt").exists())
        self.assertFalse(any(name == "README" for name, _ in learning._chunks()))

    def test_about_me_is_hidden_from_the_model(self):
        from jarvis import tools
        self.assertNotIn("about_me", [s["function"]["name"] for s in tools.schemas()])


class AnswerWording(JarvisTest):
    def test_capitals(self):
        for said, expected in [("The WIL demo on Monday", "the WIL demo on Monday"), ("Rat Attack, I think", "Rat Attack"),
                               ("Around nine", "around nine"), ("WIL demo", "WIL demo"), ("RatAttack", "RatAttack"),
                               ("A Unity game jam", "a Unity game jam"), ("Lofi beats, mostly", "lofi beats")]:
            self.assertEqual(learning._answer_sentence("{a}", said), expected, said)


class MemoryGrammar(JarvisTest):
    def test_adverbs_are_not_conjugated(self):
        self.assertEqual(memory.to_fact("I usually start working at 9am", "Sooriyan"),
                         "Sooriyan usually starts working at 9am")
        self.assertEqual(memory.to_fact("I like Unity", "Sooriyan"), "Sooriyan likes Unity")


if __name__ == "__main__":
    unittest.main()
