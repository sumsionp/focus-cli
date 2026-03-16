import unittest
import os
import sys
import time
from datetime import datetime

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI

class TestCommandSwap(unittest.TestCase):
    def setUp(self):
        self.cli = FocusCLI()
        # Mock ledger and vi to avoid side effects
        self.cli.commit_to_ledger = lambda label, items, target_file=None: None
        self.cli._run_with_vi = lambda args: None
        self.cli.load_context = lambda: None
        self.cli.sort_triage_stack = lambda: None

    def test_triage_mode_f_enters_focus(self):
        self.cli.mode = "TRIAGE"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]
        self.cli.handle_command('f')
        self.assertEqual(self.cli.mode, "FOCUS")

    def test_triage_mode_w_enters_free_write(self):
        self.cli.mode = "TRIAGE"
        # enter_free_write sets mode back to TRIAGE after vi, so we check if it was called
        # by checking if a redraw was requested
        result = self.cli.handle_command('w')
        self.assertEqual(result, "REDRAW")
        self.assertEqual(self.cli.mode, "TRIAGE")

    def test_focus_mode_w_enters_free_write(self):
        self.cli.mode = "FOCUS"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]
        result = self.cli.handle_command('w')
        self.assertEqual(result, "REDRAW")
        # In actual code, enter_free_write is called which sets mode to TRIAGE
        self.assertEqual(self.cli.mode, "TRIAGE")

    def test_focus_mode_f_is_unassigned(self):
        self.cli.mode = "FOCUS"
        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]
        self.cli.last_msg = "test"
        self.cli.handle_command('f')
        # f should not change mode or do anything specific in FOCUS mode anymore
        self.assertEqual(self.cli.mode, "FOCUS")
        # In the current implementation, unhandled commands might clear last_msg or set it to error
        # but let's just ensure it didn't transition to TRIAGE or FREE WRITE

    def test_break_mode_f_resumes_focus(self):
        self.cli.mode = "BREAK"
        self.cli.break_start_time = time.time()
        self.cli.handle_command('f')
        self.assertEqual(self.cli.mode, "FOCUS")

    def test_exit_mode_w_returns_to_free_write(self):
        self.cli.mode = "EXIT"
        result = self.cli.handle_command('w')
        self.assertEqual(result, "REDRAW")
        self.assertEqual(self.cli.mode, "TRIAGE")

    def test_ui_messages(self):
        # Check if help messages are updated
        import io
        from contextlib import redirect_stdout

        self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]
        f = io.StringIO()
        with redirect_stdout(f):
            self.cli.render_triage()
        output = f.getvalue()
        self.assertIn("[f] focus", output)
        self.assertIn("[w] free write", output)

        f = io.StringIO()
        with redirect_stdout(f):
            self.cli.mode = "FOCUS"
            self.cli.triage_stack = [{'line': '[] Task 1', 'notes': []}]
            self.cli.render_focus()
        output = f.getvalue()
        self.assertIn("[w] free write", output)
        self.assertNotIn("[f] free write", output)

        f = io.StringIO()
        with redirect_stdout(f):
            self.cli.render_exit()
        output = f.getvalue()
        self.assertIn("Enter 'q' to quit or 'w' to return to Free Write...", self.cli.last_msg)

if __name__ == '__main__':
    unittest.main()
