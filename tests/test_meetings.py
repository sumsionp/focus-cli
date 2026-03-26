import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import time
import sys
import os

# Ensure the root directory is in sys.path so we can import focuscli
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI, Meeting

class TestMeetingInterruption(unittest.TestCase):
    def setUp(self):
        # Mock FILENAME to avoid creating real files during tests
        with patch('focuscli.FILENAME', 'test-plan.txt'):
            self.cli = FocusCLI()

        # Mock dependencies to avoid side effects
        self.cli.play_chime = MagicMock()
        self.cli.commit_to_ledger = MagicMock()
        self.cli._run_with_vi = MagicMock()

    def test_new_meeting_visually_interrupts_break(self):
        """Test that a newly starting meeting triggers visual interruption but stays in BREAK mode."""
        # 1. Setup a meeting
        now = datetime.now()
        meeting_start = now + timedelta(minutes=1)

        meeting_text = f"[] Meeting at {meeting_start.strftime('%I:%M %p')} 5m"
        self.cli.triage_stack = [self.cli._parse_single_line(meeting_text)]

        # 2. Start a break
        self.cli.mode = "BREAK"
        self.cli.break_start_time = time.time()
        self.cli.break_duration = 5
        self.cli.break_meeting_interrupted = False

        # 3. Fast forward time to when meeting starts
        future_now = meeting_start + timedelta(seconds=1)

        with patch('focuscli.datetime') as mock_datetime:
            mock_datetime.now.return_value = future_now
            # 4. Call check_meetings
            self.cli.check_meetings()

        # 5. Verify results
        self.assertEqual(self.cli.mode, "BREAK") # Should stay in BREAK
        self.assertTrue(self.cli.break_meeting_interrupted) # But be interrupted

        # Verify chime triggers in BREAK mode when interrupted
        self.cli.check_chime()
        self.cli.play_chime.assert_called()

    def test_break_during_ongoing_meeting_not_visually_interrupted(self):
        """Test that starting a break during an ongoing meeting does not trigger immediate interruption."""
        # 1. Setup an ongoing meeting that has already chimed
        now = datetime.now()
        meeting_start = now - timedelta(minutes=2)

        meeting_text = f"[] Meeting at {meeting_start.strftime('%I:%M %p')} 10m"
        self.cli.triage_stack = [self.cli._parse_single_line(meeting_text)]

        # Mark as already chimed
        meeting_id = f"[] Meeting at {meeting_start.strftime('%I:%M %p')} 10m_{meeting_start.replace(second=0, microsecond=0)}"
        self.cli.chimed_meetings.add(meeting_id)

        # 2. Start a break
        self.cli.mode = "BREAK"
        self.cli.break_start_time = time.time()
        self.cli.break_duration = 5
        self.cli.break_meeting_interrupted = False

        # 3. Call check_meetings
        with patch('focuscli.datetime') as mock_datetime:
            mock_datetime.now.return_value = now
            self.cli.check_meetings()

        # 4. Verify results
        self.assertEqual(self.cli.mode, "BREAK")
        self.assertFalse(self.cli.break_meeting_interrupted)

    def test_transition_from_break_to_focus(self):
        """Test the transition logic from break back to Focus session."""
        from focuscli import Break
        now_dt = datetime.now()
        start_dt = now_dt - timedelta(minutes=5)
        break_item = Break.from_attributes("Test Break", 0, 'B', start_time=start_dt, duration=5)

        self.cli.mode = "BREAK"
        # task_start_time was 10 mins before the break started
        # which is now_dt - 15 mins
        task_start_time_float = (start_dt - timedelta(minutes=10)).timestamp()
        self.cli.task_start_time = task_start_time_float
        self.cli.break_meeting_interrupted = True

        now_float = now_dt.timestamp()
        with patch('time.time', return_value=now_float), \
             patch('focuscli.datetime') as mock_datetime:
            mock_datetime.now.return_value = now_dt
            self.cli._transition_from_break_to_focus(break_item=break_item)

        self.assertEqual(self.cli.mode, "FOCUS")
        self.assertFalse(self.cli.break_meeting_interrupted)
        # task_start_time should have advanced by the break duration (5 mins = 300s)
        expected_task_start = task_start_time_float + 300
        self.assertAlmostEqual(self.cli.task_start_time, expected_task_start)

    def test_meeting_interface(self):
        pass

    def test_presense_of_start_date_end_date_duration(self):
        """Test whether at least 2 of start_date, end_date, and duration are present"""
        content = "[] Test Meeting with no date string"
        indent = 0
        state = ' '
        self.assertIsNone(Meeting.from_attributes(content, indent, state, None, None, None))

if __name__ == '__main__':
    unittest.main()
