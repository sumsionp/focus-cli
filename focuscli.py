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
    m1 = re.search(r'(\d{1,2}(?::\d{2})?)\s*(AM|PM)(?:\s*(\d+)H)?(?:\s*(\d+)M)?', text)
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

def parse_single_line(line):
    indent_match = re.match(r'^(\s*)', line)
    indent = len(indent_match.group(1)) if indent_match else 0
    clean = line.strip()

    header = Header.from_line(clean, indent)
    if header:
        return header

    meeting = Meeting.from_line(clean, indent)
    if meeting:
        return meeting

    task = Task.from_line(clean, indent)
    if task:
        return task

    return Note(clean, indent)

class Item:
    """Base class for anything in the ledger."""
    def __init__(self, content, indent=0):
        self.content = content
        self.indent = indent
        self.parent = None

    def to_ledger(self):
        """Returns the raw string for file writing."""
        return f"{' ' * self.indent}{self.content}"

    def __eq__(self, other):
        if not isinstance(other, Item):
            return False
        return self.to_ledger() == other.to_ledger()


class Note(Item):
    """A plain text entry with no state or children."""
    pass

class Task(Item):
    """An entry with a [ ] marker and potential sub-items."""
    REGEX = re.compile(r'^\[([xeB\->\s]?)\]\s*(.*)')

    def __init__(self, content, indent=0, state=' '):
        super().__init__(content, indent)
        self.state = state  # ' ', 'x', '-', '>', 'B', 'e'
        self.children = []  # List of Item objects (Notes or Tasks)

    @classmethod
    def from_line(cls, line, indent=0):
        clean = line.strip()
        match = cls.REGEX.match(clean)
        if match:
            state_char = match.group(1)
            state = state_char if state_char and not state_char.isspace() else ' '
            content = match.group(2)
            return cls(content, indent, state)
        return None

    @property
    def is_complete(self):
        return self.state == 'x'

    def to_ledger(self):
        state_str = self.state if self.state.strip() else ''
        marker = f"[{state_str}]"
        lines = [f"{' ' * self.indent}{marker} {self.content}"]
        for child in self.children:
            lines.append(child.to_ledger())
        return "\n".join(lines)


class Meeting(Task):
    """A task that specifically maps to a time window."""
    def __init__(self, content, indent=0, state=' ', start_time=None, end_time=None):
        super().__init__(content, indent, state)
        self.start_time = start_time
        self.end_time = end_time

    @classmethod
    def from_line(cls, line, indent=0):
        clean = line.strip()
        match = cls.REGEX.match(clean)
        if match:
            state_char = match.group(1)
            state = state_char if state_char and not state_char.isspace() else ' '
            content = match.group(2)

            m_time = parse_meeting_time(content)
            if m_time or state == 'B':
                start, end = m_time if m_time else (None, None)
                return cls(content, indent, state, start, end)
        return None

    def is_active(self, now=None):
        if now is None:
            now = datetime.now()
        if not self.start_time or not self.end_time:
            m_time = parse_meeting_time(self.content)
            if m_time:
                self.start_time, self.end_time = m_time
        if not self.start_time or not self.end_time:
            return False
        return self.start_time <= now < self.end_time

