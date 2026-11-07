"""Files, folders and notes, replayed from real sessions (the misheard words are real)."""

import unittest

from support import JarvisTest

from jarvis.tools import files


class FindAndOpen(JarvisTest):
    def setUp(self):
        super().setUp()
        self.add_folder("Desktop/Bragboards")
        self.add_file("Desktop/BragBoard Notes.docx", docx="Bragboard plan")
        self.add_file("Downloads/black background.png")
        self.add_file("Downloads/Board.png")
        self.add_file("Documents/WIL/REFLECTIVE REPORT.docx", docx="This term our team built a cooking game.")
        self.add_unity_project("RatAttack")
        self.add_file("UNITY Projects/RatAttack/Assets/Scripts/RatController.cs", "class RatController {}")
        self.add_file("UNITY Projects/RatAttack/Assets/Scripts/ControllerRat.cs")
        self.add_file("UNITY Projects/RatAttack/Library/PackageCache/RatThing.cs")   # generated: never found
        self.add_file("UNITY Projects/RatAttack/Assets/Scripts/RatController.cs.meta")

    def test_misheard_report_name_still_found(self):
        r = self.say("Where's my world effective report?")
        self.assertTrue(r.fast)
        self.assertIn("REFLECTIVE REPORT.docx is in the WIL folder in Documents", r.reply)

    def test_folder_word_only_searches_folders(self):
        # "Blackboards folder" once found black background.png.
        r = self.say("Where's my Blackboards folder?")
        self.assertNotIn("black background", r.reply)
        self.assertIn("closest is Bragboards", r.reply)

    def test_near_identical_sound_is_a_confident_match(self):
        r = self.say("No, where is my BragBords folder?")
        self.assertTrue(r.fast)
        self.assertEqual(r.reply, "Bragboards is on your Desktop.")

    def test_open_the_folder_means_the_one_just_found(self):
        self.say("Where's my bragboard notes?")
        r = self.say("Open the folder.")
        self.assertEqual(self.launched[-1], "Desktop")
        self.say("Where's my Bragboards folder?")
        self.say("Open the folder.")
        self.assertEqual(self.launched[-1], "Bragboards")
        self.assertIn("Opened", r.reply)

    def test_word_order_wins(self):
        r = self.say("Open the rat controller script")
        self.assertEqual(self.launched[-1], "RatController.cs")
        self.assertIn("RatAttack's Scripts folder", r.reply)

    def test_library_and_meta_files_are_skipped(self):
        names = {e.path.name for e in files._index}
        self.assertNotIn("RatThing.cs", names)
        self.assertNotIn("RatController.cs.meta", names)

    def test_root_folders(self):
        self.assertEqual(self.say("Open my downloads.").reply, "Opened Downloads.")
        r = self.say("What's in my downloads?")
        self.assertIn("Downloads has 2 things", r.reply)

    def test_nothing_found(self):
        r = self.say("Where's my tax return?")
        self.assertIn("couldn't find anything called 'tax return'", r.reply)

    def test_on_desktop_narrows_the_search(self):
        self.add_file("Desktop/Dream planner.txt", "Finish the degree.")
        self.add_file("Downloads/plan.jpg")
        r = self.say("What's in my plan on desktop?")
        self.assertEqual(r.prefetched[0][0], "files")
        self.assertIn("Dream planner.txt on your Desktop", r.prefetched[0][2])

    def test_open_named_document_from_a_place(self):
        self.add_file("Documents/POE Docs/Game Trailer.docx", docx="Trailer script")
        r = self.say("Open my game trailer document from documents.")
        self.assertTrue(r.fast)
        self.assertEqual(self.launched[-1], "Game Trailer.docx")

    def test_open_my_app_is_not_a_file(self):
        self.assertFalse(self.say("Open my Spotify").fast)  # goes to the LLM -> open_app


class Reading(JarvisTest):
    def setUp(self):
        super().setUp()
        self.add_file("Documents/POE Docs/Game Trailer.docx", docx="Open on the pizza shop at night, rats pour in.")

    def test_what_does_it_say_reads_the_last_file(self):
        self.say("Where's my game trailer?")
        r = self.say("What does it see?")   # "say", misheard
        self.assertFalse(r.fast)
        self.assertIn("rats pour in", r.prefetched[0][2])

    def test_whats_in_without_my(self):
        r = self.say("What's in game trailer?")
        self.assertIn("rats pour in", r.prefetched[0][2])

    def test_locked_by_word_then_yes_closes_and_reads(self):
        self.say("Open my game trailer document from documents.")
        self.word_has_open.add("Game Trailer.docx")
        r = self.say("What's inside this?")
        self.assertIn("Shall I close Word and read it?", r.prefetched[0][2])
        r = self.say("Yes.")
        self.assertEqual([p[0] for p in r.prefetched], ["window", "files"])
        self.assertIn("rats pour in", r.prefetched[1][2])
        self.assertIn("window:close:Word", self.launched)

    def test_folder_read_lists_it(self):
        self.add_folder("UNITY Projects/Flow State/FlowState")
        r = self.say("What's in the Flow State folder?")
        self.assertIn("Flow State has one thing in it: FlowState.", r.prefetched[0][2])


class Notes(JarvisTest):
    def test_note_without_text_asks_then_writes(self):
        self.assertEqual(self.say("Make a note called ideas.").reply, "What should I put in the ideas note?")
        r = self.say("AddyWetBoss.")
        self.assertEqual(r.reply, 'Added "AddyWetBoss" to a new note called Ideas.')

    def test_remove_that_and_add(self):
        self.say("Make a note called ideas.")
        self.say("The fast car.")
        r = self.say("Remove that and add a boss.")
        self.assertEqual(r.reply, 'Swapped "The fast car" for "A boss" in your Ideas note.')
        note = self.note("Ideas")
        self.assertIn("- A boss", note)
        self.assertNotIn("fast car", note)

    def test_note_misheard_as_node(self):
        self.say("Make a note called ideas: a rat boss")
        r = self.say("Add a combo meter to my ideas node.")
        self.assertEqual(r.reply, 'Added "A combo meter" to your Ideas note.')

    def test_multiline_note_is_one_bullet_per_line_and_undoes_together(self):
        self.say("Make a note called ideas: a boss")
        files.files("note", "Ideas", "1. Robots with glowing eyes.\n2. A swarm of flying creatures.\n3. Shadow figures.")
        note = self.note("Ideas")
        self.assertIn("- Robots with glowing eyes\n- A swarm of flying creatures\n- Shadow figures\n", note)
        self.assertEqual(self.say("Remove that.").reply, "Removed those 3 lines from your Ideas note.")
        self.assertIn("- A boss", self.note("Ideas"))

    def test_stray_glyphs_never_reach_the_file(self):
        files.files("note", "Game Trailer Summary", "Boss fight, and他说 ends with a warehouse explosion.")
        self.assertIn("Boss fight, and ends with a warehouse explosion", self.note("Game Trailer Summary"))

    def test_where_is_my_note_saved(self):
        self.add_file("UNITY Projects/Racer/Core/SaveSystem.cs")   # "saved" once matched this
        self.say("Make a note called ideas: the fast car")
        for question in ("Where is it located?", "Where is my note saved?", "Where's the file?"):
            r = self.say(question)
            self.assertEqual(r.reply, "Ideas.txt is in the JARVIS Notes folder in Documents.", question)

    def test_make_a_note_is_not_a_memory(self):
        self.say("Make a note called ideas: add a rat boss")
        self.assertEqual(self.facts(), [])


if __name__ == "__main__":
    unittest.main()
