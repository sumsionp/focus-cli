import unittest
import copy
import os
import sys

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI

class TestHierarchicalAdd(unittest.TestCase):
    def setUp(self):
        self.cli = FocusCLI()
        # Mock ledger to avoid file IO
        self.cli.commit_to_ledger = lambda label, items: None

    def test_new_task_with_subtasks(self):
        """Scenario 1: Grouping subtasks with a new parent task."""
        self.cli.mode = "FOCUS"
        self.cli.triage_stack = [self.cli._parse_single_line('[] Active Task')]

        lines = [
            "[] New Parent",
            "  [] Subtask 1",
            "  Note 1"
        ]
        items = self.cli._process_multi_line_input(lines)
        self.cli._handle_hierarchical_new_items('n', items)

        self.assertEqual(len(self.cli.triage_stack), 2)
        self.assertEqual(self.cli.triage_stack[1]['line'], "[] New Parent")
        self.assertEqual(self.cli.triage_stack[1]['notes'], ["[] Subtask 1", "Note 1"])

    def test_focus_preservation_with_N(self):
        """Scenario 5: Mixed batch with 'N' should preserve focus on current task."""
        initial_stack = [
            self.cli._parse_single_line('[] Task 1'),
            self.cli._parse_single_line('[] Task 2')
        ]
        self.cli.mode = "FOCUS"
        self.cli.triage_stack = copy.deepcopy(initial_stack)

        lines = [
            "  [] Subtask for 1",
            "[] New Task"
        ]
        items = self.cli._process_multi_line_input(lines)
        self.cli._handle_hierarchical_new_items('N', items)

        # Focus (index 0) should still be Task 1 (with its new subtask)
        self.assertEqual(self.cli.triage_stack[0]['line'], "[] Task 1")
        self.assertIn("[] Subtask for 1", self.cli.triage_stack[0]['notes'])
        # New task should be at index 1
        self.assertEqual(self.cli.triage_stack[1]['line'], "[] New Task")

    def test_triage_leading_subtasks(self):
        """Scenario 8/9: Leading subtasks in Triage Mode target index 0."""
        initial_stack = [
            self.cli._parse_single_line('[] Task 1'),
            self.cli._parse_single_line('[] Task 2')
        ]
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = copy.deepcopy(initial_stack)

        lines = [
            "  [] Sub 1",
            "[] New Top Task"
        ]
        items = self.cli._process_multi_line_input(lines)
        self.cli._handle_hierarchical_new_items('n', items)

        # Sub 1 should go to Task 1
        self.assertIn("[] Sub 1", self.cli.triage_stack[0]['notes'])
        # New Top Task should be appended (for 'n')
        self.assertEqual(self.cli.triage_stack[2]['line'], "[] New Top Task")

    def test_prepend_notes_order_preservation(self):
        """Ensure prepended hierarchical items maintain original order."""
        self.cli.mode = "FOCUS"
        task = self.cli._parse_single_line('[] Active Task')
        sub = self.cli._parse_single_line('[] Existing Sub')
        sub.indent = 2
        sub.parent = task
        task.children.append(sub)
        self.cli.triage_stack = [task]

        lines = [
            "  [] New Sub 1",
            "  [] New Sub 2"
        ]
        items = self.cli._process_multi_line_input(lines)
        # 'N' prepends to focus notes
        self.cli._handle_hierarchical_new_items('N', items)

        # Expected order: New Sub 1, New Sub 2, Existing Sub
        self.assertEqual(self.cli.triage_stack[0]['notes'][0], "[] New Sub 1")
        self.assertEqual(self.cli.triage_stack[0]['notes'][1], "[] New Sub 2")
        self.assertEqual(self.cli.triage_stack[0]['notes'][2], "[] Existing Sub")

if __name__ == '__main__':
    unittest.main()
