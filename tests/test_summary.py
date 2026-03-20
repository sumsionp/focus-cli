import unittest
import os
import shutil
import tempfile
import sys

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI

class TestSummary(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)
        import focuscli
        focuscli.FILENAME = "test-plan.txt"
        self.cli = FocusCLI()

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def test_get_daily_summary_subtasks(self):
        with open("test-plan.txt", "w") as f:
            f.write("[] Task 1\n")
            f.write("  [x] Subtask 1.1\n")
            f.write("  [-] Subtask 1.2\n")
            f.write("[] Task 2\n")
            f.write("  Note 2.1\n")
            f.write("    [x] Subtask 2.1.1\n")
            f.write("[x] Task 1\n") # Resolving Task 1

        summary = self.cli.get_daily_summary()

        # Top-level: Task 1 [x], Task 2 [pending]
        self.assertEqual(summary['top']['[x]'], 1)
        self.assertEqual(summary['top']['[-]'], 0)

        # Subtasks: Subtask 1.1 [x], Subtask 1.2 [-], Subtask 2.1.1 [x]
        self.assertEqual(summary['sub']['[x]'], 2)
        self.assertEqual(summary['sub']['[-]'], 1)

    def test_same_name_different_parents(self):
        with open("test-plan.txt", "w") as f:
            f.write("[] Project A\n")
            f.write("  [x] Review\n")
            f.write("[] Project B\n")
            f.write("  [-] Review\n")

        summary = self.cli.get_daily_summary()
        self.assertEqual(summary['sub']['[x]'], 1)
        self.assertEqual(summary['sub']['[-]'], 1)

    def test_deferred_subtasks(self):
        with open("test-plan.txt", "w") as f:
            f.write("[] Project C\n")
            f.write("  [>] Deferred Subtask\n")
            f.write("[>] Project C\n")

        summary = self.cli.get_daily_summary()
        self.assertEqual(summary['top']['[>]'], 1)
        self.assertEqual(summary['sub']['[>]'], 1)

if __name__ == '__main__':
    unittest.main()
