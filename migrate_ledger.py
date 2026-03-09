#!/usr/bin/env python3
import os
import re
import sys

# List of currently recognized marker labels (from both Focus and Work eras)
RECOGNIZED_LABELS = [
    "Free Write",
    "Triage Session Started at",
    "Triage",
    "Work",
    "Focus",
    "New Entry(s)",
    "Prioritized Entry(s)",
    "Cancelled",
    "Interrupted",
    "Interrupted (SIGTERM)",
    "Work Session Complete",
    "Focus Session Complete",
    "Work Session Re-started at",
    "Focus Session Re-started at",
    "Break for",
    "New Entry",
    "Prioritized Task",
    "Deferred from last session",
    "Deferred",
    "Edited",
    "Meeting Auto-Completed",
    "Task Started",
    "Task Completed",
    "Task Cancelled",
    "Task Deferred",
    "FOCUS SESSION",
    "DEEP WORK SESSION"
]

def migrate_file(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    print(f"Validating {filepath}...")

    with open(filepath, 'r') as f:
        lines = f.readlines()

    unrecognized = []
    for i, line in enumerate(lines):
        # Match marker format: ------- LABEL [TIMESTAMP] -------
        # First handle the "at" variations for Triage Session Started at, Work Session Re-started at, etc.
        m_at = re.match(r'^\s*------- (.*? Session (?:Started|Re-started) at) (?:[0-9/:\sAPM]+) -------\s*$', line)
        m_gen = re.match(r'^\s*------- (.*?) (?:[0-9/:\sAPM]+) -------\s*$', line)

        label_candidate = None
        if m_at:
            label_candidate = m_at.group(1).strip()
        elif m_gen:
            label_candidate = m_gen.group(1).strip()

        if label_candidate:
            # Special case for "Break for <mins> at"
            label_for_check = label_candidate
            if label_candidate.startswith("Break for"):
                label_for_check = "Break for"

            if label_for_check not in RECOGNIZED_LABELS:
                unrecognized.append((i + 1, line.strip()))

    if unrecognized:
        print(f"Aborting! Unrecognized markers found in {filepath}:")
        for line_num, content in unrecognized:
            print(f"  Line {line_num}: {content}")
        print("\nPlease update RECOGNIZED_LABELS in the script if these are valid.")
        return

    print(f"Migrating {filepath}...")

    new_lines = []
    replacements = [
        (r'------- Work (.*) -------', r'------- Focus \1 -------'),
        (r'------- Work Session Complete (.*) -------', r'------- Focus Session Complete \1 -------'),
        (r'------- Work Session Re-started at (.*) -------', r'------- Focus Session Re-started at \1 -------'),
        (r'------- DEEP WORK SESSION (.*) -------', r'------- FOCUS SESSION \1 -------'),
        (r'------- New Entry (.*) -------', r'------- New Entry(s) \1 -------'),
        (r'------- Prioritized Task (.*) -------', r'------- Prioritized Entry(s) \1 -------'),
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
