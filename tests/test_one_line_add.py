import unittest
import sys
import os
import copy

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI

class TestOneLineAdd(unittest.TestCase):
    def setUp(self):
        self.cli = FocusCLI()
        # Mock ledger to avoid file IO
        self.cli.commit_to_ledger = lambda label, items: None
        # Mock vi input to avoid terminal hangs
        self.cli._get_multi_line_input = lambda context_lines=None: []

    def test_n_one_line_top_level(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [self.cli._parse_single_line('[] Task 1')]

        self.cli.handle_command('n "[] Task 2"')

        self.assertEqual(len(self.cli.triage_stack), 2)
        self.assertEqual(self.cli.triage_stack[1].content, "Task 2")
        self.assertEqual(self.cli.last_msg, "Task(s) Added")

    def test_N_one_line_top_level(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [self.cli._parse_single_line('[] Task 1')]

        self.cli.handle_command('N "[] Task 2"')

        self.assertEqual(len(self.cli.triage_stack), 2)
        self.assertEqual(self.cli.triage_stack[0].content, "Task 2")
        self.assertEqual(self.cli.triage_stack[1].content, "Task 1")
        self.assertEqual(self.cli.last_msg, "Task(s) Added & Prioritized")

    def test_n_one_line_subtask(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [self.cli._parse_single_line('[] Task 1')]

        # Adding a subtask in triage mode should target the first task
        self.cli.handle_command('n "  [] Subtask 1"')

        self.assertEqual(len(self.cli.triage_stack), 1)
        self.assertTrue(any(c.content == "Subtask 1" for c in self.cli.triage_stack[0].children))

    def test_N_one_line_subtask_focus_mode(self):
        self.cli.mode = "FOCUS"
        # Hierarchy: Task 1 -> Sub 1 -> Sub 2 (focused)
        task = self.cli._parse_single_line('[] Task 1')
        sub1 = self.cli._parse_single_line('[] Sub 1')
        sub1.indent = 2; sub1.parent = task
        sub2 = self.cli._parse_single_line('[] Sub 2')
        sub2.indent = 4; sub2.parent = sub1
        sub1.children.append(sub2)
        task.children.append(sub1)
        self.cli.triage_stack = [task]

        # N "    [] Sub 3" should add Sub 3 as a sibling of Sub 2, before Sub 2
        self.cli.handle_command('N "    [] Sub 3"')

        # Check children of Sub 1
        sub1 = self.cli.triage_stack[0].children[0]
        self.assertEqual(sub1.children[0].content, "Sub 3")
        self.assertEqual(sub1.children[1].content, "Sub 2")

    def test_n_one_line_note_filtered(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = []

        # Adding a top-level note should NOT add it to the triage stack
        self.cli.handle_command('n "My Note"')

        self.assertEqual(len(self.cli.triage_stack), 0)
        self.assertEqual(self.cli.last_msg, "Note(s) Added")

    def test_n_one_line_with_extra_spaces(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [self.cli._parse_single_line('[] Task 1')]

        # Verify it handles more than 2 spaces (deeper nesting or just extra space)
        self.cli.handle_command('n "    [] Sub Sub 1"')

        # In Triage mode, any leading subtask is relative to index 0.
        self.assertEqual(self.cli.triage_stack[0].children[0].content, "Sub Sub 1")
        # Check that indentation was preserved correctly (absolute)
        self.assertEqual(self.cli.triage_stack[0].children[0].indent, 4)

    def test_n_unbalanced_quotes(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = []

        # Missing closing quote
        self.cli.handle_command('n "[] My task')

        self.assertEqual(len(self.cli.triage_stack), 1)
        self.assertEqual(self.cli.triage_stack[0].content, "My task")
        self.assertEqual(self.cli.last_msg, "Task(s) Added (Note: Added missing closing quote.)")

if __name__ == '__main__':
    unittest.main()
