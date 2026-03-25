import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import time
import sys
import os

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock FILENAME before importing FocusCLI
os.environ['FOCUS_FILENAME'] = 'test-plan.txt'

from focuscli import FocusCLI

class TestMeetingInterruption(unittest.TestCase):
    def setUp(self):
        self.cli = FocusCLI()
        # Mock dependencies to avoid side effects
        self.cli.play_chime = MagicMock()
        self.cli.commit_to_ledger = MagicMock()
        self.cli._run_with_vi = MagicMock()

    def test_new_meeting_visually_interrupts_break(self):
        # 1. Setup a meeting
        now = datetime.now()
        meeting_start = now + timedelta(minutes=1)
        meeting_end = meeting_start + timedelta(minutes=5)

        meeting_text = f"[] Meeting at {meeting_start.strftime('%I:%M %p')} 5m"
        self.cli.triage_stack = [self.cli._parse_single_line(meeting_text)]

        # 2. Start a break
        self.cli.mode = "BREAK"
        self.cli.break_start_time = time.time()
        self.cli.break_duration = 5

        # 3. Fast forward time to when meeting starts
        future_now = meeting_start + timedelta(seconds=1)

        with patch('focuscli.datetime') as mock_datetime:
            mock_datetime.now.return_value = future_now
            # 4. Call check_meetings
            self.cli.check_meetings()

        # 5. Verify results
        self.assertEqual(self.cli.mode, "BREAK") # Should stay in BREAK
        self.assertTrue(self.cli.break_meeting_interrupted) # But be interrupted
        self.assertEqual(self.cli.last_msg, "Meeting Starting: Meeting at " + meeting_start.strftime('%I:%M %p') + " 5m")

        # 6. Verify chime triggers in BREAK mode when interrupted
        self.cli.check_chime()
        self.cli.play_chime.assert_called()

    def test_break_during_ongoing_meeting_not_visually_interrupted(self):
        # 1. Setup an ongoing meeting that has already chimed
        now = datetime.now()
        meeting_start = now - timedelta(minutes=2)
        meeting_end = now + timedelta(minutes=5)

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

if __name__ == '__main__':
    unittest.main()
