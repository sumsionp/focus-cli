import unittest
import os
import sys
from datetime import datetime

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import Item, Task, Meeting, Break

class TestArchitecture(unittest.TestCase):

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

        break_line = "[B] Lunch 12-1 PM"

        # Valid breaks
        b1 = Break.from_line(break_line)
        self.assertIsInstance(b1, Break)
        self.assertEqual(b1.content, "Lunch 12-1 PM")

        # [B] can't be a Meeting or a Task
        t1 = Task.from_line(break_line)
        self.assertIsNone(t1)

        m1 = Meeting.from_line(break_line)
        self.assertIsNone(m1)

if __name__ == '__main__':
    unittest.main()