class Header(Item):
    """A ledger marker line like ------- LABEL TIMESTAMP -------"""
    REGEX = re.compile(r'^------- (.*?) ([0-9/:\sAPM]+) -------$')

    def __init__(self, label, timestamp, indent=0):
        super().__init__(label, indent)
        self.label = label
        self.timestamp = timestamp

    @classmethod
    def from_line(cls, line, indent=0):
        clean = line.strip()
        match = cls.REGEX.match(clean)
        if match:
            return cls(match.group(1).strip(), match.group(2).strip(), indent)

        if clean.startswith('-------') and clean.endswith('-------'):
            label = clean.strip('-').strip()
            return cls(label, "", indent)
        return None

    def to_ledger(self):
        return f"{' ' * self.indent}------- {self.label} {self.timestamp} -------"

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

    def _parse_single_line(self, line):
        return parse_single_line(line)

    def get_daily_summary(self):
        """Returns a dictionary of counts for top-level tasks and subtasks."""
        counts = {
            'top': {'[x]': 0, '[-]': 0, '[>]': 0},
            'sub': {'[x]': 0, '[-]': 0, '[>]': 0}
        }
        if not os.path.exists(FILENAME): return counts

        with open(FILENAME, 'r') as f:
            lines = f.readlines()

        latest_states = {} # full_path_key -> (state, level)
        stack = [] # current hierarchy of (content, indent)

        for line in lines:
            line_raw = line.rstrip('\n\r')
            if not line_raw.strip() or "-------" in line_raw:
                continue

            item = self._parse_single_line(line_raw)
            level = item.indent // 2

            while stack and stack[-1][1] >= item.indent:
                stack.pop()

            parent_path = " > ".join(c for c, i in stack)
            full_key = f"{parent_path} > {item.content}" if parent_path else item.content

            if isinstance(item, Task):
                latest_states[full_key] = (item.state, level)

            stack.append((item.content, item.indent))

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
            if isinstance(item, Meeting):
                if item.start_time and item.end_time and item.start_time <= now < item.end_time:
                    active_meetings.append(item)
                elif item.start_time:
                    inactive_meetings.append((item.start_time, item))
                else:
                    other_tasks.append(item)
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
                pending_tasks = [t for t in tasks_and_notes if isinstance(t, Task) and t.state == ' ']

                if pending_tasks:
                    # Mark as deferred in the old file
                    # Requirement: ------- Deferred to [Target Filename] <Timestamp> -------
                    label = f"Deferred to {FILENAME}"

                    # Prepare the deferred version for the old file
                    ledger_items = []
                    for task in pending_tasks:
                        # Current ledger version: main task [>], pending subtasks [>], others preserve
                        l_task = self._prepare_task_with_markers(task, '>', '>')
                        ledger_items.append(l_task)

                    self.commit_to_ledger(label, ledger_items, target_file=prev_file)

                    # Prepare the rescued tasks for today's file
                    # Requirement: Include full hierarchy of pending subtasks
                    for task in pending_tasks:
                        # Deep copy and strip meeting times
                        rescued_task = copy.deepcopy(task)
                        rescued_task.content = strip_meeting_time(rescued_task.content)
                        # Target version: main task [], subtasks preserve status
                        t_task = self._prepare_task_with_markers(rescued_task, ' ', ' ')
                        all_rescued_tasks.append(t_task)

        if all_rescued_tasks:
            self.commit_to_ledger("Deferred from last session", all_rescued_tasks)
            # Update in-memory stack
            self.triage_stack.extend(all_rescued_tasks)

    def _parse_file(self, filepath):
        """Parses a ledger file and returns a list of active Task and Note objects."""
        if not os.path.exists(filepath):
            return []

        with open(filepath, 'r') as f:
            lines = [l.rstrip() for l in f.readlines()]

        active_items = {} # (path_tuple) -> Item
        top_level_contents = [] # To preserve order
        current_path = [] # list of Item objects

        for line in lines:
            line_raw = line.rstrip()
            if not line_raw.strip(): continue

            if "------- Triage" in line_raw:
                new_top_level = []
                for content in top_level_contents:
                    key = (content,)
                    if key in active_items and isinstance(active_items[key], Task):
                        new_top_level.append(content)
                    else:
                        active_items.pop(key, None)
                top_level_contents = new_top_level
                continue

            if "-------" in line_raw: continue

            item = self._parse_single_line(line_raw)

            # Adjust current_path
            while current_path and current_path[-1].indent >= item.indent:
                current_path.pop()

            parent_path = tuple(i.content for i in current_path)
            full_path = parent_path + (item.content,)

            if isinstance(item, Task):
                if item.state == ' ':
                    # Pending task. Preserve children if already known.
                    if full_path in active_items:
                        existing = active_items[full_path]
                        if isinstance(existing, Task):
                            item.children = existing.children
                            for c in item.children: c.parent = item

                    active_items[full_path] = item
                    if not current_path:
                        if item.content not in top_level_contents:
                            top_level_contents.append(item.content)
                    else:
                        parent = current_path[-1]
                        if isinstance(parent, Task):
                            parent.children = [c for c in parent.children if c.content != item.content]
                            parent.children.append(item)
                            item.parent = parent
                    current_path.append(item)
                else:
                    # Resolution
                    active_items.pop(full_path, None)
                    if not current_path:
                        if item.content in top_level_contents:
                            top_level_contents.remove(item.content)
                    else:
                        parent = current_path[-1]
                        if isinstance(parent, Task):
                            parent.children = [c for c in parent.children if c.content != item.content]
            else:
                # Note
                if not current_path:
                    if item.content not in top_level_contents:
                        top_level_contents.append(item.content)
                else:
                    parent = current_path[-1]
                    if isinstance(parent, Task):
                         parent.children = [c for c in parent.children if not (isinstance(c, Note) and c.content == item.content)]
                         parent.children.append(item)
                         item.parent = parent
                active_items[full_path] = item

        return [active_items[(c,)] for c in top_level_contents if (c,) in active_items]

    def _get_multi_line_input(self, context_lines=None):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            tf.write("\n")
            tf.write("\n\n")
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

            if not current_item or indent_len <= current_item['indent']:
                current_item = {'line': content, 'notes': [], 'indent': indent_len}
                items.append(current_item)
            else:
                note_rel = line[current_item['indent'] + 2:] if len(line) >= current_item['indent'] + 2 else line.lstrip()
                current_item['notes'].append(note_rel)

        return items

    def _edit_item_obj(self, item):
        original_item = copy.deepcopy(item)
        content_lines = item.to_ledger().split('\n')

        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            tf.write("\n".join(content_lines))
            temp_path = tf.name

        try:
            self._run_with_vi([temp_path])
            with open(temp_path, 'r') as f:
                new_lines = [l.rstrip() for l in f.readlines() if l.strip()]

            if not new_lines: return item

            first_line = new_lines[0]
            new_item = self._parse_single_line(first_line)
            new_item.indent = original_item.indent

            if isinstance(new_item, Task):
                for line in new_lines[1:]:
                    child = self._parse_single_line(line)
                    child.parent = new_item
                    new_item.children.append(child)

            if new_item.to_ledger() != original_item.to_ledger():
                edited_old = copy.deepcopy(original_item)
                if isinstance(edited_old, Task):
                    edited_old.state = 'e'

                if isinstance(original_item, Task) and isinstance(new_item, Task):
                    new_item.state = ' '

                self.commit_to_ledger("Edited", [edited_old, new_item])
                self.last_msg = "Item Edited"
                return new_item

            return item
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _handle_defer_command_obj(self, base_cmd, parts):
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

        def prepare_defer(item):
            is_target_today = target_date.date() == datetime.now().date()
            today_str = datetime.now().strftime(DATE_FORMAT)
            is_current_file_today = today_str in FILENAME

            target = self._prepare_task_with_markers(item, ' ', ' ')
            ledger = self._prepare_task_with_markers(item, '>', '>')

            res = "today" if (is_target_today and is_current_file_today) else get_target_file(target_date)
            return ledger, target, res

        if base_cmd == '>>':
            count = len(self.triage_stack)
            while self.triage_stack:
                item = self.triage_stack.pop(0)
                l_item, t_item, res = prepare_defer(item)
                ledger_items.append(l_item)
                target_items.append(t_item)
                target_res = res

            if target_res == "today":
                self.commit_to_ledger("Deferred", ledger_items)
                self.triage_stack.extend(target_items)
            else:
                self.commit_to_ledger("Deferred from last session", target_items, target_file=target_res)
                self.commit_to_ledger(f"Deferred to {target_res}", ledger_items)
        else: # '>'
            item = self.triage_stack.pop(0)
            l_item, t_item, res = prepare_defer(item)
            if res == "today":
                self.commit_to_ledger("Deferred", [l_item])
                self.triage_stack.append(t_item)
            else:
                self.commit_to_ledger("Deferred from last session", [t_item], target_file=res)
                self.commit_to_ledger(f"Deferred to {res}", [l_item])

        self.commit_to_ledger("Triage", self.triage_stack)
        self.task_start_time = None
        self.initial_stack = copy.deepcopy(self.triage_stack)
        return True

    def _prepare_task_with_markers(self, item, main_state, pending_sub_state):
        """Helper to create a copy of a task with updated markers for pending items."""
        new_item = copy.deepcopy(item)

        def process_item(it, state):
            if isinstance(it, Task):
                if it.state == ' ':
                    it.state = state
                for child in it.children:
                    process_item(child, pending_sub_state)

        if isinstance(new_item, Task):
            if new_item.state == ' ':
                new_item.state = main_state
            for child in new_item.children:
                process_item(child, pending_sub_state)

        if main_state == '>' and isinstance(new_item, Task):
             new_item.content = strip_meeting_time(new_item.content)

        return new_item

    def _get_recursive_focus(self, item):
        """Recursively find the deepest pending task."""
        if not isinstance(item, Task):
            return item, None, []

        for i, child in enumerate(item.children):
            if isinstance(child, Task) and child.state == ' ':
                deep_item, deep_parent, deep_path = self._get_recursive_focus(child)
                if deep_parent is None:
                    return deep_item, item, [i]
                else:
                    return deep_item, deep_parent, [i] + deep_path

        return item, None, []

    def _update_recursive_item(self, top_item, path, new_sub_item):
        """Update a sub-item in the hierarchy recursively."""
        self._recursive_set(top_item, path, new_sub_item)

    def _recursive_set(self, item, path, new_sub_item):
        if not path:
            item.content = new_sub_item.content
            if isinstance(item, Task) and isinstance(new_sub_item, Task):
                item.state = new_sub_item.state
                item.children = new_sub_item.children
                for c in item.children: c.parent = item
            return

        idx = path[0]
        if isinstance(item, Task) and idx < len(item.children):
            self._recursive_set(item.children[idx], path[1:], new_sub_item)

    def _recursive_insert(self, item, path, new_items, position='before'):
        """Recursively insert items into the hierarchy relative to the focus path."""
        if not path:
            if not isinstance(item, Task): return True
            if position == 'append':
                for it in new_items:
                    it.parent = item
                    item.children.append(it)
                return False
            elif position == 'prepend_notes':
                # To maintain order [A, B] -> insert B at 0, then A at 0 -> [A, B, ...]
                for it in reversed(new_items):
                    it.parent = item
                    item.children.insert(0, it)
                return False
            else:
                return True # Signal to parent

        idx = path[0]
        if not isinstance(item, Task) or idx >= len(item.children):
            return False

        child = item.children[idx]

        if len(path) == 1 and position not in ['append', 'prepend_notes']:
            if position == 'before':
                for it in reversed(new_items):
                    it.parent = item
                    item.children.insert(idx, it)
            else: # 'after'
                for it in reversed(new_items):
                    it.parent = item
                    item.children.insert(idx + 1, it)
        else:
            self._recursive_insert(child, path[1:], new_items, position)

        return False

    def _handle_hierarchical_new_items(self, base_cmd_orig, raw_items, target_index=None):
        """Processes a batch of items and inserts them into the task tree."""
        if target_index is not None:
            mode_label = f"New Entry(s) at index {target_index}"
        else:
            mode_label = "Prioritized Entry(s)" if base_cmd_orig == 'N' else "New Entry(s)"

        any_changed = False

        def to_obj(raw, base_indent=0):
            obj = self._parse_single_line(raw['line'])
            obj.indent = raw['indent'] + base_indent
            if isinstance(obj, Task):
                for n in raw['notes']:
                    child = to_obj({'line': n, 'indent': 2, 'notes': []}, obj.indent)
                    child.parent = obj
                    obj.children.append(child)
            return obj

        items = [to_obj(it) for it in raw_items]
        top_level_items = [it for it in items if it.indent == 0]
        hier_items = [it for it in items if it.indent > 0]

        if hier_items and self.triage_stack:
            any_changed = True
            idx = target_index if target_index is not None else 0

            if idx < len(self.triage_stack):
                target_task = self.triage_stack[idx]

                if self.mode in ["TRIAGE"] or target_index is not None:
                    focus_path = []
                    focus_indents = [0]
                else:
                    _, _, focus_path = self._get_recursive_focus(target_task)
                    focus_indents = [0]
                    curr = target_task
                    for p_idx in focus_path:
                        focus_indents.append(focus_indents[-1] + 2)
                        curr = curr.children[p_idx]

                msg = "Sub-item(s) Added"
                if self.mode == "TRIAGE" or target_index is not None:
                    pos = 'prepend_notes' if base_cmd_orig == 'N' else 'append'
                    self._recursive_insert(target_task, focus_path, hier_items, position=pos)
                else:
                    items_by_depth = {}
                    for it in hier_items:
                        focus_indent = focus_indents[len(focus_path)]
                        depth_offset = (it.indent - focus_indent) // 2
                        target_depth = len(focus_path) + depth_offset
                        target_depth = max(0, min(len(focus_path) + 1, target_depth))

                        if target_depth > 0:
                            it.indent = it.indent - focus_indents[target_depth - 1] - 2

                        if target_depth not in items_by_depth:
                            items_by_depth[target_depth] = []
                        items_by_depth[target_depth].append(it)

                    for depth in sorted(items_by_depth.keys()):
                        depth_items = items_by_depth[depth]
                        for it in depth_items:
                             if depth <= len(focus_path):
                                 it.indent = focus_indents[depth]
                             else:
                                 it.indent = focus_indents[len(focus_path)] + 2

                        if depth == len(focus_path) + 1:
                            child_pos = 'append' if base_cmd_orig == 'n' else 'prepend_notes'
                            self._recursive_insert(target_task, focus_path, depth_items, position=child_pos)
                        else:
                            pos = 'before' if base_cmd_orig == 'N' else 'after'
                            target_path = focus_path[:depth]
                            self._recursive_insert(target_task, target_path, depth_items, position=pos)

                self.commit_to_ledger(mode_label, [target_task])
                self.last_recorded_focus = target_task.content.strip()
                if base_cmd_orig == 'N':
                    self.task_start_time = None

                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg

        if top_level_items:
            any_changed = True
            self.commit_to_ledger(mode_label, top_level_items)
            top_level_tasks = [it for it in top_level_items if isinstance(it, Task)]

            if target_index is not None:
                insert_idx = target_index
                if hier_items and target_index < len(self.triage_stack):
                    insert_idx += 1
                insert_idx = min(insert_idx, len(self.triage_stack))

                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)

                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0].content.strip()
                    self.task_start_time = None
                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"
            elif base_cmd_orig == 'N':
                insert_idx = 1 if (hier_items and self.triage_stack) else 0
                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)
                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0].content.strip()
                    self.task_start_time = None
                msg = "Task(s) Added & Prioritized" if top_level_tasks else "Note(s) Added & Prioritized"
            else:
                self.triage_stack.extend(top_level_tasks)
                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"

            self.last_msg = f"{msg} ({self.last_msg})" if self.last_msg.startswith("Note:") else msg

        return any_changed

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
            summary = self.get_daily_summary()
            completed = sum(summary['top'].values())
            pending = 0
            for it in self.triage_stack:
                if isinstance(it, Task) and it.state == ' ':
                    pending += 1
            total = completed + pending
        else:
            for child in parent_item.children:
                if isinstance(child, Task):
                    total += 1
                    if child.state in ['x', '-', '>']:
                        completed += 1

        return completed, total

    def _render_progress_bar(self, completed, total):
        if total == 0:
            return ""

        try:
            term_width = os.get_terminal_size().columns
        except OSError:
            term_width = 65

        label = f" Completed {completed}/{total}"
        max_bar_width = term_width - len(label) - 2
        if max_bar_width < 10:
             return f"[{completed}/{total}]"

        bar_width = min(40, max_bar_width)
        filled_width = int(round((completed / total) * bar_width))

        bar = "#" * filled_width + " " * (bar_width - filled_width)
        return f"[{bar}]{label}"

    def _get_path_pruned_item(self, item, path, leaf_item=None):
        """Returns a copy of item with hierarchy pruned to only show the path to focus."""
        if not path:
            return copy.deepcopy(leaf_item if leaf_item else item)

        new_item = copy.deepcopy(item)
        idx = path[0]

        if not isinstance(new_item, Task) or idx >= len(new_item.children):
            return new_item

        child = new_item.children[idx]
        pruned_child = self._get_path_pruned_item(child, path[1:], leaf_item)

        new_children = []
        for i, c in enumerate(new_item.children):
            if i == idx:
                new_children.append(pruned_child)
                pruned_child.parent = new_item
            elif isinstance(c, Note):
                new_children.append(c)

        new_item.children = new_children
        return new_item

    def commit_to_ledger(self, mode_label, items, target_file=None):
        dest = target_file if target_file else FILENAME
        with open(dest, 'a') as f:
            f.write(f"\n------- {mode_label} {get_timestamp()} -------\n")
            if items:
                for item in items:
                    f.write(f"{item.to_ledger()}\n")

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
        if sound == 'chime':
            linux_file = "/usr/share/sounds/freedesktop/stereo/complete.oga"
            macos_file = "/System/Library/Sounds/Glass.aiff"
        else:
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
                if subprocess.call(["which", cmd[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
            except Exception:
                continue
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
                is_meeting = isinstance(self.triage_stack[0], Meeting)
            if self.focus_start_time:
                focus_elapsed = now - self.focus_start_time
                if focus_elapsed >= self.focus_threshold:
                    if now - self.last_chime_timestamp >= 60:
                        if not is_meeting:
                            self.play_chime()
                        self.last_chime_timestamp = now

    def is_meeting_active(self):
        if not self.triage_stack: return False
        item = self.triage_stack[0]
        if isinstance(item, Meeting):
            if not item.start_time or not item.end_time:
                m_time = parse_meeting_time(item.content)
                if m_time:
                    item.start_time, item.end_time = m_time
            return item.is_active()
        return False

    def check_meetings(self):
        if self.mode not in ["FOCUS", "BREAK"]: return
        if not self.triage_stack: return

        now = datetime.now()
        found_active_meeting = False
        for i, item in enumerate(self.triage_stack):
            is_active_meeting = False
            if isinstance(item, Meeting):
                 is_active_meeting = item.is_active(now=now)

            if is_active_meeting:
                state_str = item.state if item.state.strip() else ''
                meeting_id = f"[{state_str}] {item.content}_{item.start_time}"
                if meeting_id not in self.chimed_meetings:
                    if self.mode == "BREAK":
                        self.break_meeting_interrupted = True
                    self.play_chime()
                    self.chimed_meetings.add(meeting_id)
                    self.last_msg = f"Meeting Starting: {item.content}"

                if self.mode == "FOCUS":
                    if i > 0 and not found_active_meeting:
                        current_item = self.triage_stack[0]
                        is_current_active_meeting = isinstance(current_item, Meeting) and current_item.is_active()

                        if not is_current_active_meeting:
                            self.triage_stack.insert(0, self.triage_stack.pop(i))
                            self.task_start_time = None
                            self.last_msg = f"Meeting Started: {self.triage_stack[0].content}"
                            found_active_meeting = True

                    if i == 0:
                        found_active_meeting = True

    def render_break(self):
        elapsed_break = time.time() - self.break_start_time
        remaining = int(self.break_duration * 60 - elapsed_break)
        sign = "-" if remaining < 0 else ""
        m, s = divmod(abs(remaining), 60)
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
        sys.stdout.write("\033[s")
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
            top_item = self.triage_stack[0]
            focus_elapsed = int(now - self.focus_start_time)
            focus_remaining = self.focus_threshold - focus_elapsed
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(focus_remaining), 60)
            meeting_timer_str = ""
            if isinstance(top_item, Meeting) and top_item.end_time:
                now_dt = datetime.now()
                remaining = int((top_item.end_time - now_dt).total_seconds())
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
        sys.stdout.write("\033[u")
        sys.stdout.flush()

    def _read_keypress(self, fd):
        """Reads a single keypress, escape sequence burst, or multi-byte UTF-8 character."""
        try:
            b = os.read(fd, 1)
            if not b: return None
            if (b[0] & 0x80) != 0 and b[0] != 0x1b:
                if (b[0] & 0xE0) == 0xC0: length = 2
                elif (b[0] & 0xF0) == 0xE0: length = 3
                elif (b[0] & 0xF8) == 0xF0: length = 4
                else: return b.decode('utf-8', errors='ignore')
                seq = b
                for _ in range(length - 1):
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if r:
                        next_b = os.read(fd, 1)
                        if not next_b: break
                        seq += next_b
                    else: break
                return seq.decode('utf-8', errors='ignore')
            if b == b'\x1b':
                seq = b
                while True:
                    r, _, _ = select.select([fd], [], [], 0.02)
                    if r:
                        next_b = os.read(fd, 1)
                        if not next_b: break
                        seq += next_b
                        if len(seq) >= 3 and seq[1:2] == b'[' and (0x40 <= seq[-1] <= 0x7E): break
                        if len(seq) == 3 and seq[1:2] == b'O': break
                        if len(seq) > 10: break
                    else: break
                return seq.decode('utf-8', errors='ignore')
            else: return b.decode('utf-8', errors='ignore')
        except Exception: return None

    def run(self):
        fd = sys.stdin.fileno()
        self.original_termios = termios.tcgetattr(fd)
        def signal_handler(sig, frame):
            self._rescue_stack("Interrupted (SIGTERM)")
            if self.original_termios: termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
            sys.exit(0)
        signal.signal(signal.SIGTERM, signal_handler)
        if not os.path.exists(FILENAME): self.rescue_previous_tasks()
        self.enter_free_write()
        self.focus_start_time = time.time()
        try:
            tty.setcbreak(fd)
            buffer = ""; cursor_pos = 0; last_render_second = -1; last_buffer = None
            last_cursor_pos = None; last_mode = None; last_msg = None; last_task = None
            last_expired = False; last_exceeded = False
            while True:
                now = time.time(); current_second = int(now)
                current_task = self.triage_stack[0] if self.triage_stack else None
                is_expired = False
                if self.mode == "BREAK":
                    elapsed_break = now - self.break_start_time
                    is_expired = (elapsed_break >= self.break_duration * 60)
                is_exceeded = False
                if self.mode == "FOCUS" and self.focus_start_time:
                    focus_elapsed = now - self.focus_start_time
                    is_exceeded = (focus_elapsed > self.focus_threshold)
                structural_change = (buffer != last_buffer or cursor_pos != last_cursor_pos or self.mode != last_mode or self.last_msg != last_msg or current_task != last_task or is_expired != last_expired or is_exceeded != last_exceeded)
                if structural_change:
                    sys.stdout.write("\033[H\033[2J")
                    if self.mode == "TRIAGE": self.render_triage()
                    elif self.mode == "FOCUS": self.render_focus()
                    elif self.mode == "BREAK": self.render_break()
                    elif self.mode == "EXIT": self.render_exit()
                    print(f"\n\033[90mStatus: {self.last_msg}\033[0m")
                    prompt = ">> "; sys.stdout.write(f"\033[1;37m{prompt}\033[0m{buffer}")
                    if cursor_pos < len(buffer):
                        move_back = len(buffer) - cursor_pos
                        sys.stdout.write(f"\033[{move_back}D")
                    sys.stdout.flush()
                    last_render_second = current_second; last_buffer = buffer; last_cursor_pos = cursor_pos
                    last_mode = self.mode; last_msg = self.last_msg; last_task = copy.deepcopy(current_task)
                    last_expired = is_expired; last_exceeded = is_exceeded
                elif current_second != last_render_second:
                    if self.mode in ["FOCUS", "BREAK", "TRIAGE"]: self.update_timer_ui()
                    last_render_second = current_second
                if self.mode in ["FOCUS", "BREAK", "TRIAGE"]: self.check_chime()
                if self.mode in ["FOCUS", "BREAK"]:
                    self.check_meetings()
                    if self.mode == "FOCUS": self.update_mini_timer()
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
                        cmd = buffer.strip(); buffer = ""
                        if not cmd and self.mode != "EXIT":
                            last_mode = None; cursor_pos = 0; continue
                        termios.tcsetattr(fd, termios.TCSANOW, self.original_termios); print()
                        result = self.handle_command(cmd); tty.setcbreak(fd); cursor_pos = 0
                        if result == "QUIT": print(); break
                        if result == "REDRAW": last_mode = None
                        continue
                    elif char in ['\x7f', '\x08']:
                        if cursor_pos > 0: buffer = buffer[:cursor_pos-1] + buffer[cursor_pos:]; cursor_pos -= 1
                    elif char == '\x03': raise KeyboardInterrupt
                    elif char.startswith('\x1b'):
                        seq = char
                        if seq in ['\x1b[D', '\x1bOD']:
                            if cursor_pos > 0: cursor_pos -= 1
                        elif seq in ['\x1b[C', '\x1bOC']:
                            if cursor_pos < len(buffer): cursor_pos += 1
                        elif seq in ['\x1b[H', '\x1b[1~', '\x1bOH']: cursor_pos = 0
                        elif seq in ['\x1b[F', '\x1b[4~', '\x1bOF']: cursor_pos = len(buffer)
                        elif seq in ['\x1b[3~']:
                            if cursor_pos < len(buffer): buffer = buffer[:cursor_pos] + buffer[cursor_pos+1:]
                    elif char == '\x01': cursor_pos = 0
                    elif char == '\x05': cursor_pos = len(buffer)
                    elif char == '\x04':
                        if cursor_pos < len(buffer): buffer = buffer[:cursor_pos] + buffer[cursor_pos+1:]
                    elif len(char) == 1 and ord(char) >= 32:
                        buffer = buffer[:cursor_pos] + char + buffer[cursor_pos:]; cursor_pos += 1
        except KeyboardInterrupt: self._rescue_stack("Interrupted")
        finally: termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

    def render_triage(self):
        now = time.time()
        focus_elapsed = int(now - (self.focus_start_time if self.focus_start_time else now))
        focus_remaining = self.focus_threshold - focus_elapsed
        f_sign = "-" if focus_remaining < 0 else ""; fm, fs = divmod(abs(focus_remaining), 60)
        f_color = "\033[1;31m" if focus_remaining <= 0 else ""
        timer_str = f" | Focus: {f_color}{f_sign}{fm:02d}:{fs:02d}\033[0m"
        print(f"--- TRIAGE: {os.path.basename(FILENAME)}{timer_str} ---")
        meetings = []
        for i, item in enumerate(self.triage_stack):
            if isinstance(item, Meeting) and item.start_time and item.end_time:
                meetings.append({'idx': i, 'start': item.start_time, 'end': item.end_time})
        overlapping_indices = set()
        for i in range(len(meetings)):
            for j in range(i + 1, len(meetings)):
                m1 = meetings[i]; m2 = meetings[j]
                if m1['start'] < m2['end'] and m2['start'] < m1['end']:
                    overlapping_indices.add(m1['idx']); overlapping_indices.add(m2['idx'])
        visible_count = 0
        for i, item in enumerate(self.triage_stack):
            if i in overlapping_indices: color = OVERLAP_COLOR
            elif isinstance(item, Meeting): color = MEETING_COLOR
            elif isinstance(item, Task): color = "\033[1;36m"
            else: color = ""
            display_line = item.to_ledger().split('\n')[0].strip()
            print(f"{i}: {color}{display_line}\033[0m")
            if isinstance(item, Task):
                for j, child in enumerate(item.children):
                    n_color = "\033[1;36m" if isinstance(child, Task) and child.state == ' ' else ""
                    child_display = child.to_ledger().split('\n')[0].strip()
                    print(f"   {i}.{j}: {n_color}{child_display}\033[0m")
            visible_count += 1
        if visible_count == 0: print("\n\033[1;36m[FREE WRITE MODE]\033[0m Everything triaged or finished.")
        else: print("\nCmds: [p# #] reorder, [a# #] assign, [e#] edit, [w] free write, [i#] ignore, [N#] prioritize, [n#] add, [>>] defer all, [b#] break, [f] focus, [q] quit")

    def render_exit(self):
        summary = self.get_daily_summary()
        print(f"\n\033[1;32mDAILY SCORECARD ({os.path.basename(FILENAME)})\033[0m")
        print(f"  Finished  [x]: {summary['top']['[x]'] + summary['sub']['[x]']}")
        print(f"    - Top-level: {summary['top']['[x]']}")
        print(f"    - Subtasks:  {summary['sub']['[x]']}")
        print(f"  Cancelled [-]: {summary['top']['[-]'] + summary['sub']['[-]']}")
        print(f"    - Top-level: {summary['top']['[-]']}")
        print(f"    - Subtasks:  {summary['sub']['[-]']}")
        print(f"  Deferred  [>]: {summary['top']['[>]'] + summary['sub']['[>]']}")
        print(f"    - Top-level: {summary['top']['[>]']}")
        print(f"    - Subtasks:  {summary['sub']['[>]']}")
        print("="*35)
        self.last_msg = "Enter 'q' to quit or 'w' to return to Free Write..."

    def render_focus(self):
        if not self.triage_stack: return
        now = time.time()
        if self.task_start_time is None: self.task_start_time = now
        if self.focus_start_time is None: self.focus_start_time = now
        task_elapsed = int(now - self.task_start_time); tm, ts = divmod(task_elapsed, 60)
        focus_elapsed = int(now - self.focus_start_time)
        focus_remaining = self.focus_threshold - focus_elapsed
        f_sign = "-" if focus_remaining < 0 else ""; fm, fs = divmod(abs(focus_remaining), 60)
        top_item = self.triage_stack[0]
        focus_item, parent_item, focus_path = self._get_recursive_focus(top_item)
        root_id = top_item.to_ledger().split('\n')[0].strip()
        if root_id != self.last_recorded_focus:
            if not focus_path:
                item_to_record = copy.deepcopy(focus_item)
                if isinstance(item_to_record, Task):
                    item_to_record.children = [c for c in item_to_record.children if not (isinstance(c, Task) and c.state != ' ')]
                self.commit_to_ledger("Task Started", [item_to_record])
            else:
                item_to_record = copy.deepcopy(focus_item)
                if isinstance(item_to_record, Task):
                    item_to_record.children = [c for c in item_to_record.children if not (isinstance(c, Task) and c.state != ' ')]
                hierarchical_context = self._get_path_pruned_item(top_item, focus_path, item_to_record)
                if isinstance(hierarchical_context, Task): hierarchical_context.state = ' '
                self.commit_to_ledger("Task Started", [hierarchical_context])
            self.last_recorded_focus = root_id
        t = focus_item
        meeting_timer_str = ""
        if isinstance(top_item, Meeting) and top_item.end_time:
            now_dt = datetime.now(); remaining = int((top_item.end_time - now_dt).total_seconds())
            m_sign = "-" if remaining < 0 else ""; mm, ms = divmod(abs(remaining), 60)
            meeting_timer_str = f" | Meeting: {m_sign}{mm:02d}:{ms:02d}"
        mini_timer_str = ""; is_mini_session = False
        if self.mini_timer_active and self.triage_stack:
            is_mini_session = True; sign = "-" if self.mini_timer_remaining < 0 else ""
            mm, ms = divmod(abs(self.mini_timer_remaining), 60); mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"
        task_timer_str = ""
        if not (meeting_timer_str and mini_timer_str):
            task_elapsed = int(now - self.task_start_time); tm, ts = divmod(task_elapsed, 60); task_timer_str = f" | Task: {tm:02d}:{ts:02d}"
        color = "\033[1;34m"; header = " MINI TASK SESSION " if is_mini_session else " FOCUS SESSION "
        if focus_elapsed > self.focus_threshold: color = "\033[1;31;7m"; header = " !! BREAK TIME !! "
        is_task = isinstance(t, Task); print(color + "="*65 + "\033[0m")
        print(f"{color}{header}\033[0m{task_timer_str} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
        print(color + "="*65 + "\033[0m")
        if parent_item:
            parent_display = parent_item.content; print(f"\n\033[1;34mPARENT TASK >>\n{parent_display}\033[0m")
        completed, total = self._get_progress_stats(focus_item, parent_item)
        if total > 0:
            p_bar = self._render_progress_bar(completed, total)
            if p_bar: print(f"\n\033[1;36m{p_bar}\033[0m")
        display_line = t.content
        if is_task: print(f"\n\033[1;32mFOCUS >> {display_line}\033[0m")
        else: print(f"\n\033[1;32mFOCUS >> \033[0m{display_line}")
        if isinstance(t, Task):
            for i, child in enumerate(t.children):
                n_color = "\033[1;36m" if isinstance(child, Task) and child.state == ' ' else ""
                child_display = child.to_ledger().split('\n')[0].strip()
                print(f"  {i}: {n_color}{child_display}\033[0m")
        print("\n" + color + "-"*65 + "\033[0m")
        extra_cmds = ", [Space] reset" if is_mini_session else ""
        print(f"Cmds: [x] done, [x#] subtask, [e] edit, [-] cancel, [>] defer, [>>] defer all, [w] free write, [m#] mini{extra_cmds}, [N#] prioritize, [n#] add, [i] ignore, [t] triage, [q] quit")

    def handle_command(self, cmd):
        self.last_msg = ""
        try:
            cmd_clean = re.sub(r'^([a-zA-Z])(\d)', r'\1 \2', cmd)
            try: parts = shlex.split(cmd_clean)
            except ValueError:
                if '"' in cmd_clean:
                    try: parts = shlex.split(cmd_clean + '"'); self.last_msg = "Note: Added missing closing quote."
                    except ValueError: self.last_msg = "Error: Unbalanced quotes."; return
                else: parts = cmd_clean.split()
            if self.mode == "EXIT":
                if not parts or parts[0].lower() == 'q': return "QUIT"
                if parts[0].lower() == 'w': self.enter_free_write(); return "REDRAW"
                return
            if not parts: return
            base_cmd_orig = parts[0]; base_cmd = base_cmd_orig.lower()
            if base_cmd == 'q':
                if self.triage_stack:
                    fd = sys.stdin.fileno()
                    if self.original_termios: termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
                    print(f"\n\033[1;33m[!] Session Interrupted.\033[0m")
                    res = input("Rescue remaining tasks to Free Write? (y/n): ").lower(); tty.setcbreak(fd)
                    if res == 'y': self._rescue_stack("Interrupted")
                    else: self.commit_to_ledger("Interrupted", [])
                else:
                    if self.mode in ["FOCUS", "BREAK"]: self.commit_to_ledger("Focus Session Complete", [])
                    else: self.commit_to_ledger("Triage", [])
                self.mode = "EXIT"; return "REDRAW"
            if base_cmd == 't': 
                self.commit_to_ledger("Triage Session Started at", []); self.sort_triage_stack()
                self.mode = "TRIAGE"; self.task_start_time = None; self.break_start_time = None
                if self.focus_start_time is None: self.focus_start_time = time.time()
                return
            if base_cmd == 'w' and self.mode in ["FOCUS", "TRIAGE"]: self.enter_free_write(); return "REDRAW"
            if (base_cmd == 'n' or base_cmd == 'N') and self.mode in ["FOCUS", "BREAK", "TRIAGE"]:
                target_idx = None
                if len(parts) > 1 and parts[1].isdigit(): target_idx = int(parts[1]); remaining_parts = parts[2:]
                else: remaining_parts = parts[1:]
                items = []
                if remaining_parts is not None:
                    if remaining_parts:
                        full_line = " ".join(remaining_parts); m = re.match(r'^(\s*)', full_line)
                        indent_len = len(m.group(1)); content = full_line[indent_len:]
                        items = [{'line': content, 'notes': [], 'indent': indent_len}]
                    else:
                        context = None
                        if (self.mode in ["FOCUS", "BREAK"] or target_idx is not None) and self.triage_stack:
                            if target_idx is None or target_idx == 0:
                                top_item = self.triage_stack[0]; focus_item, _, focus_path = self._get_recursive_focus(top_item)
                                context = []
                                if focus_path:
                                    indent = ""; curr = top_item
                                    for idx in focus_path: indent += "  "; curr = curr.children[idx]
                                    context.append(f"{indent}{focus_item.to_ledger().strip()}")
                            elif target_idx < len(self.triage_stack):
                                target_task = self.triage_stack[target_idx]; context = [target_task.to_ledger().strip()]
                        lines = self._get_multi_line_input(context_lines=context)
                        items = self._process_multi_line_input(lines)
                    if not items: return
                self._handle_hierarchical_new_items(base_cmd_orig, items, target_index=target_idx)
                if (base_cmd_orig == 'N' or target_idx is not None) and self.mode == "FOCUS":
                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time(); self.mini_timer_last_chime_timestamp = 0
                    self.check_meetings()
                self.initial_stack = copy.deepcopy(self.triage_stack); return
            if self.mode == "BREAK":
                if base_cmd == 'f': self._transition_from_break_to_focus(); return
                elif base_cmd == 'b': self.last_msg = "Break time overload! Doing nothing."; self.break_quote = random.choice(BREAK_QUOTES); return
                elif base_cmd in ['n', 'N']: pass
                elif base_cmd in ['t', 'q']: pass
                else: self.last_msg = "Command disabled during break."; return
            if self.mode == "TRIAGE":
                if base_cmd == 'f':
                    now = datetime.now(); new_stack = []
                    for item in self.triage_stack:
                        if isinstance(item, Meeting) and item.end_time and item.end_time < now:
                            item.state = 'x'
                            for child in item.children:
                                if isinstance(child, Task): child.state = 'x'
                            self.commit_to_ledger("Meeting Auto-Completed", [item]); continue
                        new_stack.append(item)
                    self.triage_stack = new_stack; active = self.triage_stack
                    items_to_write = active if active != self.initial_stack else []
                    self.commit_to_ledger("Triage", items_to_write)
                    self.triage_stack = active; self.mode = "FOCUS"; self.last_msg = ""
                    if self.mini_timer_active: self.mini_timer_last_tick = time.time()
                    self.last_chime_timestamp = 0; self.initial_stack = copy.deepcopy(self.triage_stack)
                elif base_cmd == 'i':
                    idx = int(parts[1]) if len(parts) > 1 else (0 if len(self.triage_stack) == 1 else None)
                    if idx is not None:
                        item = self.triage_stack.pop(idx)
                        if isinstance(item, Task): resolved_item = self._prepare_task_with_markers(item, '-', '-'); self.commit_to_ledger("Cancelled", [resolved_item])
                elif base_cmd == 'p':
                    src, dest = int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
                    self.triage_stack.insert(dest, self.triage_stack.pop(src))
                elif base_cmd == 'a':
                    src_str, dest_idx = parts[1], int(parts[2])
                    if '.' in src_str:
                        p_idx, c_idx = map(int, src_str.split('.'))
                        item = self.triage_stack[p_idx].children.pop(c_idx); item.parent = self.triage_stack[dest_idx]
                        self.triage_stack[dest_idx].children.append(item)
                    else:
                        item = self.triage_stack.pop(int(src_str)); item.parent = self.triage_stack[dest_idx]
                        self.triage_stack[dest_idx].children.append(item)
                elif base_cmd == 'e':
                    idx = int(parts[1]) if len(parts) > 1 else 0
                    if 0 <= idx < len(self.triage_stack):
                        self.triage_stack[idx] = self._edit_item_obj(self.triage_stack[idx])
                        self.initial_stack = copy.deepcopy(self.triage_stack)
                elif base_cmd == 'b':
                    duration = 5
                    if len(parts) > 1:
                        try: duration = int(parts[1])
                        except ValueError: self.last_msg = f"Invalid break duration: {parts[1]}"; return
                    if duration <= 0: self.last_msg = "Seriously? Take a real break! 0 minutes is too short."; return
                    self.mode = "BREAK"; self.break_meeting_interrupted = False; self.break_duration = duration
                    self.break_start_time = time.time(); self.break_quote = random.choice(BREAK_QUOTES)
                    self.last_chime_timestamp = 0; self.commit_to_ledger(f"Break for {duration} at", []); return
                elif base_cmd in ['>', '>>']:
                    if self._handle_defer_command_obj(base_cmd, parts): return
            elif self.mode in ["FOCUS", "BREAK"]:
                if not self.triage_stack:
                    if base_cmd == 'q': return "QUIT"
                    if base_cmd != 'n': return
                top_item = self.triage_stack[0]; focus_item, parent_item, focus_path = self._get_recursive_focus(top_item)
                is_note = isinstance(focus_item, Note)
                if base_cmd == 'b' and self.mode == "FOCUS":
                    duration = 5
                    if len(parts) > 1:
                        try: duration = int(parts[1])
                        except ValueError: self.last_msg = f"Invalid break duration: {parts[1]}"; return
                    if duration <= 0: self.last_msg = "Seriously? Take a real break! 0 minutes is too short."; return
                    self.mode = "BREAK"; self.break_meeting_interrupted = False; self.break_duration = duration
                    self.break_start_time = time.time(); self.break_quote = random.choice(BREAK_QUOTES)
                    self.last_chime_timestamp = 0; self.commit_to_ledger(f"Break for {duration} at", []); return
                if base_cmd == 'e':
                    new_item = self._edit_item_obj(focus_item)
                    if new_item != focus_item:
                        self._update_recursive_item(top_item, focus_path, new_item)
                        self.initial_stack = copy.deepcopy(self.triage_stack)
                    return
                if base_cmd == 'm' and self.mode == "FOCUS":
                    if len(parts) > 1:
                        try:
                            duration = int(parts[1])
                            if duration <= 0: self.mini_timer_active = False; self.last_msg = "Mini Timer Stopped"
                            else:
                                self.mini_timer_active = True; self.mini_timer_duration = duration; self.mini_timer_remaining = duration * 60
                                self.mini_timer_last_tick = time.time(); self.mini_timer_last_chime_timestamp = 0
                                self.last_msg = f"Mini Timer Started: {duration}m"
                        except ValueError: self.last_msg = f"Invalid mini timer duration: {parts[1]}"
                    else:
                        if self.mini_timer_active: self.mini_timer_active = False; self.last_msg = "Mini Timer Stopped"
                        else:
                            self.mini_timer_active = True; self.mini_timer_duration = 2; self.mini_timer_remaining = 2 * 60
                            self.mini_timer_last_tick = time.time(); self.mini_timer_last_chime_timestamp = 0
                            self.last_msg = "Mini Timer Started: 2m"
                    return
                match_x = re.match(r'^x(\d+)', cmd)
                if match_x:
                    if self.mode == "BREAK": self.last_msg = "Command disabled during break."; return
                    idx = int(match_x.group(1))
                    if isinstance(focus_item, Task) and 0 <= idx < len(focus_item.children):
                        child = focus_item.children[idx]
                        if isinstance(child, Task):
                            child.state = 'x'
                            self._update_recursive_item(top_item, focus_path, focus_item)
                            if self.mini_timer_active:
                                self.mini_timer_remaining = self.mini_timer_duration * 60
                                self.mini_timer_last_tick = time.time(); self.mini_timer_last_chime_timestamp = 0
                    return
                if is_note and base_cmd in ['x', '-', 'i']:
                    if self.mode == "BREAK": self.last_msg = "Command disabled during break."; return
                    if not focus_path: self.triage_stack.pop(0)
                    else: pass
                    self.task_start_time = None; self.initial_stack = copy.deepcopy(self.triage_stack); return
                if base_cmd in ['x', '-', '>', '>>', 'i']:
                    if self.mode == "BREAK": self.last_msg = "Command disabled during break."; return
                    if base_cmd == '>>' or base_cmd == '>':
                        if self._handle_defer_command_obj(base_cmd, parts): return
                    effective_cmd = '-' if base_cmd == 'i' else base_cmd
                    marker = 'x' if effective_cmd == 'x' else ('-' if effective_cmd == '-' else '>')
                    ledger_label = 'Task Completed' if effective_cmd == 'x' else ('Task Cancelled' if effective_cmd == '-' else 'Task Deferred')
                    resolved_item = self._prepare_task_with_markers(focus_item, marker, marker)
                    if not focus_path:
                        item_to_record = self.triage_stack.pop(0)
                        resolved_top = self._prepare_task_with_markers(item_to_record, marker, marker)
                        self.commit_to_ledger(ledger_label, [resolved_top])
                    else:
                        self._update_recursive_item(top_item, focus_path, resolved_item)
                        hierarchical_context = self._get_path_pruned_item(top_item, focus_path, resolved_item)
                        if isinstance(hierarchical_context, Task) and focus_path != []: hierarchical_context.state = ' '
                        self.commit_to_ledger(ledger_label, [hierarchical_context])
                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time(); self.mini_timer_last_chime_timestamp = 0
                    self.task_start_time = None; self.initial_stack = copy.deepcopy(self.triage_stack)
                    if not self.triage_stack and self.mode == "FOCUS":
                        self.commit_to_ledger("Focus Session Complete", []); self.mode = "EXIT"; return "REDRAW"
        except Exception as e: self.last_msg = f"Error: {e}"
        return None

if __name__ == "__main__":
    FocusCLI().run()
