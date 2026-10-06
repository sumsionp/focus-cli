import unittest
import os
from unittest.mock import patch
from tests.isolated_test_case import IsolatedTestCase
from focuscli import Task

class TestFreeWriteTriageSerialization(IsolatedTestCase):

    def test_free_write_serializes_triage_stack_order(self):
        cli = self.create_cli()

        # Populate ledger with initial entries
        initial_ledger = (
            "------- Free Write 10/06/2026 01:03:47 PM -------\n"
            "[] Task 1\n\n"
            "------- Triage Session Started at 10/06/2026 01:10:02 PM -------\n\n"
            "------- Triage 10/06/2026 01:10:09 PM -------\n"
            "[] Task 1\n\n"
            "------- Task Started 10/06/2026 01:10:09 PM -------\n"
            "[] Task 1\n\n"
            "------- Prioritized Entry(s) 10/06/2026 01:10:25 PM -------\n"
            "[] Focused task\n\n"
            "------- Task Started 10/06/2026 01:10:25 PM -------\n"
            "[] Focused task\n"
        )
        with open(cli.filename, 'w') as f:
            f.write(initial_ledger)

        cli.load_context()
        # Verify initial stack in memory
        self.assertEqual(len(cli.triage_stack), 2)

        # Prioritize "Focused task" to index 0 (as 'N' or reorder would do)
        focused_task = None
        for i, t in enumerate(cli.triage_stack):
            if t.content == "Focused task":
                focused_task = cli.triage_stack.pop(i)
                break
        cli.triage_stack.insert(0, focused_task)

        self.assertEqual(cli.triage_stack[0].content, "Focused task")
        self.assertEqual(cli.triage_stack[1].content, "Task 1")

        # Mock vi call when entering free write
        with patch.object(cli, '_run_with_vi'):
            cli.enter_free_write()

        # Check ledger content
        with open(cli.filename, 'r') as f:
            content = f.read()

        self.assertIn("------- Triage", content)
        self.assertIn("------- Free Write", content)

        # Reload context and verify the order is preserved
        cli.load_context()
        self.assertEqual(len(cli.triage_stack), 2)
        self.assertEqual(cli.triage_stack[0].content, "Focused task")
        self.assertEqual(cli.triage_stack[1].content, "Task 1")

    def test_free_write_empty_stack_skips_triage(self):
        cli = self.create_cli()
        cli.triage_stack.populate([])

        with patch.object(cli, '_run_with_vi'):
            cli.enter_free_write()

        with open(cli.filename, 'r') as f:
            content = f.read()

        self.assertNotIn("------- Triage ", content)
        self.assertIn("------- Free Write ", content)

    def test_quit_command_auto_serializes_triage_without_prompt(self):
        cli = self.create_cli()
        t1 = Task("Task 1")
        t2 = Task("Task 2")
        cli.triage_stack.populate([t1, t2])

        cmd = cli.handle_command("q")
        self.assertEqual(cli.mode, "EXIT")

        with open(cli.filename, 'r') as f:
            content = f.read()

        self.assertIn("------- Triage ", content)
        self.assertIn("[] Task 1", content)
        self.assertIn("[] Task 2", content)

    def test_legacy_or_edited_file_preserves_omitted_pending_tasks_after_triage_block(self):
        cli = self.create_cli()

        legacy_ledger = (
            "------- Free Write 10/06/2026 01:03:47 PM -------\n"
            "[] Task 1\n\n"
            "------- Prioritized Entry(s) 10/06/2026 03:59:43 PM -------\n"
            "[] This task on top\n\n"
            "------- Triage 10/06/2026 03:59:51 PM -------\n"
            "[] Task 1\n"
        )
        with open(cli.filename, 'w') as f:
            f.write(legacy_ledger)

        cli.load_context()

        # Both pending tasks should be present
        self.assertEqual(len(cli.triage_stack), 2)
        # "Task 1" is listed in the latest Triage block so it comes first
        self.assertEqual(cli.triage_stack[0].content, "Task 1")
        # "This task on top" was omitted from the latest Triage block so it is placed after
        self.assertEqual(cli.triage_stack[1].content, "This task on top")


if __name__ == '__main__':
    unittest.main()
