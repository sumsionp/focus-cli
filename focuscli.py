#!/usr/bin/env python3
import os
import sys
import re
import time
import logging
import copy
import random
import select
import termios
import tty
import signal
import subprocess
import shlex
import tempfile
from datetime import datetime, timedelta

# --- CONFIG ---
DATE_FORMAT = '%Y%m%d'
FILENAME = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime(f'{DATE_FORMAT}-plan.txt')
LOG_FILE = "focus_activity.log"
DEFAULT_FOCUS_THRESHOLD_MINS = 25
ALERT_THRESHOLD = DEFAULT_FOCUS_THRESHOLD_MINS * 60
CHIME_COMMAND = None # Set to a command string like "play /path/to/sound.wav" to override
MEETING_COLOR = "\033[1;32m" # Green
OVERLAP_COLOR = "\033[1;31m" # Red

BREAK_QUOTES = [
    "The time to relax is when you don't have time for it. – Sydney J. Harris",
    "Taking a break can lead to breakthroughs. – Unknown",
    "Rest is not idleness, and to lie sometimes on the grass under trees... is by no means a waste of time. – John Lubbock",
    "Sometimes the most productive thing you can do is relax. – Mark Black",
    "Almost everything will work again if you unplug it for a few minutes, including you. – Anne Lamott",
    "A break from everything is much needed once in a while. – Unknown",
    "Reflection is one of the most underused yet powerful tools for success. – Richard Carlson",
    "Disconnect to reconnect. – Unknown",
    "Your mind will answer most questions if you learn to relax and wait for the answer. – William S. Burroughs",
    "Pause. Breathe. Rest. Start again. – Unknown"
]

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)

def get_timestamp():
    return datetime.now().strftime('%m/%d/%Y %I:%M:%S %p')

def parse_defer_date(date_str):
    now = datetime.now()
    date_str = date_str.lower().strip()

    if not date_str or date_str == 'today':
        return now
    if date_str == 'tomorrow':
        return now + timedelta(days=1)

    days_map = {
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6
    }

    if date_str in days_map:
        target_weekday = days_map[date_str]
        current_weekday = now.weekday()
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:
            days_ahead += 7
        return now + timedelta(days=days_ahead)

    # Try YYYYMMDD
    try:
        return datetime.strptime(date_str, '%Y%m%d')
    except ValueError:
        pass

    # Try MM/DD/YYYY
    try:
        return datetime.strptime(date_str, '%m/%d/%Y')
    except ValueError:
        pass

    return None

def get_target_file(date):
    return date.strftime(f'{DATE_FORMAT}-plan.txt')

def parse_meeting_time(text):
    now = datetime.now()
    text = text.upper()

    # 1. Check for 2 PM 2h 15m format
    m1 = re.search(r'(\d{1,2}(?::\d{2})?)\s*(AM|PM)\s*(?:(\d+)H)?\s*(?:(\d+)M)?', text)
    if m1 and (m1.group(3) or m1.group(4)):
        start_time_str = m1.group(1)
        ampm = m1.group(2)
        hours = int(m1.group(3)) if m1.group(3) else 0
        minutes = int(m1.group(4)) if m1.group(4) else 0

        start_dt = _parse_time_with_ampm(start_time_str, ampm, now)
        end_dt = start_dt + timedelta(hours=hours, minutes=minutes)
        return start_dt, end_dt

    # 2. Check for 11:00 AM-1:00 PM format
    m2 = re.search(r'(\d{1,2}(?::\d{2})?)\s*(AM|PM)\s*-\s*(\d{1,2}(?::\d{2})?)\s*(AM|PM)', text)
    if m2:
        start_dt = _parse_time_with_ampm(m2.group(1), m2.group(2), now)
        end_dt = _parse_time_with_ampm(m2.group(3), m2.group(4), now)
        return start_dt, end_dt

    # 3. Check for 2:00-3:00 PM or 2-3 PM format
    m3 = re.search(r'(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)\s*(AM|PM)', text)
    if m3:
        end_time_str = m3.group(2)
        ampm = m3.group(3)
        end_dt = _parse_time_with_ampm(end_time_str, ampm, now)

        start_time_str = m3.group(1)
        start_dt = _parse_time_with_ampm(start_time_str, ampm, now)

        if start_dt > end_dt:
            alt_ampm = 'AM' if ampm == 'PM' else 'PM'
            start_dt = _parse_time_with_ampm(start_time_str, alt_ampm, now)

        return start_dt, end_dt

    return None

def _parse_time_with_ampm(time_str, ampm, reference_date):
    if ':' in time_str:
        h, m = map(int, time_str.split(':'))
    else:
        h = int(time_str)
        m = 0

    if ampm == 'PM' and h < 12:
        h += 12
    elif ampm == 'AM' and h == 12:
        h = 0

    return reference_date.replace(hour=h, minute=m, second=0, microsecond=0)

def strip_meeting_time(text):
    """Removes supported meeting time patterns from task text."""
    patterns = [
        # Format: 11:00 AM-1:00 PM (must be before more general formats)
        r'\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*-\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)',
        # Format: 2:00-3:00 PM or 2-3 PM
        r'\d{1,2}(?::\d{2})?\s*-\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)',
        # Format: 2 PM 2h 15m or just 2 PM
        r'\d{1,2}(?::\d{2})?\s*(?:AM|PM)(?:\s*\d+H)?(?:\s*\d+M)?'
    ]
    result = text
    for p in patterns:
        result = re.sub(p, '', result, flags=re.IGNORECASE)

    # Cleanup extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result

