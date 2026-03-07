#!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime

def migrate_file(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    print(f"Migrating {filepath}...")

    with open(filepath, 'r') as f:
        lines = f.readlines()

    new_lines = []
    replacements = [
        (r'------- Work (.*) -------', r'------- Focus \1 -------'),
        (r'------- Work Session Complete (.*) -------', r'------- Focus Session Complete \1 -------'),
        (r'------- Work Session Re-started at (.*) -------', r'------- Focus Session Re-started at \1 -------'),
        (r'------- DEEP WORK SESSION (.*) -------', r'------- FOCUS SESSION \1 -------'),
    ]

    count = 0
    for line in lines:
        new_line = line
        for pattern, replacement in replacements:
            new_line, n = re.subn(pattern, replacement, new_line)
            count += n
        new_lines.append(new_line)

    if count > 0:
        backup_path = filepath + ".bak"
        with open(backup_path, 'w') as f:
            f.writelines(lines)

        with open(filepath, 'w') as f:
            f.writelines(new_lines)

        print(f"Successfully migrated {count} markers. Backup created at {backup_path}")
    else:
        print("No markers found to migrate.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 migrate_ledger.py <file1> <file2> ...")
        sys.exit(1)

    for arg in sys.argv[1:]:
        migrate_file(arg)
