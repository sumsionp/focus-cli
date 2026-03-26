import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import datetime as dt
import time

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import Item, Task, Meeting, Break

class TestArchitecture(unittest.TestCase):

    def test_random_quote(self):
        """Break.random_quote returns a random inspirational quote"""
        self.assertIn(Break.random_quote(), Break.BREAK_QUOTES)

    def test_datetime_knowledge(self):
        now = dt.time(2,3)

        self.assertEqual(now.strftime('%I:%M %p'), '02:03 AM')

        with patch('time.time', return_value=50000.0):
            time_now = time.time()

        time_now_plus_five_minutes = time_now + (5 * 60)

        self.assertEqual(time_now_plus_five_minutes, 50300.0)

    def test_break_attributes(self):
        """Break objects have attributes"""
        b1 = Break.from_line("[B] Run errand 3-4 PM")

        self.assertEqual(b1.duration, 60)
        self.assertEqual(b1.start_time.strftime('%I:%M %p'), '03:00 PM')
        self.assertEqual(b1.end_time.strftime('%I:%M %p'), '04:00 PM')

        content = "Be Inspired!"
        start = dt.datetime.combine(dt.date.today(), dt.time(3,55))
        end = dt.datetime.combine(dt.date.today(), dt.time(4,00))
        duration = 5

        # All attributes
        b2 = Break.from_attributes(content, 0, " ", start_time=start, end_time=end, duration=duration)

        self.assertEqual(b2.duration, 5)
        self.assertEqual(b2.start_time.strftime('%I:%M %p'), '03:55 AM')
        self.assertEqual(b2.end_time.strftime('%I:%M %p'), '04:00 AM')

        # Only start and end
        b3 = Break.from_attributes(content, 0, " ", start_time=start, end_time=end, duration=None)

        self.assertEqual(b3.duration, 5)
        self.assertEqual(b3.start_time.strftime('%I:%M %p'), '03:55 AM')
        self.assertEqual(b3.end_time.strftime('%I:%M %p'), '04:00 AM')
 
        # Only start and duration
        b4 = Break.from_attributes(content, 0, " ", start_time=start, end_time=None, duration=duration)

        self.assertEqual(b4.duration, 5)
        self.assertEqual(b4.start_time.strftime('%I:%M %p'), '03:55 AM')
        self.assertEqual(b4.end_time.strftime('%I:%M %p'), '04:00 AM')

        # Only end and duration
        b5 = Break.from_attributes(content, 0, " ", start_time=None, end_time=end, duration=duration)

        self.assertEqual(b5.duration, 5)
        self.assertEqual(b5.start_time.strftime('%I:%M %p'), '03:55 AM')
        self.assertEqual(b5.end_time.strftime('%I:%M %p'), '04:00 AM')

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
