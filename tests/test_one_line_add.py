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
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]

        self.cli.handle_command('n "[] Task 2"')

        self.assertEqual(len(self.cli.triage_stack), 2)
        self.assertEqual(self.cli.triage_stack[1]['line'], "[] Task 2")
        self.assertEqual(self.cli.last_msg, "Task(s) Added")

    def test_N_one_line_top_level(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]

        self.cli.handle_command('N "[] Task 2"')

        self.assertEqual(len(self.cli.triage_stack), 2)
        self.assertEqual(self.cli.triage_stack[0]['line'], "[] Task 2")
        self.assertEqual(self.cli.triage_stack[1]['line'], "[] Task 1")
        self.assertEqual(self.cli.last_msg, "Task(s) Added & Prioritized")

    def test_n_one_line_subtask(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]

        # Adding a subtask in triage mode should target the first task
        self.cli.handle_command('n "  [] Subtask 1"')

        self.assertEqual(len(self.cli.triage_stack), 1)
        self.assertIn("[] Subtask 1", self.cli.triage_stack[0]['notes'])

    def test_N_one_line_subtask_focus_mode(self):
        self.cli.mode = "FOCUS"
        # Hierarchy: Task 1 -> Sub 1 -> Sub 2 (focused)
        # Proper internal representation (notes are relative to parent + 2)
        self.cli.triage_stack = [{
            'line': '[] Task 1',
            'notes': ['[] Sub 1', '  [] Sub 2']
        }]

        # N "    [] Sub 3" should add Sub 3 as a sibling of Sub 2, before Sub 2
        self.cli.handle_command('N "    [] Sub 3"')

        self.assertEqual(self.cli.triage_stack[0]['notes'], [
            '[] Sub 1',
            '  [] Sub 3', # New sibling before focus
            '  [] Sub 2'  # Old focus pushed down
        ])

    def test_n_one_line_note_filtered(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = []

        # Adding a top-level note should NOT add it to the triage stack
        self.cli.handle_command('n "My Note"')

        self.assertEqual(len(self.cli.triage_stack), 0)
        self.assertEqual(self.cli.last_msg, "Note(s) Added")

    def test_n_one_line_with_extra_spaces(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]

        # Verify it handles more than 2 spaces (deeper nesting or just extra space)
        self.cli.handle_command('n "    [] Sub Sub 1"')

        # In Triage mode, any leading subtask is relative to index 0.
        # _insert_hierarchical_batch strips 2 spaces.
        # "    [] Sub Sub 1" (4 spaces) -> "  [] Sub Sub 1" (2 spaces)
        self.assertEqual(self.cli.triage_stack[0]['notes'], ["  [] Sub Sub 1"])

    def test_n_unbalanced_quotes(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = []

        # Missing closing quote
        self.cli.handle_command('n "[] My task')

        self.assertEqual(len(self.cli.triage_stack), 1)
        self.assertEqual(self.cli.triage_stack[0]['line'], "[] My task")
        self.assertEqual(self.cli.last_msg, "Task(s) Added (Note: Added missing closing quote.)")

if __name__ == '__main__':
    unittest.main()
