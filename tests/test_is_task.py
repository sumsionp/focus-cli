import unittest
import copy
import os
import sys
import re

# Ensure the root directory is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from focuscli import FocusCLI
from focuscli import is_task

class TestIsTask(unittest.TestCase):
    def setUp(self):
        self.cli = FocusCLI()

    def test_is_task(self):
        """Test whether an item is a task or not."""
        pending_task_empty = '[] Active Task'
        pending_task_space = '[ ] Active Task'
        pending_task_break = '[B] Active Task'
        note = 'Top-level note'

        self.assertTrue(is_task(pending_task_empty))
        self.assertTrue(is_task(pending_task_space))
        self.assertTrue(is_task(pending_task_break))
        self.assertFalse(is_task(note))

if __name__ == '__main__':
    unittest.main()
