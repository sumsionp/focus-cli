import unittest
import os
import shutil
import tempfile
import sys
from datetime import datetime, timedelta

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI, DATE_FORMAT

class TestRescueTask(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)
        # Re-import to ensure we have a fresh FILENAME mock
        import focuscli
        import importlib
        importlib.reload(focuscli)
        self.cli = FocusCLI()

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def test_rescue_previous_tasks(self):
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        yesterday_str = yesterday.strftime(DATE_FORMAT)
        today_str = today.strftime(DATE_FORMAT)

        yesterday_file = f"{yesterday_str}-plan.txt"
        today_file = f"{today_str}-plan.txt"

        with open(yesterday_file, 'w') as f:
            f.write("[] Task 1\n")
            f.write("  Note 1\n")
            f.write("  [] Subtask 1\n")
            f.write("[x] Completed Task\n")
            f.write("Note alone\n")

        # Mock FILENAME to today's file
        import focuscli
        focuscli.FILENAME = today_file
        self.cli.rescue_previous_tasks()

        # Check if today's file exists and has the rescued task
        self.assertTrue(os.path.exists(today_file))
        with open(today_file, 'r') as f:
            content = f.read()
            self.assertIn("Deferred from last session", content)
            self.assertIn("[] Task 1", content)
            self.assertIn("  Note 1", content)
            self.assertIn("  [] Subtask 1", content)
            self.assertNotIn("Completed Task", content)
            self.assertNotIn("Note alone", content)

        # Check if yesterday's file has the deferred marker
        with open(yesterday_file, 'r') as f:
            content = f.read()
            self.assertIn(f"Deferred to {today_file}", content)
            self.assertIn("[>] Task 1", content)
            self.assertIn("  [>] Subtask 1", content)

    def test_rescue_chronological_order(self):
        today = datetime.now()
        day1 = today - timedelta(days=2)
        day2 = today - timedelta(days=1)

        day1_str = day1.strftime(DATE_FORMAT)
        day2_str = day2.strftime(DATE_FORMAT)
        today_str = today.strftime(DATE_FORMAT)

        with open(f"{day1_str}-plan.txt", 'w') as f:
            f.write("[] Task Day 1\n")
        with open(f"{day2_str}-plan.txt", 'w') as f:
            f.write("[] Task Day 2\n")

        import focuscli
        focuscli.FILENAME = f"{today_str}-plan.txt"
        self.cli.rescue_previous_tasks()

        with open(f"{today_str}-plan.txt", 'r') as f:
            content = f.read()
            # Task Day 1 should come before Task Day 2
            idx1 = content.find("Task Day 1")
            idx2 = content.find("Task Day 2")
            self.assertTrue(idx1 < idx2)

    def test_defer_to_tomorrow_marker(self):
        today = datetime.now()
        tomorrow = today + timedelta(days=1)
        today_str = today.strftime(DATE_FORMAT)
        tomorrow_str = tomorrow.strftime(DATE_FORMAT)

        today_file = f"{today_str}-plan.txt"
        tomorrow_file = f"{tomorrow_str}-plan.txt"

        with open(today_file, 'w') as f:
            f.write("[] Task to defer\n")

        import focuscli
        focuscli.FILENAME = today_file
        self.cli.load_context() # To populate triage_stack

        # Defer Task to tomorrow
        self.cli.handle_command(f"> {tomorrow_str}")

        # Check today's file for "Deferred to YYYYMMDD-plan.txt"
        with open(today_file, 'r') as f:
            content = f.read()
            self.assertIn(f"Deferred to {tomorrow_file}", content)
            self.assertIn("[>] Task to defer", content)

if __name__ == '__main__':
    unittest.main()