class FocusCLI:
    def __init__(self):
        self.mode = "TRIAGE"
        self.triage_stack = []
        self.initial_stack = []
        self.last_msg = "FocusCLI Ready."
        self.task_start_time = None
        self.focus_start_time = None
        self.break_start_time = None
        self.break_duration = 0
        self.break_quote = ""
        self.focus_threshold = ALERT_THRESHOLD
        self.last_chime_timestamp = 0
        self.chimed_meetings = set()
        self.original_termios = None
        self.mini_timer_active = False
        self.mini_timer_duration = 2
        self.mini_timer_remaining = 0
        self.mini_timer_last_tick = 0
        self.mini_timer_last_chime_timestamp = 0
        self.mini_timer_was_meeting = False
        self.last_recorded_focus = None
        self.break_meeting_interrupted = False

    def get_daily_summary(self):
        """Returns a dictionary of counts for top-level tasks and subtasks."""
        counts = {
            'top': {'[x]': 0, '[-]': 0, '[>]': 0},
            'sub': {'[x]': 0, '[-]': 0, '[>]': 0}
        }
        if not os.path.exists(FILENAME): return counts

        latest_states = {} # full_path_key -> (state, level)

        with open(FILENAME, 'r') as f:
            lines = f.readlines()

        stack = [] # current hierarchy of content strings

        for line in lines:
            line_raw = line.rstrip('\n\r')
            if not line_raw.strip() or "-------" in line_raw:
                continue

            m = re.match(r'^(\s*)', line_raw)
            indent = len(m.group(1)) if m else 0
            clean = line_raw.strip()
            level = indent // 2

            # Adjust stack to current level
            if level < len(stack):
                stack = stack[:level]
            while len(stack) < level:
                stack.append("") # Fill gaps

            marker_match = re.match(r'^\[([xe\->\s]?)\]', clean)
            if marker_match:
                state = marker_match.group(1).strip()
                if not state: state = 'pending'
                content = clean[marker_match.end():].strip()

                # Build a unique key based on parent path
                parent_path = " > ".join(stack)
                full_key = f"{parent_path} > {content}" if parent_path else content

                latest_states[full_key] = (state, level)
                stack.append(content)
            else:
                # It's a note; still update stack as it can be a parent
                content = clean
                stack.append(content)

        for key, (state, level) in latest_states.items():
            if state in ['x', '-', '>']:
                label = f"[{state}]"
                if level == 0:
                    if label in counts['top']:
                        counts['top'][label] += 1
                else:
                    if label in counts['sub']:
                        counts['sub'][label] += 1
        return counts

    def _run_with_vi(self, args):
        """Spawns vi with terminal state management."""
        fd = sys.stdin.fileno()
        if self.original_termios:
            termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
        subprocess.run(["vi"] + args)
        tty.setcbreak(fd)

    def enter_free_write(self):
        """Appends Free Write marker, launches vi, reloads context, and sorts the stack."""
        with open(FILENAME, 'a') as f:
            f.write(f"\n------- Free Write {get_timestamp()} -------\n\n")

        self._run_with_vi(["+$", "+startinsert", FILENAME])

        self.mode = "TRIAGE"
        self.commit_to_ledger("Triage Session Started at", [])
        self.load_context()
        self.sort_triage_stack()
        self.initial_stack = copy.deepcopy(self.triage_stack)

    def sort_triage_stack(self):
        """Move non-active meetings to the bottom, sorted by start time, while keeping active meetings at top."""
        if not self.triage_stack:
            return

        now = datetime.now()
        active_meetings = []
        other_tasks = []
        inactive_meetings = []

        for item in self.triage_stack:
            m_time = parse_meeting_time(item['line'])
            if m_time:
                if m_time[0] <= now < m_time[1]:
                    active_meetings.append(item)
                else:
                    inactive_meetings.append((m_time[0], item))
            else:
                other_tasks.append(item)

        # Sort inactive meetings by start time
        inactive_meetings.sort(key=lambda x: x[0])
        sorted_inactive = [m[1] for m in inactive_meetings]

        self.triage_stack = active_meetings + other_tasks + sorted_inactive

    def load_context(self):
        """Whole-file aware parser with resolution logic. Resolutions are [x], [-], [>], and [e]."""
        if not os.path.exists(FILENAME):
            with open(FILENAME, 'w') as f: f.write(f"Session Start - {get_timestamp()}\n")
            self.triage_stack = []
            return

        self.triage_stack = self._parse_file(FILENAME)

    def rescue_previous_tasks(self):
        """Scans the last 7 days for pending tasks and defers them to today."""
        # Only rescue if we are using the default daily plan format
        today_str = datetime.now().strftime(DATE_FORMAT)
        if FILENAME != f"{today_str}-plan.txt":
            return

        all_rescued_tasks = []
        today_dt = datetime.now()

        # Scan forward from 7 days ago to yesterday
        for i in range(7, 0, -1):
            prev_date = today_dt - timedelta(days=i)
            prev_file = get_target_file(prev_date)

            if os.path.exists(prev_file):
                # Parse the file for pending items
                tasks_and_notes = self._parse_file(prev_file)

                # We only want tasks (starting with [])
                pending_tasks = [t for t in tasks_and_notes if t['line'].strip().startswith('[]')]

                if pending_tasks:
                    # Mark as deferred in the old file
                    # Requirement: ------- Deferred to [Target Filename] <Timestamp> -------
                    label = f"Deferred to {FILENAME}"

                    # Prepare the deferred version for the old file
                    ledger_items = []
                    for task in pending_tasks:
                        # Current ledger version: main task [>], pending subtasks [>], others preserve
                        l_task = self._prepare_task_with_markers(task, '[>]', '[>]')
                        ledger_items.append(l_task)

                    self.commit_to_ledger(label, ledger_items, target_file=prev_file)

                    # Prepare the rescued tasks for today's file
                    # Requirement: Include full hierarchy of pending subtasks
                    for task in pending_tasks:
                        # Deep copy and strip meeting times
                        rescued_task = copy.deepcopy(task)
                        rescued_task['line'] = strip_meeting_time(rescued_task['line'])
                        # Target version: main task [], subtasks preserve status
                        t_task = self._prepare_task_with_markers(rescued_task, '[]', '[]')
                        all_rescued_tasks.append(t_task)

        if all_rescued_tasks:
            self.commit_to_ledger("Deferred from last session", all_rescued_tasks)
            # Update in-memory stack
            self.triage_stack.extend(all_rescued_tasks)

    def _parse_file(self, filepath):
        """Parses a ledger file and returns a list of active tasks and notes."""
        if not os.path.exists(filepath):
            return []

        with open(filepath, 'r') as f:
            lines = [l.rstrip() for l in f.readlines()]

        active_entries = {} # content -> {notes, is_task}
        entry_order = [] # list of contents
        last_entry_content = None

        for line in lines:
            if "------- Triage" in line:
                new_entry_order = []
                for content in entry_order:
                    if content in active_entries:
                        if active_entries[content]['is_task']:
                            new_entry_order.append(content)
                        else:
                            del active_entries[content]
                entry_order = new_entry_order
                last_entry_content = None
                continue

            if not line.strip() or "-------" in line:
                continue

            if not line.startswith('  '):
                clean = line.strip()
                marker_match = re.match(r'^\[([xe\->\s]?)\]\s*', clean)
                if marker_match:
                    state = marker_match.group(1).strip()
                    content = clean[marker_match.end():].strip()

                    if not state:
                        # Pending task
                        notes = active_entries.pop(content, {}).get('notes', [])
                        active_entries[content] = {'notes': notes, 'is_task': True}
                        if content in entry_order: entry_order.remove(content)
                        entry_order.append(content)
                    else:
                        # Resolution
                        active_entries.pop(content, None)
                        if content in entry_order: entry_order.remove(content)
                    last_entry_content = content
                else:
                    # Non-task entry
                    content = clean
                    notes = active_entries.pop(content, {}).get('notes', [])
                    active_entries[content] = {'notes': notes, 'is_task': False}
                    if content in entry_order: entry_order.remove(content)
                    entry_order.append(content)
                    last_entry_content = content
            else:
                # Indented line
                if last_entry_content and last_entry_content in active_entries:
                    # Remove only the first 2 spaces to preserve deeper nesting
                    note = line[2:] if line.startswith('  ') else line.lstrip()
                    notes_list = active_entries[last_entry_content]['notes']

                    # Subtask/Note resolution logic
                    sub_marker_match = re.match(r'^\[([xe\->\s]?)\]\s*', note)
                    if sub_marker_match:
                        sub_content = note[sub_marker_match.end():].strip()
                        # Remove any existing instance of this subtask content
                        new_notes = []
                        for n in notes_list:
                            m = re.match(r'^\[[xe\->\s]?\]\s*', n)
                            if m and n[m.end():].strip() == sub_content:
                                continue
                            new_notes.append(n)
                        notes_list = new_notes
                        notes_list.append(note)
                    else:
                        if note in notes_list: notes_list.remove(note)
                        notes_list.append(note)
                    active_entries[last_entry_content]['notes'] = notes_list

        stack = []
        for content in entry_order:
            if content in active_entries:
                entry = active_entries[content]
                stack.append({
                    'line': f"[] {content}" if entry['is_task'] else content,
                    'notes': entry['notes']
                })
        return stack

    def _prepare_defer_tasks(self, task, target_date):
        """Prepare tasks for ledger and target file without committing them."""
        is_target_today = target_date.date() == datetime.now().date()
        today_str = datetime.now().strftime(DATE_FORMAT)
        is_current_file_today = today_str in FILENAME

        # Deep copy to avoid mutating original
        deferred_task = copy.deepcopy(task)
        deferred_task['line'] = strip_meeting_time(deferred_task['line'])

        # Target version: main task [], subtasks preserve status
        target_task = self._prepare_task_with_markers(deferred_task, '[]', '[]')
        # Current ledger version: main task [>], pending subtasks [>], others preserve
        ledger_task = self._prepare_task_with_markers(deferred_task, '[>]', '[>]')

        if is_target_today and is_current_file_today:
            return ledger_task, target_task, "today"
        else:
            target_file = get_target_file(target_date)
            return ledger_task, target_task, target_file

    def _handle_defer_command(self, base_cmd, parts):
        """Common logic for > and >> deferral commands."""
        defer_date_str = " ".join(parts[1:])
        target_date = parse_defer_date(defer_date_str)
        if not target_date:
            self.last_msg = f"Invalid date: {defer_date_str}"
            return True

        if not self.triage_stack:
            return True

        ledger_items = []
        target_items = []
        target_res = None

        if base_cmd == '>>':
            count = len(self.triage_stack)
            while self.triage_stack:
                task = self.triage_stack.pop(0)
                l_task, t_task, res = self._prepare_defer_tasks(task, target_date)
                ledger_items.append(l_task)
                if t_task: target_items.append(t_task)
                target_res = res

            if target_res == "today":
                self.commit_to_ledger("Deferred", ledger_items)
                self.triage_stack.extend(target_items)
                self.last_msg = f"Deferred {count} items to end of today's stack"
            else:
                label = f"Deferred to {target_res}"
                self.commit_to_ledger("Deferred from last session", target_items, target_file=target_res)
                self.commit_to_ledger(label, ledger_items)
                self.last_msg = f"Deferred {count} items to {target_res}"
        else: # base_cmd == '>'
            task = self.triage_stack.pop(0)
            l_task, t_task, res = self._prepare_defer_tasks(task, target_date)
            if res == "today":
                self.commit_to_ledger("Deferred", [l_task])
                self.triage_stack.append(t_task)
                self.last_msg = "Deferred to end of today's stack"
            else:
                label = f"Deferred to {res}"
                self.commit_to_ledger("Deferred from last session", [t_task], target_file=res)
                self.commit_to_ledger(label, [l_task])
                self.last_msg = f"Deferred to {res}"

        self.commit_to_ledger("Triage", self.triage_stack)
        self.task_start_time = None
        self.initial_stack = copy.deepcopy(self.triage_stack)
        return True

    def _get_multi_line_input(self, context_lines=None):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            # Start with a blank line for typing
            tf.write("\n")
            # Pad with some space so comments are clearly at the bottom
            tf.write("\n\n")
            # Comments at the bottom for visual alignment
            tf.write("# Enter one task or note per line\n")
            if context_lines:
                for cl in context_lines:
                    tf.write(f"#{cl}\n")
            temp_path = tf.name

        try:
            self._run_with_vi(["+startinsert", temp_path])

            with open(temp_path, 'r') as f:
                lines = [l.rstrip() for l in f.readlines() if not l.startswith('#')]

            return lines
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _process_multi_line_input(self, lines):
        """Parse multi-line input into items, preserving absolute indentation levels."""
        if not lines:
            return []

        items = []
        current_item = None

        for line in lines:
            if not line.strip(): continue

            m = re.match(r'^(\s*)', line)
            indent_len = len(m.group(1))
            content = line[indent_len:]

            # A line starts a new item if:
            # 1. There is no current item
            # 2. OR its indentation is less than or equal to the current item's base indentation
            if not current_item or indent_len <= current_item['indent']:
                current_item = {'line': content, 'notes': [], 'indent': indent_len}
                items.append(current_item)
            else:
                # It's a note for the current item
                # Preserve relative indentation for notes by stripping only the task's base indent + 2
                note_rel = line[current_item['indent'] + 2:] if len(line) >= current_item['indent'] + 2 else line.lstrip()
                current_item['notes'].append(note_rel)

        return items

    def _edit_item(self, item):
        original_item = copy.deepcopy(item)

        content = [item['line']]
        for note in item['notes']:
            content.append(f"  {note}")

        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            tf.write("\n".join(content))
            temp_path = tf.name

        try:
            self._run_with_vi([temp_path])

            with open(temp_path, 'r') as f:
                new_lines = [l.rstrip() for l in f.readlines() if l.strip()]

            if not new_lines: return item

            new_line = new_lines[0]
            new_notes = [l[2:] if l.startswith('  ') else l.strip() for l in new_lines[1:]]

            new_item = {'line': new_line, 'notes': new_notes}

            if new_item != original_item:
                # Handle ledger
                edited_old = copy.deepcopy(original_item)
                # If it was a task, mark as [e]
                if edited_old['line'].startswith('[]'):
                    edited_old['line'] = re.sub(r'^\[\s?\]', '[e]', edited_old['line'])
                else:
                    # If it was a note, we just mark it as [e] anyway to satisfy auditability
                    edited_old['line'] = f"[e] {edited_old['line']}"

                # New item should be [] if it wasn't already or if it's a note that we want to become a task?
                # Actually, the user can decide in vi.
                # But if it doesn't have a marker, and they wanted it to be a task, they should add [].
                # However, the user said "The new task should be written as pending '[]'".
                # Let's ensure if it was a task, it stays a task.
                if original_item['line'].startswith('[]') and not new_item['line'].startswith('[]'):
                    new_item['line'] = f"[] {new_item['line']}"

                self.commit_to_ledger("Edited", [edited_old, new_item])
                self.last_msg = "Item Edited"
                return new_item

            return item
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _prepare_task_with_markers(self, task, main_marker, pending_sub_marker):
        """Helper to create a copy of a task with updated markers for pending items."""
        new_task = copy.deepcopy(task)

        # Helper to process a single line
        def process_line(line, marker):
            m = re.match(r'^(\s*)\[([xe\->\s]?)\]\s*', line)
            if m:
                indent = m.group(1)
                state = m.group(2).strip()
                if not state: # only change if pending
                    content = line[m.end():].strip()
                    return f"{indent}{marker} {content}"
            return line

        new_task['line'] = process_line(task['line'], main_marker)
        new_task['notes'] = [process_line(n, pending_sub_marker) for n in task['notes']]
        return new_task

    def _get_subtask_as_item(self, parent_task, idx):
        subtask_line = parent_task['notes'][idx]
        notes = []
        i = idx + 1
        while i < len(parent_task['notes']) and parent_task['notes'][i].startswith('  '):
            # Remove the extra 2 spaces of indentation
            notes.append(parent_task['notes'][i][2:])
            i += 1
        return {'line': subtask_line, 'notes': notes}, i

    def _update_subtask_from_item(self, parent_task, idx, end_idx, item):
        new_lines = [item['line']] + [f"  {n}" for n in item['notes']]
        parent_task['notes'][idx:end_idx] = new_lines
        return idx + len(new_lines)

    def _get_recursive_focus(self, item):
        """Recursively find the deepest pending task."""
        for i, note in enumerate(item['notes']):
            if note.strip().startswith('[]'):
                sub_item, sub_end_idx = self._get_subtask_as_item(item, i)
                deep_item, deep_parent, deep_path = self._get_recursive_focus(sub_item)

                if deep_parent is None:
                    # sub_item is the focus, item is its parent
                    return deep_item, item, [i]
                else:
                    # Focus is even deeper
                    return deep_item, deep_parent, [i] + deep_path

        # No pending subtasks found
        return item, None, []

    def _update_recursive_item(self, top_item, path, new_sub_item):
        """Update a sub-item in the hierarchy recursively."""
        self._recursive_set(top_item, path, new_sub_item)

    def _recursive_set(self, item, path, new_sub_item):
        if not path:
            item['line'] = new_sub_item['line']
            item['notes'] = new_sub_item['notes']
            return

        idx = path[0]
        sub_item, end_idx = self._get_subtask_as_item(item, idx)
        self._recursive_set(sub_item, path[1:], new_sub_item)
        self._update_subtask_from_item(item, idx, end_idx, sub_item)

    def _recursive_insert(self, item, path, new_items, position='before'):
        """Recursively insert items into the hierarchy relative to the focus path."""
        if not path:
            if position == 'append':
                # Appending to the end of this item's notes
                for it in new_items:
                    prefix = " " * it.get('indent', 0)
                    item['notes'].append(f"{prefix}{it['line']}")
                    for n in it['notes']:
                        item['notes'].append(f"{prefix}  {n}")
                return False
            elif position == 'prepend_notes':
                # Prepend to the beginning of this item's notes
                new_lines = []
                for it in new_items:
                    prefix = " " * it.get('indent', 0)
                    new_lines.append(f"{prefix}{it['line']}")
                    for n in it['notes']:
                        new_lines.append(f"{prefix}  {n}")
                item['notes'][0:0] = new_lines
                return False
            else:
                return True # Signal to parent to insert relative to this item

        idx = path[0]
        sub_item, end_idx = self._get_subtask_as_item(item, idx)

        if len(path) == 1 and position not in ['append', 'prepend_notes']:
            # We are in the parent of the focus item
            new_lines = []
            for it in new_items:
                prefix = " " * it.get('indent', 0)
                new_lines.append(f"{prefix}{it['line']}")
                for n in it['notes']:
                    new_lines.append(f"{prefix}  {n}")

            if position == 'before':
                item['notes'][idx:idx] = new_lines
            else:
                # 'after' - insert after the sub-item AND its notes
                item['notes'][end_idx:end_idx] = new_lines
        else:
            # Recurse deeper
            self._recursive_insert(sub_item, path[1:], new_items, position)
            # Update our record of the sub_item which might have changed
            self._update_subtask_from_item(item, idx, end_idx, sub_item)

        return False

    def _handle_hierarchical_new_items(self, base_cmd_orig, items, target_index=None):
        """Processes a batch of items and inserts them into the task tree based on absolute indentation."""
        if target_index is not None:
            mode_label = f"New Entry(s) at index {target_index}"
        else:
            mode_label = "Prioritized Entry(s)" if base_cmd_orig == 'N' else "New Entry(s)"

        any_changed = False

        # Separate items into top-level (indent 0) and hierarchical (indent > 0)
        top_level_items = [it for it in items if it['indent'] == 0]
        hier_items = [it for it in items if it['indent'] > 0]

        if hier_items and self.triage_stack:
            any_changed = True

            # Use target_index if provided, otherwise default to 0
            idx = target_index if target_index is not None else 0

            if idx < len(self.triage_stack):
                target_task = self.triage_stack[idx]

                if self.mode in ["TRIAGE"] or target_index is not None:
                    focus_path = []
                    focus_indents = [0]
                else:
                    _, _, focus_path = self._get_recursive_focus(target_task)
                    # Calculate absolute indentation of focus path elements
                    focus_indents = [0] # Top-level task is 0
                    curr = target_task
                    for p_idx in focus_path:
                        sub, _ = self._get_subtask_as_item(curr, p_idx)
                        focus_indents.append(focus_indents[-1] + 2)
                        curr = sub

                msg = "Sub-item(s) Added"
                if self.mode == "TRIAGE" or target_index is not None:
                    # Target specific task in stack
                    self._insert_hierarchical_batch(target_task, focus_path, hier_items, base_cmd_orig)
                    self.commit_to_ledger(mode_label, [target_task])
                else:
                    # Focus mode recursive insertion
                    # Reverse items for 'N' to maintain original order when prepending/inserting before
                    items_to_process = reversed(hier_items) if base_cmd_orig == 'N' else hier_items
                    for it in items_to_process:
                        it_copy = copy.deepcopy(it)
                        focus_indent = focus_indents[len(focus_path)]

                        depth_offset = (it['indent'] - focus_indent) // 2
                        target_depth = len(focus_path) + depth_offset
                        target_depth = max(0, min(len(focus_path) + 1, target_depth))

                        if target_depth > 0:
                            it_copy['indent'] = it['indent'] - focus_indents[target_depth - 1] - 2
                        else:
                            it_copy['indent'] = it['indent']

                        pos = 'before' if base_cmd_orig == 'N' else 'after'
                        if target_depth == len(focus_path) + 1:
                            # Child: ignore n/N for exact position, just append or prepend to notes
                            child_pos = 'append' if base_cmd_orig == 'n' else 'prepend_notes'
                            self._recursive_insert(target_task, focus_path, [it_copy], position=child_pos)
                        else:
                            # Sibling or higher
                            target_path = focus_path[:target_depth]
                            self._recursive_insert(target_task, target_path, [it_copy], position=pos)

                    self.commit_to_ledger(mode_label, [target_task])
                    self.last_recorded_focus = target_task['line'].strip()
                    if base_cmd_orig == 'N':
                        self.task_start_time = None

                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg

        if top_level_items:
            any_changed = True
            # Handle top-level items using standard stack logic
            # (Notes are committed here but not added to triage_stack)
            self.commit_to_ledger(mode_label, top_level_items)

            # Filter only items that are tasks (start with []) to add to the triage stack
            top_level_tasks = [it for it in top_level_items if it['line'].strip().startswith('[]')]

            # If we have top-level notes that aren't tasks, and we ARE in N1-style indexed mode,
            # they were already committed to the ledger above but should NOT be added to stack.
            # If we are in standard N/n mode, the original behavior for notes was...
            # Wait, let's check N/n behavior for notes.
            # In N mode: top_level_tasks are inserted at index 0. Notes are lost from stack but kept in ledger?
            # Actually, current code for 'N':
            # msg = "Task(s) Added & Prioritized" if top_level_tasks else "Note(s) Added & Prioritized"
            # It only adds top_level_tasks to self.triage_stack.

            if target_index is not None:
                # Specified index insertion
                insert_idx = target_index
                if hier_items and target_index < len(self.triage_stack):
                    # If we also added sub-items to an existing task, insert new tasks AFTER it
                    insert_idx += 1

                # Cap insertion index at current stack size
                insert_idx = min(insert_idx, len(self.triage_stack))

                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)

                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0]['line'].strip()
                    self.task_start_time = None

                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"
                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg
            elif base_cmd_orig == 'N':
                # If we also had hierarchical items, and they targeted index 0 (N always does),
                # we insert new top-level tasks at index 1 to preserve focus on the original task.
                insert_idx = 1 if (hier_items and self.triage_stack) else 0

                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)

                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0]['line'].strip()
                    self.task_start_time = None

                msg = "Task(s) Added & Prioritized" if top_level_tasks else "Note(s) Added & Prioritized"
                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg
            else:
                self.triage_stack.extend(top_level_tasks)
                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"
                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg

        return any_changed

    def _insert_hierarchical_batch(self, target, path, items, base_cmd_orig):
        # Normalizes a batch of items (stripping the common 2-space prefix)
        # and appends/prepends to target hierarchy.

        # In Triage Mode (or whenever path is empty), 'before'/'after' don't make sense
        # for leading items as there is no focus item to be relative to.
        # Instead, we use 'prepend_notes' for N and 'append' for n.
        if not path:
            pos = 'prepend_notes' if base_cmd_orig == 'N' else 'append'
        else:
            pos = 'before' if base_cmd_orig == 'N' else 'after'

        # Reverse items for prepend_notes/before to keep their original order
        items_to_process = reversed(items) if pos in ['prepend_notes', 'before'] else items

        for it in items_to_process:
            # Shift item to be relative to target (e.g. 2 spaces -> 0 spaces)
            it_copy = copy.deepcopy(it)
            it_copy['indent'] = max(0, it['indent'] - 2)
            self._recursive_insert(target, path, [it_copy], position=pos)

    def _transition_from_break_to_focus(self):
        now = time.time()
        break_total_time = now - self.break_start_time
        if self.task_start_time:
            self.task_start_time += break_total_time
        self.focus_start_time = now
        self.mode = "FOCUS"
        self.break_meeting_interrupted = False
        if self.mini_timer_active:
            self.mini_timer_remaining = self.mini_timer_duration * 60
            self.mini_timer_last_tick = now
            self.mini_timer_last_chime_timestamp = 0
        self.commit_to_ledger("Focus Session Re-started at", [])
        self.last_msg = "Focus Resumed"
        self.last_chime_timestamp = 0

    def _rescue_stack(self, label="Interrupted"):
        """Commits the current triage_stack to the ledger if it contains items."""
        if self.triage_stack:
            self.commit_to_ledger(label, self.triage_stack)
            return True
        return False

    def _get_progress_stats(self, focus_item, parent_item):
        completed = 0
        total = 0

        if parent_item is None:
            # Top-level progress
            summary = self.get_daily_summary()
            completed = sum(summary['top'].values())
            pending = 0
            for it in self.triage_stack:
                if it['line'].strip().startswith('[]') or it['line'].strip().startswith('[ ]'):
                    pending += 1
            total = completed + pending
        else:
            # Subtask level progress
            for line in parent_item['notes']:
                if line.startswith('  '):
                    continue

                marker_match = re.match(r'^\[([xe\->\s]?)\]', line.strip())
                if marker_match:
                    total += 1
                    state = marker_match.group(1).strip()
                    if state in ['x', '-', '>']:
                        completed += 1

        return completed, total

    def _render_progress_bar(self, completed, total):
        if total == 0:
            return ""

        try:
            term_width = os.get_terminal_size().columns
        except OSError:
            term_width = 65

        # Bar label: " Completed 5/30"
        label = f" Completed {completed}/{total}"
        max_bar_width = term_width - len(label) - 2 # 2 for brackets []
        if max_bar_width < 10:
             # Fallback if terminal is very narrow
             return f"[{completed}/{total}]"

        bar_width = min(40, max_bar_width) # Cap bar at 40 chars or terminal width
        filled_width = int(round((completed / total) * bar_width))

        bar = "#" * filled_width + " " * (bar_width - filled_width)
        return f"[{bar}]{label}"

    def _get_path_pruned_item(self, item, path, leaf_item=None):
        """Returns a copy of item with hierarchy pruned to only show the path to focus."""
        if not path:
            return copy.deepcopy(leaf_item if leaf_item else item)

        new_item = copy.deepcopy(item)
        idx = path[0]
        sub_item, end_idx = self._get_subtask_as_item(new_item, idx)

        pruned_sub = self._get_path_pruned_item(sub_item, path[1:], leaf_item)

        # Rebuild notes: keep non-task notes and the path-relevant subtask
        new_notes = []
        current_idx = 0
        while current_idx < len(item['notes']):
            if current_idx == idx:
                new_notes.append(pruned_sub['line'])
                for sn in pruned_sub['notes']:
                    new_notes.append(f"  {sn}")
                _, next_idx = self._get_subtask_as_item(item, current_idx)
                current_idx = next_idx
            else:
                line = item['notes'][current_idx]
                if not re.match(r'^(\s*)\[([xe\->\s]?)\]\s*', line):
                    new_notes.append(line)
                current_idx += 1

        new_item['notes'] = new_notes
        return new_item

    def commit_to_ledger(self, mode_label, items, target_file=None):
        dest = target_file if target_file else FILENAME
        with open(dest, 'a') as f:
            f.write(f"\n------- {mode_label} {get_timestamp()} -------\n")
            if items:
                for t in items:
                    f.write(f"{t['line']}\n")
                    for n in t['notes']:
                        f.write(f"  {n}\n")

    def update_mini_timer(self):
        if not self.mini_timer_active:
            return

        now = time.time()

        if self.mode == "FOCUS" and self.triage_stack:
            if self.mini_timer_last_tick == 0:
                self.mini_timer_last_tick = now

            elapsed = now - self.mini_timer_last_tick
            if elapsed >= 1.0:
                ticks = int(elapsed)
                self.mini_timer_remaining -= ticks
                self.mini_timer_last_tick += ticks

            if self.mini_timer_remaining <= 0:
                if now - self.mini_timer_last_chime_timestamp >= 30:
                    self.play_chime(sound='tick')
                    self.mini_timer_last_chime_timestamp = now
        else:
            self.mini_timer_last_tick = now

    def play_chime(self, sound='chime'):
        if CHIME_COMMAND:
            subprocess.Popen(shlex.split(CHIME_COMMAND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        # Fallback chain
        if sound == 'chime':
            linux_file = "/usr/share/sounds/freedesktop/stereo/complete.oga"
            macos_file = "/System/Library/Sounds/Glass.aiff"
        else:
            # For 'tick', use something sharper/shorter
            linux_file = "/usr/share/sounds/freedesktop/stereo/bell.oga"
            macos_file = "/System/Library/Sounds/Tink.aiff"

        commands = []
        if sys.platform == "darwin":
            commands.append(["afplay", macos_file])
            commands.append(["osascript", "-e", "beep"])
        else:
            commands.append(["paplay", linux_file])
            commands.append(["play", linux_file])

        for cmd in commands:
            try:
                # Check if command exists
                if subprocess.call(["which", cmd[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
            except Exception:
                continue

        # Final fallback to terminal bell
        sys.stdout.write('\a')
        sys.stdout.flush()

    def check_chime(self):
        now = time.time()
        if self.mode == "BREAK":
            elapsed_break = now - self.break_start_time
            remaining = self.break_duration * 60 - elapsed_break

            if remaining <= 0 or self.break_meeting_interrupted:
                if now - self.last_chime_timestamp >= 60:
                    self.play_chime()
                    self.last_chime_timestamp = now
                    if remaining <= 0:
                        self.last_msg = "!! BREAK EXPIRED !!"
        elif self.mode in ["FOCUS", "TRIAGE"]:
            is_meeting = False
            if self.mode == "FOCUS" and self.triage_stack:
                is_meeting = parse_meeting_time(self.triage_stack[0]['line']) is not None

            if self.focus_start_time:
                focus_elapsed = now - self.focus_start_time
                if focus_elapsed >= self.focus_threshold:
                    if now - self.last_chime_timestamp >= 60:
                        if not is_meeting:
                            self.play_chime()
                        self.last_chime_timestamp = now

    def is_meeting_active(self):
        if not self.triage_stack: return False
        m_time = parse_meeting_time(self.triage_stack[0]['line'])
        if not m_time: return False
        now_dt = datetime.now()
        return m_time[0] <= now_dt < m_time[1]

    def check_meetings(self):
        if self.mode not in ["FOCUS", "BREAK"]: return
        if not self.triage_stack: return

        now = datetime.now()
        found_active_meeting = False
        for i, task in enumerate(self.triage_stack):
            m_time = parse_meeting_time(task['line'])
            if m_time and m_time[0] <= now < m_time[1]:
                meeting_id = f"{task['line']}_{m_time[0]}"
                if meeting_id not in self.chimed_meetings:
                    if self.mode == "BREAK":
                        self.break_meeting_interrupted = True
                    self.play_chime()
                    self.chimed_meetings.add(meeting_id)
                    task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', task['line'])
                    self.last_msg = f"Meeting Starting: {task_content}"

                if self.mode == "FOCUS":
                    if i > 0 and not found_active_meeting:
                        current_task = self.triage_stack[0]
                        current_m_time = parse_meeting_time(current_task['line'])
                        is_current_active_meeting = current_m_time and current_m_time[0] <= now < current_m_time[1]

                        if not is_current_active_meeting:
                            self.triage_stack.insert(0, self.triage_stack.pop(i))
                            self.task_start_time = None
                            task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', self.triage_stack[0]['line'])
                            self.last_msg = f"Meeting Started: {task_content}"
                            found_active_meeting = True

                    if i == 0:
                        found_active_meeting = True

    def render_break(self):
        elapsed_break = time.time() - self.break_start_time
        remaining = int(self.break_duration * 60 - elapsed_break)

        sign = ""
        if remaining < 0:
            sign = "-"
            abs_rem = abs(remaining)
        else:
            abs_rem = remaining

        m, s = divmod(abs_rem, 60)
        time_str = f"{sign}{m:02d}:{s:02d}"

        color = "\033[1;34m"
        header = " BREAK SESSION "
        if remaining <= 0 or self.break_meeting_interrupted:
            color = "\033[1;31;7m"
            header = " !! BREAK EXPIRED !! " if remaining <= 0 else " !! MEETING STARTING !! "

        print(color + "="*65 + "\033[0m")
        print(f"{color}{header}\033[0m | Remaining: {time_str}")
        print(color + "="*65 + "\033[0m")

        print(f"\n\033[1;32mFOCUS >> \033[0m{self.break_quote}")

        print("\n" + color + "-"*65 + "\033[0m")
        print("Cmds: [N#] prioritize, [n#] add, [t] triage, [f] focus, [q] quit")

    def update_timer_ui(self):
        """Minimal redraw of just the header to preserve terminal selection."""
        sys.stdout.write("\033[s") # Save cursor
        now = time.time()
        if self.mode == "TRIAGE":
            focus_elapsed = int(now - (self.focus_start_time if self.focus_start_time else now))
            focus_remaining = self.focus_threshold - focus_elapsed
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(focus_remaining), 60)
            f_color = "\033[1;31m" if focus_remaining <= 0 else ""
            timer_str = f" | Focus: {f_color}{f_sign}{fm:02d}:{fs:02d}\033[0m"

            sys.stdout.write("\033[1;1H" + f"\033[K--- TRIAGE: {os.path.basename(FILENAME)}{timer_str} ---")
        elif self.mode == "FOCUS":
            if not self.triage_stack: return
            if self.task_start_time is None: self.task_start_time = now
            if self.focus_start_time is None: self.focus_start_time = now

            top_task = self.triage_stack[0]
            focus_elapsed = int(now - self.focus_start_time)
            focus_remaining = self.focus_threshold - focus_elapsed
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(focus_remaining), 60)

            meeting_time = None
            if self.triage_stack:
                meeting_time = parse_meeting_time(top_task['line'])
            meeting_timer_str = ""
            if meeting_time:
                now_dt = datetime.now()
                remaining = int((meeting_time[1] - now_dt).total_seconds())
                m_sign = "-" if remaining < 0 else ""
                mm, ms = divmod(abs(remaining), 60)
                meeting_timer_str = f" | Meeting: {m_sign}{mm:02d}:{ms:02d}"

            mini_timer_str = ""
            is_mini_session = False
            if self.mini_timer_active and self.triage_stack:
                is_mini_session = True
                sign = "-" if self.mini_timer_remaining < 0 else ""
                mm, ms = divmod(abs(self.mini_timer_remaining), 60)
                mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"

            task_timer_str = ""
            if not (meeting_timer_str and mini_timer_str):
                task_elapsed = int(now - self.task_start_time)
                tm, ts = divmod(task_elapsed, 60)
                task_timer_str = f" | Task: {tm:02d}:{ts:02d}"

            color = "\033[1;34m"
            header = " MINI TASK SESSION " if is_mini_session else " FOCUS SESSION "
            if focus_elapsed > self.focus_threshold:
                color = "\033[1;31;7m"
                header = " !! BREAK TIME !! "

            sys.stdout.write("\033[1;1H" + f"{color}{'='*65}\033[0m")
            sys.stdout.write("\033[2;1H" + f"{color}{header}\033[0m{task_timer_str} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
            sys.stdout.write("\033[3;1H" + f"{color}{'='*65}\033[0m")
        elif self.mode == "BREAK":
            elapsed_break = time.time() - self.break_start_time
            remaining = int(self.break_duration * 60 - elapsed_break)
            sign = "-" if remaining < 0 else ""
            m, s = divmod(abs(remaining), 60)
            color = "\033[1;34m"
            header = " BREAK SESSION "
            if remaining <= 0 or self.break_meeting_interrupted:
                color = "\033[1;31;7m"
                header = " !! BREAK EXPIRED !! " if remaining <= 0 else " !! MEETING STARTING !! "

            sys.stdout.write("\033[1;1H" + f"{color}{'='*65}\033[0m")
            sys.stdout.write("\033[2;1H" + f"{color}{header}\033[0m | Remaining: {sign}{m:02d}:{s:02d}")
            sys.stdout.write("\033[3;1H" + f"{color}{'='*65}\033[0m")

        sys.stdout.write("\033[u") # Restore cursor
        sys.stdout.flush()

    def _read_keypress(self, fd):
        """Reads a single keypress, escape sequence burst, or multi-byte UTF-8 character."""
        try:
            b = os.read(fd, 1)
            if not b: return None

            # UTF-8 multi-byte character handling
            if (b[0] & 0x80) != 0 and b[0] != 0x1b:
                # Count leading 1s to determine length
                if (b[0] & 0xE0) == 0xC0: length = 2
                elif (b[0] & 0xF0) == 0xE0: length = 3
                elif (b[0] & 0xF8) == 0xF0: length = 4
                else: return b.decode('utf-8', errors='ignore') # Invalid leading byte

                seq = b
                for _ in range(length - 1):
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if r:
                        next_b = os.read(fd, 1)
                        if not next_b: break
                        seq += next_b
                    else:
                        break
                return seq.decode('utf-8', errors='ignore')

            if b == b'\x1b':
                # burst read for escape sequences
                seq = b
                while True:
                    r, _, _ = select.select([fd], [], [], 0.02)
                    if r:
                        next_b = os.read(fd, 1)
                        if not next_b: break
                        seq += next_b
                        # CSI terminators are 0x40-0x7E
                        if len(seq) >= 3 and seq[1:2] == b'[' and (0x40 <= seq[-1] <= 0x7E):
                            break
                        # SS3 terminators
                        if len(seq) == 3 and seq[1:2] == b'O':
                            break
                        if len(seq) > 10: break
                    else:
                        break
                return seq.decode('utf-8', errors='ignore')
            else:
                return b.decode('utf-8', errors='ignore')
        except Exception:
            return None

    def run(self):
        fd = sys.stdin.fileno()
        self.original_termios = termios.tcgetattr(fd)

        def signal_handler(sig, frame):
            self._rescue_stack("Interrupted (SIGTERM)")
            if self.original_termios:
                termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)

        # Check for previous tasks if this is the first launch for today's file
        if not os.path.exists(FILENAME):
            self.rescue_previous_tasks()

        # Always open in Free Write mode at start
        self.enter_free_write()
        self.focus_start_time = time.time()
        try:
            # Set terminal to cbreak mode for the main input loop
            tty.setcbreak(fd)
            buffer = ""
            cursor_pos = 0
            last_render_second = -1
            last_buffer = None
            last_cursor_pos = None
            last_mode = None
            last_msg = None
            last_task = None
            last_expired = False
            last_exceeded = False

            while True:
                now = time.time()
                current_second = int(now)
                current_task = self.triage_stack[0] if self.triage_stack else None

                is_expired = False
                if self.mode == "BREAK":
                    elapsed_break = now - self.break_start_time
                    is_expired = (elapsed_break >= self.break_duration * 60)

                is_exceeded = False
                if self.mode == "FOCUS" and self.focus_start_time:
                    focus_elapsed = now - self.focus_start_time
                    is_exceeded = (focus_elapsed > self.focus_threshold)

                structural_change = (
                    buffer != last_buffer or
                    cursor_pos != last_cursor_pos or
                    self.mode != last_mode or
                    self.last_msg != last_msg or
                    current_task != last_task or
                    is_expired != last_expired or
                    is_exceeded != last_exceeded
                )

                if structural_change:
                    sys.stdout.write("\033[H\033[2J")
                    if self.mode == "TRIAGE":
                        self.render_triage()
                    elif self.mode == "FOCUS":
                        self.render_focus()
                    elif self.mode == "BREAK":
                        self.render_break()
                    elif self.mode == "EXIT":
                        self.render_exit()

                    print(f"\n\033[90mStatus: {self.last_msg}\033[0m")
                    prompt = ">> "
                    sys.stdout.write(f"\033[1;37m{prompt}\033[0m{buffer}")

                    # Manual cursor positioning for the prompt line
                    # Note: This is a simplified positioning that doesn't handle multi-line cursor placement perfectly
                    # but since we're using full-screen redraw, it's sufficient for the user to see where they are.
                    # We move cursor back by (len(buffer) - cursor_pos)
                    if cursor_pos < len(buffer):
                        move_back = len(buffer) - cursor_pos
                        sys.stdout.write(f"\033[{move_back}D")

                    sys.stdout.flush()

                    last_render_second = current_second
                    last_buffer = buffer
                    last_cursor_pos = cursor_pos
                    last_mode = self.mode
                    last_msg = self.last_msg
                    last_task = copy.deepcopy(current_task)
                    last_expired = is_expired
                    last_exceeded = is_exceeded

                elif current_second != last_render_second:
                    if self.mode in ["FOCUS", "BREAK", "TRIAGE"]:
                        self.update_timer_ui()
                    last_render_second = current_second

                if self.mode in ["FOCUS", "BREAK", "TRIAGE"]:
                    self.check_chime()

                if self.mode in ["FOCUS", "BREAK"]:
                    self.check_meetings()
                    if self.mode == "FOCUS":
                        self.update_mini_timer()

                rlist, _, _ = select.select([fd], [], [], 0.1)
                if rlist:
                    char = self._read_keypress(fd)
                    if not char: continue

                    if char == ' ' and self.mode == "FOCUS" and self.mini_timer_active and not buffer:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0
                        self.last_msg = "Mini Timer Reset"
                    elif char == '\n' or char == '\r':
                        cmd = buffer.strip()
                        buffer = ""

                        if not cmd and self.mode != "EXIT":
                            last_mode = None # Force redraw on empty enter
                            cursor_pos = 0
                            continue

                        # Restore terminal for command processing
                        termios.tcsetattr(fd, termios.TCSANOW, self.original_termios)
                        print()
                        result = self.handle_command(cmd)
                        tty.setcbreak(fd)
                        cursor_pos = 0

                        if result == "QUIT":
                            print() # Ensure newline for shell prompt
                            break

                        # For all other results (None, REDRAW, etc)
                        if result == "REDRAW":
                            last_mode = None
                        continue
                    elif char in ['\x7f', '\x08']: # Backspace
                        if cursor_pos > 0:
                            buffer = buffer[:cursor_pos-1] + buffer[cursor_pos:]
                            cursor_pos -= 1
                    elif char == '\x03': # Ctrl+C
                        raise KeyboardInterrupt
                    elif char.startswith('\x1b'): # ESC sequence
                        seq = char
                        if seq in ['\x1b[D', '\x1bOD']: # Left Arrow
                            if cursor_pos > 0: cursor_pos -= 1
                        elif seq in ['\x1b[C', '\x1bOC']: # Right Arrow
                            if cursor_pos < len(buffer): cursor_pos += 1
                        elif seq in ['\x1b[H', '\x1b[1~', '\x1bOH']: # Home
                            cursor_pos = 0
                        elif seq in ['\x1b[F', '\x1b[4~', '\x1bOF']: # End
                            cursor_pos = len(buffer)
                        elif seq in ['\x1b[A', '\x1bOA', '\x1b[B', '\x1bOB']: # Up/Down Arrows
                            pass # Just swallow them
                        elif seq in ['\x1b[3~']: # Delete
                            if cursor_pos < len(buffer):
                                buffer = buffer[:cursor_pos] + buffer[cursor_pos+1:]
                        else:
                            logging.info(f"Unhandled escape sequence: {repr(seq)}")
                    elif char == '\x01': # Ctrl+A (Home)
                        cursor_pos = 0
                    elif char == '\x05': # Ctrl+E (End)
                        cursor_pos = len(buffer)
                    elif char == '\x04': # Ctrl+D (Delete)
                        if cursor_pos < len(buffer):
                            buffer = buffer[:cursor_pos] + buffer[cursor_pos+1:]
                    elif len(char) == 1 and ord(char) >= 32: # Only printable characters
                        buffer = buffer[:cursor_pos] + char + buffer[cursor_pos:]
                        cursor_pos += 1
        except KeyboardInterrupt:
            self._rescue_stack("Interrupted")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

    def render_triage(self):
        now = time.time()
        focus_elapsed = int(now - (self.focus_start_time if self.focus_start_time else now))
        focus_remaining = self.focus_threshold - focus_elapsed
        f_sign = "-" if focus_remaining < 0 else ""
        fm, fs = divmod(abs(focus_remaining), 60)

        f_color = "\033[1;31m" if focus_remaining <= 0 else ""
        timer_str = f" | Focus: {f_color}{f_sign}{fm:02d}:{fs:02d}\033[0m"

        print(f"--- TRIAGE: {os.path.basename(FILENAME)}{timer_str} ---")

        meetings = []
        for i, t in enumerate(self.triage_stack):
            m_time = parse_meeting_time(t['line'])
            if m_time:
                meetings.append({'idx': i, 'start': m_time[0], 'end': m_time[1]})

        overlapping_indices = set()
        for i in range(len(meetings)):
            for j in range(i + 1, len(meetings)):
                m1 = meetings[i]
                m2 = meetings[j]
                if m1['start'] < m2['end'] and m2['start'] < m1['end']:
                    overlapping_indices.add(m1['idx'])
                    overlapping_indices.add(m2['idx'])

        visible_count = 0
        for i, t in enumerate(self.triage_stack):
            if i in overlapping_indices:
                color = OVERLAP_COLOR
            elif parse_meeting_time(t['line']):
                color = MEETING_COLOR
            elif '[]' in t['line']:
                color = "\033[1;36m"
            else:
                color = ""
            print(f"{i}: {color}{t['line']}\033[0m")
            for j, n in enumerate(t['notes']):
                n_color = "\033[1;36m" if '[]' in n else ""
                print(f"   {i}.{j}: {n_color}{n}\033[0m")
            visible_count += 1
        
        if visible_count == 0:
            print("\n\033[1;36m[FREE WRITE MODE]\033[0m Everything triaged or finished.")
        else:
            print("\nCmds: [p# #] reorder, [a# #] assign, [e#] edit, [w] free write, [i#] ignore, [N#] prioritize, [n#] add, [>>] defer all, [b#] break, [f] focus, [q] quit")

    def render_exit(self):
        summary = self.get_daily_summary()
        print(f"\n\033[1;32mDAILY SCORECARD ({os.path.basename(FILENAME)})\033[0m")

        # Finished [x]
        print(f"  Finished  [x]: {summary['top']['[x]'] + summary['sub']['[x]']}")
        print(f"    - Top-level: {summary['top']['[x]']}")
        print(f"    - Subtasks:  {summary['sub']['[x]']}")

        # Cancelled [-]
        print(f"  Cancelled [-]: {summary['top']['[-]'] + summary['sub']['[-]']}")
        print(f"    - Top-level: {summary['top']['[-]']}")
        print(f"    - Subtasks:  {summary['sub']['[-]']}")

        # Deferred [>]
        print(f"  Deferred  [>]: {summary['top']['[>]'] + summary['sub']['[>]']}")
        print(f"    - Top-level: {summary['top']['[>]']}")
        print(f"    - Subtasks:  {summary['sub']['[>]']}")

        print("="*35)
        self.last_msg = "Enter 'q' to quit or 'w' to return to Free Write..."

    def render_focus(self):
        if not self.triage_stack:
            return
        
        now = time.time()
        if self.task_start_time is None: self.task_start_time = now
        if self.focus_start_time is None: self.focus_start_time = now

        task_elapsed = int(now - self.task_start_time)
        tm, ts = divmod(task_elapsed, 60)

        focus_elapsed = int(now - self.focus_start_time)
        focus_remaining = self.focus_threshold - focus_elapsed
        f_sign = "-" if focus_remaining < 0 else ""
        fm, fs = divmod(abs(focus_remaining), 60)
        
        if not self.triage_stack:
            return
        top_task = self.triage_stack[0]
        focus_item, parent_item, focus_path = self._get_recursive_focus(top_task)

        # Handle "Task Started" ledger entry
        # We only log "Task Started" when the root task changes.
        root_id = top_task['line'].strip()
        if root_id != self.last_recorded_focus:
            if not focus_path:
                item_to_record = copy.deepcopy(focus_item)
                item_to_record['notes'] = [n for n in item_to_record['notes'] if not re.match(r'^\[[xe\->\s]?\]', n)]
                self.commit_to_ledger("Task Started", [item_to_record])
            else:
                # Build full path from top for ledger context
                item_to_record = copy.deepcopy(focus_item)
                item_to_record['notes'] = [n for n in item_to_record['notes'] if not re.match(r'^\[[xe\->\s]?\]', n)]

                hierarchical_context = self._get_path_pruned_item(top_task, focus_path, item_to_record)
                # Ensure root of this context is marked pending
                if not hierarchical_context['line'].strip().startswith('[]'):
                     hierarchical_context['line'] = re.sub(r'^(\s*)\[([xe\->\s]?)\]\s*', r'\1[] ', hierarchical_context['line'])
                     if not hierarchical_context['line'].strip().startswith('[]'):
                         hierarchical_context['line'] = f"[] {hierarchical_context['line'].lstrip()}"

                self.commit_to_ledger("Task Started", [hierarchical_context])
            self.last_recorded_focus = root_id

        t = focus_item
        meeting_time = parse_meeting_time(top_task['line'])
        meeting_timer_str = ""
        if meeting_time:
            now_dt = datetime.now()
            remaining = int((meeting_time[1] - now_dt).total_seconds())
            m_sign = "-" if remaining < 0 else ""
            mm, ms = divmod(abs(remaining), 60)
            meeting_timer_str = f" | Meeting: {m_sign}{mm:02d}:{ms:02d}"

        mini_timer_str = ""
        is_mini_session = False
        if self.mini_timer_active and self.triage_stack:
            is_mini_session = True
            sign = "-" if self.mini_timer_remaining < 0 else ""
            mm, ms = divmod(abs(self.mini_timer_remaining), 60)
            mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"

        task_timer_str = ""
        if not (meeting_timer_str and mini_timer_str):
            task_timer_str = f" | Task: {tm:02d}:{ts:02d}"

        color = "\033[1;34m"
        header = " MINI TASK SESSION " if is_mini_session else " FOCUS SESSION "
        if focus_elapsed > self.focus_threshold:
            color = "\033[1;31;7m"
            header = " !! BREAK TIME !! "

        is_task = t['line'].startswith('[]')
        print(color + "="*65 + "\033[0m")
        print(f"{color}{header}\033[0m{task_timer_str} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
        print(color + "="*65 + "\033[0m")
        
        if parent_item:
            parent_display = re.sub(r'^\[\s?\]\s*', '', parent_item['line'])
            print(f"\n\033[1;34mPARENT TASK >>\n{parent_display}\033[0m")

        # Progress Bar
        completed, total = self._get_progress_stats(focus_item, parent_item)
        if total > 0:
            p_bar = self._render_progress_bar(completed, total)
            if p_bar:
                print(f"\n\033[1;36m{p_bar}\033[0m")

        display_line = re.sub(r'^\[\s?\]\s*', '', t['line'])
        if is_task:
            print(f"\n\033[1;32mFOCUS >> {display_line}\033[0m")
        else:
            print(f"\n\033[1;32mFOCUS >> \033[0m{display_line}")
        for i, n in enumerate(t['notes']):
            n_color = "\033[1;36m" if '[]' in n else ""
            print(f"  {i}: {n_color}{n}\033[0m")
        print("\n" + color + "-"*65 + "\033[0m")
        extra_cmds = ", [Space] reset" if is_mini_session else ""
        print(f"Cmds: [x] done, [x#] subtask, [e] edit, [-] cancel, [>] defer, [>>] defer all, [w] free write, [m#] mini{extra_cmds}, [N#] prioritize, [n#] add, [i] ignore, [t] triage, [q] quit")

    def handle_command(self, cmd):
        self.last_msg = "" # Reset status message
        try:
            cmd_clean = re.sub(r'^([a-zA-Z])(\d)', r'\1 \2', cmd)
            try:
                parts = shlex.split(cmd_clean)
            except ValueError:
                # Handle unbalanced quotes by appending a closing quote if possible
                if '"' in cmd_clean:
                    try:
                        parts = shlex.split(cmd_clean + '"')
                        self.last_msg = "Note: Added missing closing quote."
                    except ValueError:
                        self.last_msg = "Error: Unbalanced quotes."
                        return
                else:
                    parts = cmd_clean.split()

            if self.mode == "EXIT":
                if not parts or parts[0].lower() == 'q':
                    return "QUIT"
                if parts[0].lower() == 'w':
                    self.enter_free_write()
                    return "REDRAW"
                return

            if not parts: return
            base_cmd_orig = parts[0]
            base_cmd = base_cmd_orig.lower()
            
            if base_cmd == 'q':
                if self.triage_stack:
                    # Restore terminal for input()
                    fd = sys.stdin.fileno()
                    if self.original_termios:
                        termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
                    print(f"\n\033[1;33m[!] Session Interrupted.\033[0m")
                    res = input("Rescue remaining tasks to Free Write? (y/n): ").lower()
                    tty.setcbreak(fd)
                    if res == 'y':
                        self._rescue_stack("Interrupted")
                    else:
                        self.commit_to_ledger("Interrupted", [])
                else:
                    if self.mode in ["FOCUS", "BREAK"]:
                        self.commit_to_ledger("Focus Session Complete", [])
                    else:
                        self.commit_to_ledger("Triage", [])
                self.mode = "EXIT"
                return "REDRAW"

            if base_cmd == 't': 
                self.commit_to_ledger("Triage Session Started at", [])
                self.sort_triage_stack()
                self.mode = "TRIAGE"; self.task_start_time = None
                self.break_start_time = None
                if self.focus_start_time is None: self.focus_start_time = time.time()
                return

            if base_cmd == 'w' and self.mode in ["FOCUS", "TRIAGE"]:
                self.enter_free_write()
                return "REDRAW"

            if (base_cmd == 'n' or base_cmd == 'N') and self.mode in ["FOCUS", "BREAK", "TRIAGE"]:
                target_idx = None
                # Check for n# or N# pattern which was split into ['n', '#'] or ['N', '#']
                if len(parts) > 1 and parts[1].isdigit():
                    target_idx = int(parts[1])
                    remaining_parts = parts[2:]
                else:
                    remaining_parts = parts[1:]

                items = []
                if remaining_parts is not None:
                    if remaining_parts:
                        # One-line addition
                        full_line = " ".join(remaining_parts)
                        m = re.match(r'^(\s*)', full_line)
                        indent_len = len(m.group(1))
                        content = full_line[indent_len:]
                        items = [{'line': content, 'notes': [], 'indent': indent_len}]
                    else:
                        context = None
                        if (self.mode in ["FOCUS", "BREAK"] or target_idx is not None) and self.triage_stack:
                            # Only show context if we are NOT targeting a specific index
                            # OR if the index is 0 (current focus)
                            if target_idx is None or target_idx == 0:
                                top_task = self.triage_stack[0]
                                focus_item, _, focus_path = self._get_recursive_focus(top_task)

                                # Building hierarchical context string (just the focused item)
                                context = []
                                if focus_path:
                                    # Find the indentation of the focus item
                                    indent = ""
                                    curr = top_task
                                    for idx in focus_path:
                                        sub, _ = self._get_subtask_as_item(curr, idx)
                                        # Every subtask is 2 spaces deeper than its parent line
                                        indent += "  "
                                        curr = sub
                                    context.append(f"{indent}{focus_item['line']}")
                            elif target_idx < len(self.triage_stack):
                                # Context for targeting a specific task at an index
                                target_task = self.triage_stack[target_idx]
                                context = [target_task['line']]

                        lines = self._get_multi_line_input(context_lines=context)
                        items = self._process_multi_line_input(lines)

                    if not items:
                        return

                # Delegate all addition logic to the hierarchical handler
                self._handle_hierarchical_new_items(base_cmd_orig, items, target_index=target_idx)

                if (base_cmd_orig == 'N' or target_idx is not None) and self.mode == "FOCUS":
                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0
                    self.check_meetings()

                self.initial_stack = copy.deepcopy(self.triage_stack)
                return

            if self.mode == "BREAK":
                if base_cmd == 'f':
                    self._transition_from_break_to_focus()
                    return
                elif base_cmd == 'b':
                    self.last_msg = "Break time overload! Doing nothing."
                    self.break_quote = random.choice(BREAK_QUOTES)
                    return
                elif base_cmd in ['n', 'N']:
                    pass # Handled by shared FOCUS/BREAK logic
                elif base_cmd in ['t', 'q']:
                    pass # Handled by common logic
                else:
                    self.last_msg = "Command disabled during break."
                    return

            if self.mode == "TRIAGE":
                if base_cmd == 'f':
                    now = datetime.now()
                    new_stack = []
                    for t in self.triage_stack:
                        m_time = parse_meeting_time(t['line'])
                        if m_time and m_time[1] < now:
                            # Meeting already ended
                            task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', t['line'])
                            t['line'] = f"[x] {task_content}"
                            t['notes'] = [f"[x] " + re.sub(r'^\[[xe\->\s]?\]\s*', '', n) for n in t['notes']]
                            self.commit_to_ledger("Meeting Auto-Completed", [t])
                            continue
                        new_stack.append(t)
                    self.triage_stack = new_stack

                    active = self.triage_stack
                    items_to_write = active if active != self.initial_stack else []
                    self.commit_to_ledger("Triage", items_to_write)
                    self.triage_stack = active
                    self.mode = "FOCUS"; self.last_msg = ""
                    if self.mini_timer_active:
                        self.mini_timer_last_tick = time.time()
                    self.last_chime_timestamp = 0
                    self.initial_stack = copy.deepcopy(self.triage_stack)
                elif base_cmd == 'i':
                    if len(parts) > 1:
                        idx = int(parts[1])
                    elif len(self.triage_stack) == 1:
                        idx = 0
                    else:
                        # Fallback to existing behavior if multiple items but no index
                        idx = int(parts[1])

                    item = self.triage_stack.pop(idx)
                    if item['line'].strip().startswith('[]'):
                        # It's a task, mark as cancelled
                        resolved_item = self._prepare_task_with_markers(item, '[-]', '[-]')
                        self.commit_to_ledger("Cancelled", [resolved_item])
                elif base_cmd == 'p':
                    src, dest = int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
                    self.triage_stack.insert(dest, self.triage_stack.pop(src))
                elif base_cmd == 'a':
                    src_str, dest_idx = parts[1], int(parts[2])
                    item = self.triage_stack[int(src_str.split('.')[0])]['notes'].pop(int(src_str.split('.')[1])) if '.' in src_str else self.triage_stack.pop(int(src_str))['line']
                    self.triage_stack[dest_idx]['notes'].append(item)
                elif base_cmd == 'e':
                    idx = int(parts[1]) if len(parts) > 1 else 0
                    if 0 <= idx < len(self.triage_stack):
                        self.triage_stack[idx] = self._edit_item(self.triage_stack[idx])
                        self.initial_stack = copy.deepcopy(self.triage_stack)

                elif base_cmd == 'b':
                    duration = 5
                    if len(parts) > 1:
                        try:
                            duration = int(parts[1])
                        except ValueError:
                            self.last_msg = f"Invalid break duration: {parts[1]}"
                            return

                    if duration <= 0:
                        self.last_msg = "Seriously? Take a real break! 0 minutes is too short."
                        return

                    self.mode = "BREAK"
                    self.break_meeting_interrupted = False
                    self.break_duration = duration
                    self.break_start_time = time.time()
                    self.break_quote = random.choice(BREAK_QUOTES)
                    self.last_chime_timestamp = 0
                    self.commit_to_ledger(f"Break for {duration} at", [])
                    return

                elif base_cmd in ['>', '>>']:
                    if self._handle_defer_command(base_cmd, parts):
                        return

            elif self.mode in ["FOCUS", "BREAK"]:
                if not self.triage_stack:
                    if base_cmd == 'q':
                        return "QUIT"
                    if base_cmd != 'n':
                        return

                top_task = self.triage_stack[0]
                focus_item, parent_item, focus_path = self._get_recursive_focus(top_task)
                is_note = not focus_item['line'].startswith('[]')

                if base_cmd == 'b' and self.mode == "FOCUS":
                    duration = 5
                    if len(parts) > 1:
                        try:
                            duration = int(parts[1])
                        except ValueError:
                            self.last_msg = f"Invalid break duration: {parts[1]}"
                            return

                    if duration <= 0:
                        self.last_msg = "Seriously? Take a real break! 0 minutes is too short."
                        return

                    self.mode = "BREAK"
                    self.break_meeting_interrupted = False
                    self.break_duration = duration
                    self.break_start_time = time.time()
                    self.break_quote = random.choice(BREAK_QUOTES)
                    self.last_chime_timestamp = 0
                    self.commit_to_ledger(f"Break for {duration} at", [])
                    return


                if base_cmd == 'e':
                    new_item = self._edit_item(focus_item)
                    if new_item != focus_item:
                        self._update_recursive_item(top_task, focus_path, new_item)
                        self.initial_stack = copy.deepcopy(self.triage_stack)
                    return

                if base_cmd == 'm' and self.mode == "FOCUS":
                    if len(parts) > 1:
                        try:
                            duration = int(parts[1])
                            if duration <= 0:
                                self.mini_timer_active = False
                                self.last_msg = "Mini Timer Stopped"
                            else:
                                self.mini_timer_active = True
                                self.mini_timer_duration = duration
                                self.mini_timer_remaining = duration * 60
                                self.mini_timer_last_tick = time.time()
                                self.mini_timer_last_chime_timestamp = 0
                                self.last_msg = f"Mini Timer Started: {duration}m"
                        except ValueError:
                            self.last_msg = f"Invalid mini timer duration: {parts[1]}"
                    else:
                        if self.mini_timer_active:
                            self.mini_timer_active = False
                            self.last_msg = "Mini Timer Stopped"
                        else:
                            self.mini_timer_active = True
                            self.mini_timer_duration = 2
                            self.mini_timer_remaining = 2 * 60
                            self.mini_timer_last_tick = time.time()
                            self.mini_timer_last_chime_timestamp = 0
                            self.last_msg = "Mini Timer Started: 2m"
                    return

                match_x = re.match(r'^x(\d+)', cmd)
                if match_x:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return
                    idx = int(match_x.group(1))
                    if 0 <= idx < len(focus_item['notes']):
                        focus_item['notes'][idx] = re.sub(r'^\[\s?\]', '[x]', focus_item['notes'][idx])
                        self._update_recursive_item(top_task, focus_path, focus_item)
                        if self.mini_timer_active:
                            self.mini_timer_remaining = self.mini_timer_duration * 60
                            self.mini_timer_last_tick = time.time()
                            self.mini_timer_last_chime_timestamp = 0
                    return

                if is_note and base_cmd in ['x', '-', 'i']:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return
                    # Notes are always top-level in triage_stack if they were returned as focus_item with empty path
                    if not focus_path:
                        self.triage_stack.pop(0)
                    else:
                        # This shouldn't happen with current recursive logic but let's be safe
                        pass
                    self.task_start_time = None
                    self.initial_stack = copy.deepcopy(self.triage_stack)
                    return

                if base_cmd in ['x', '-', '>', '>>', 'i']:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return

                    if base_cmd == '>>' or base_cmd == '>':
                        if self._handle_defer_command(base_cmd, parts):
                            return

                    effective_cmd = '-' if base_cmd == 'i' else base_cmd
                    marker = {'x': '[x]', '-': '[-]', '>': '[>]'}[effective_cmd]
                    ledger_label = {'x': 'Task Completed', '-': 'Task Cancelled', '>': 'Task Deferred'}[effective_cmd]

                    # Resolve item
                    resolved_item = self._prepare_task_with_markers(focus_item, marker, marker)
                    
                    if not focus_path:
                        item_to_record = self.triage_stack.pop(0)
                        # Ensure we commit the RESOLVED version
                        resolved_top = self._prepare_task_with_markers(item_to_record, marker, marker)
                        self.commit_to_ledger(ledger_label, [resolved_top])
                    else:
                        self._update_recursive_item(top_task, focus_path, resolved_item)
                        # Build full path from top for ledger context
                        hierarchical_context = self._get_path_pruned_item(top_task, focus_path, resolved_item)

                        # Ensure root of this context is marked pending if it's not the focused item
                        if not hierarchical_context['line'].startswith('[]') and not focus_path == []:
                             # Use regex to replace/add marker
                             hierarchical_context['line'] = re.sub(r'^(\s*)\[([xe\->\s]?)\]\s*', r'\1[] ', hierarchical_context['line'])
                             if not hierarchical_context['line'].strip().startswith('[]'):
                                 hierarchical_context['line'] = f"[] {hierarchical_context['line'].lstrip()}"

                        self.commit_to_ledger(ledger_label, [hierarchical_context])

                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0
                    self.task_start_time = None
                    self.initial_stack = copy.deepcopy(self.triage_stack)

                    if not self.triage_stack and self.mode == "FOCUS":
                        self.commit_to_ledger("Focus Session Complete", [])
                        self.mode = "EXIT"
                        return "REDRAW"

        except Exception as e:
            self.last_msg = f"Error: {e}"
        return None

if __name__ == "__main__":
    FocusCLI().run()
