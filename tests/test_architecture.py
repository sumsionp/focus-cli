import unittest
import os
import sys
from datetime import datetime

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import Item, Task, Meeting, Break, Note, Header, parse_single_line

class TestArchitecture(unittest.TestCase):

    def test_tree_construction_from_lines(self):
        """Item.from_lines should correctly build a nested hierarchy."""
        lines = [
            "[] Root Task",
            "  [] Subtask 1",
            "    Note under subtask",
            "  Note directly under root",
            "  [] Subtask 2",
            "Note outside root"
        ]
        items = Item.from_lines(lines)

        self.assertEqual(len(items), 2)
        root = items[0]
        note_outside = items[1]

        self.assertIsInstance(root, Task)
        self.assertEqual(root.content, "Root Task")
        self.assertEqual(len(root.children), 3) # sub1, note, sub2

        sub1 = root.children[0]
        self.assertIsInstance(sub1, Task)
        self.assertEqual(sub1.content, "Subtask 1")
        self.assertEqual(len(sub1.children), 1)
        self.assertEqual(sub1.children[0].content, "Note under subtask")

        note_under = root.children[1]
        self.assertIsInstance(note_under, Note)
        self.assertEqual(note_under.content, "Note directly under root")

        sub2 = root.children[2]
        self.assertIsInstance(sub2, Task)
        self.assertEqual(sub2.content, "Subtask 2")

        self.assertIsInstance(note_outside, Note)
        self.assertEqual(note_outside.content, "Note outside root")

    def test_task_clone_with_state(self):
        """Task.clone_with_state should propagate markers to pending sub-tasks only."""
        lines = [
            "[] Parent",
            "  [] Pending Sub",
            "  [x] Completed Sub",
            "  Note"
        ]
        items = Item.from_lines(lines)
        parent = items[0]

        # Defer the parent
        deferred = parent.clone_with_state('>', '>')

        self.assertEqual(deferred.state, '>')
        self.assertEqual(deferred.children[0].state, '>') # Pending Sub becomes [>]
        self.assertEqual(deferred.children[1].state, 'x') # Completed Sub stays [x]
        self.assertIsInstance(deferred.children[2], Note)

        # Original should be untouched
        self.assertEqual(parent.state, ' ')
        self.assertEqual(parent.children[0].state, ' ')

    def test_serialization_to_ledger(self):
        """to_ledger should recreate the exact file format including indentation."""
        lines = [
            "[] Root",
            "  [] Subtask",
            "    Note"
        ]
        items = Item.from_lines(lines)
        root = items[0]

        expected = "[] Root\n  [] Subtask\n    Note"
        self.assertEqual(root.to_ledger(), expected)

    def test_meeting_detection(self):
        """Meeting.from_line should identify time patterns but ignore regular tasks."""
        # Valid meetings
        m1 = Meeting.from_line("[] Meeting 2-3 PM")
        self.assertIsInstance(m1, Meeting)
        self.assertEqual(m1.content, "Meeting 2-3 PM")

        # Regular tasks
        t1 = Meeting.from_line("[] Regular Task")
        self.assertIsNone(t1)

        t2 = Task.from_line("[] Regular Task")
        self.assertIsInstance(t2, Task)
        self.assertNotIsInstance(t2, Meeting)

    def test_break_detection(self):
        """Break.from_line should identify break pattern"""
        # Valid breaks
        b1 = Break.from_line("[B] Lunch 12-1 PM")
        self.assertIsInstance(b1, Break)
        self.assertEqual(b1.content, "Lunch 12-1 PM")

    def test_header_parsing(self):
        """Header.from_line should parse labels and timestamps."""
        h1 = Header.from_line("------- Triage Session Started 01/01/2024 10:00:00 AM -------")
        self.assertIsInstance(h1, Header)
        self.assertEqual(h1.label, "Triage Session Started")
        self.assertEqual(h1.timestamp, "01/01/2024 10:00:00 AM")

        h2 = Header.from_line("------- Simple Label -------")
        self.assertIsInstance(h2, Header)
        self.assertEqual(h2.label, "Simple Label")
        self.assertEqual(h2.timestamp, "")

if __name__ == '__main__':
    unittest.main()
