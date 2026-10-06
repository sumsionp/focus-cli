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
DEFAULT_FOCUS_THRESHOLD_MINS = 25
ALERT_THRESHOLD = DEFAULT_FOCUS_THRESHOLD_MINS * 60
CHIME_COMMAND = None # Set to a command string like "play /path/to/sound.wav" to override
MEETING_COLOR = "\033[1;32m" # Green
OVERLAP_COLOR = "\033[1;31m" # Red

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

class Item:
    """Base class for anything in the ledger."""
    def __init__(self, content, indent=0):
        self.content = content
        self.indent = indent
        self.parent = None

    @staticmethod
    def from_lines(lines):
        """Builds a tree of items from a list of indented lines using a stack."""
        root_items = []
        stack = [] # list of Item objects

        for line in lines:
            line_raw = line.rstrip()
            if not line_raw.strip(): continue

            item = ItemFactory.from_line(line_raw)

            # Adjust current_path
            while stack and stack[-1].indent >= item.indent:
                stack.pop()

            if not stack:
                root_items.append(item)
            else:
                parent = stack[-1]
                if isinstance(parent, Task):
                    item.parent = parent
                    parent.children.append(item)

            stack.append(item)
        return root_items

    def to_ledger(self):
        """Returns the raw string for file writing."""
        return f"{' ' * self.indent}{self.content}"

    def __eq__(self, other):
        if not isinstance(other, Item):
            return False
        return self.to_ledger() == other.to_ledger()


class Note(Item):
    """A plain text entry with no state or children."""
    @classmethod
    def from_line(cls, line, indent=0):
        return cls(line, indent)

class Task(Item):
    """An entry with a [ ] marker and potential sub-items."""
    REGEX = re.compile(r'^\[([xe\->\s]?)\]\s*(.*)')

    def __init__(self, content, indent=0, state=' '):
        super().__init__(content, indent)
        self.state = state  # ' ', 'x', '-', '>', 'e'
        self.children = []  # List of Item objects (Notes or Tasks)

    @classmethod
    def _parse_common(cls, line):
        match = cls.REGEX.match(line)
        if match:
            state_char = match.group(1)
            state = state_char if state_char and not state_char.isspace() else ' '
            content = match.group(2)
            return state, content
        return None, None

    @classmethod
    def parse_duration(cls, text):
        """Parses standalone duration pattern like 60m, 1h, 1h30m, 1h 30m from text."""
        m = re.search(r'\b(?:(\d+)\s*H\s*(\d+)\s*M|(\d+)\s*H|(\d+)\s*M)\b', text, re.IGNORECASE)
        if m:
            if m.group(1) is not None and m.group(2) is not None:
                return int(m.group(1)) * 60 + int(m.group(2))
            elif m.group(3) is not None:
                return int(m.group(3)) * 60
            elif m.group(4) is not None:
                return int(m.group(4))
        return None

    @classmethod
    def from_line(cls, line, indent=0):
        state, content = cls._parse_common(line)
        if content is not None:
            return cls(content, indent, state)
        return None

    @property
    def is_complete(self):
        return self.state in ['x', '-', '>', 'e']

    @property
    def is_pending(self):
        return self.state in [' ']

    def clone_with_state(self, main_state, pending_sub_state):
        """Helper to create a copy of a task with updated markers for pending items."""
        new_item = copy.deepcopy(self)

        def process_item(it, state):
            if isinstance(it, Task):
                if it.state == ' ':
                    it.state = state
                for child in it.children:
                    process_item(child, pending_sub_state)

        if new_item.state == ' ':
            new_item.state = main_state
        for child in new_item.children:
            process_item(child, pending_sub_state)

        if main_state == '>':
             new_item.content = strip_meeting_time(new_item.content)

        return new_item

    def to_ledger(self):
        state_str = self.state if self.state.strip() else ''
        marker = f"[{state_str}]"
        lines = [f"{' ' * self.indent}{marker} {self.content}"]
        for child in self.children:
            lines.append(child.to_ledger())
        return "\n".join(lines)

class Meeting(Task):
    """A task that specifically maps to a time window."""
    def __init__(self, content, indent=0, state=' ', start_time=None, end_time=None, duration=None):
        super().__init__(content, indent, state)
        self.start_time = start_time
        self.end_time = end_time
        self.duration = duration

    def get_meeting_id(self):
        """Returns a unique identifier for this meeting instance based on content and time."""
        state_str = self.state if self.state.strip() else ''
        return f"[{state_str}] {self.content}_{self.start_time}"

    def _clone_to_task(self, target_state):
        """Internal helper to clone a meeting/break into a Task."""
        new_content = strip_meeting_time(self.content)
        new_task = Task(new_content, self.indent, target_state)
        new_task.children = copy.deepcopy(self.children)
        for child in new_task.children:
            child.parent = new_task
        return new_task

    def to_task(self):
        """Converts this meeting to a regular Task, stripping time patterns."""
        return self._clone_to_task(self.state)

    def reschedule(self, time_str):
        """Updates meeting time attributes based on a time string."""
        # Use our existing parsing logic which is robust
        m_time = self.parse_meeting_time(time_str)
        if m_time:
            self.start_time, self.end_time, self.duration = m_time
            # Update content to reflect new time
            base_content = strip_meeting_time(self.content)
            self.content = f"{base_content} {self.start_time.strftime('%I:%M')}-{self.end_time.strftime('%I:%M %p')}"
            return True

        # Try just start time (keep duration)
        now = datetime.now()
        # Regex for just a time like "2 PM" or "2:30 PM"
        m1 = re.search(r'^(\d{1,2}(?::\d{2})?)\s*(AM|PM)$', time_str.upper().strip())
        if m1:
            start_dt = self._parse_time_with_ampm(m1.group(1), m1.group(2), now)
            duration = self.duration if self.duration else 60
            self.start_time = start_dt
            self.end_time = start_dt + timedelta(minutes=duration)
            self.duration = duration
            base_content = strip_meeting_time(self.content)
            self.content = f"{base_content} {self.start_time.strftime('%I:%M')}-{self.end_time.strftime('%I:%M %p')}"
            return True

        return False

    @classmethod
    def from_attributes(cls, content, indent, state, start_time=None, end_time=None, duration=None):
        if start_time and end_time:
            duration = (end_time - start_time) // timedelta(minutes=1)
        elif start_time and duration:
            end_time = start_time + timedelta(minutes=duration)
        elif end_time and duration:
            start_time = end_time - timedelta(minutes=duration)
        else:
            return None

        content = f"{content} {start_time.strftime('%I:%M')}-{end_time.strftime('%I:%M %p')}"

        return cls(content, indent, state, start_time, end_time, duration)

    @classmethod
    def from_line(cls, line, indent=0):
        state, content = cls._parse_common(line)
        if content:
            m_time = cls.parse_meeting_time(content)
            if m_time:
                start, end, duration = m_time
                return cls(content, indent, state, start, end, duration)
        return None

    @classmethod
    def parse_meeting_time(cls, text):
        now = datetime.now()
        text = text.upper()

        # 1. Check for 2 PM 2h 15m format
        m1 = re.search(r'(\d{1,2}(?::\d{2})?)\s*(AM|PM)(?:\s*(\d+)H)?(?:\s*(\d+)M)?', text)
        if m1 and (m1.group(3) or m1.group(4)):
            start_time_str = m1.group(1)
            ampm = m1.group(2)
            hours = int(m1.group(3)) if m1.group(3) else 0
            minutes = int(m1.group(4)) if m1.group(4) else 0

            start_dt = cls._parse_time_with_ampm(start_time_str, ampm, now)
            end_dt = start_dt + timedelta(hours=hours, minutes=minutes)
            return start_dt, end_dt, (end_dt - start_dt) // timedelta(minutes=1)

        # 2. Check for 11:00 AM-1:00 PM format
        m2 = re.search(r'(\d{1,2}(?::\d{2})?)\s*(AM|PM)\s*-\s*(\d{1,2}(?::\d{2})?)\s*(AM|PM)', text)
        if m2:
            start_dt = cls._parse_time_with_ampm(m2.group(1), m2.group(2), now)
            end_dt = cls._parse_time_with_ampm(m2.group(3), m2.group(4), now)
            return start_dt, end_dt, (end_dt - start_dt) // timedelta(minutes=1)

        # 3. Check for 2:00-3:00 PM or 2-3 PM format
        m3 = re.search(r'(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)\s*(AM|PM)', text)
        if m3:
            end_time_str = m3.group(2)
            ampm = m3.group(3)
            end_dt = cls._parse_time_with_ampm(end_time_str, ampm, now)

            start_time_str = m3.group(1)
            start_dt = cls._parse_time_with_ampm(start_time_str, ampm, now)

            if start_dt > end_dt:
                alt_ampm = 'AM' if ampm == 'PM' else 'PM'
                start_dt = cls._parse_time_with_ampm(start_time_str, alt_ampm, now)

            return start_dt, end_dt, (end_dt - start_dt) // timedelta(minutes=1)

        return None

    @classmethod
    def _parse_time_with_ampm(cls, time_str, ampm, reference_date):
        if ':' in time_str:
            h, m = map(int, time_str.split(':'))
        else:
            h = int(time_str)
            m = 0

        if ampm == 'PM' and h < 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0

        # Handle crossing midnight if the time is earlier than the reference date
        dt = reference_date.replace(hour=h, minute=m, second=0, microsecond=0)
        if dt < reference_date - timedelta(hours=10):
             dt += timedelta(days=1)
        elif dt > reference_date + timedelta(hours=14):
             dt -= timedelta(days=1)
        return dt

    def is_active(self, now=None):
        if now is None:
            now = datetime.now()
        if not self.start_time or not self.end_time:
            return False
        return self.start_time <= now < self.end_time

class Break(Meeting):
    """A meeting designed to act like a break, both scheduled and immediate"""
    REGEX = re.compile(r'^\[(B)\]\s*(.*)')

    def to_task(self):
        """Converts this break to a regular Task, stripping time patterns."""
        return self._clone_to_task(' ') # Break -> Task uses [ ]

    def clone_with_state(self, main_state, pending_sub_state):
        """Override to handle the [B] marker specifically, ensuring it's treated as a pending state."""
        # Task.clone_with_state creates a deep copy first, then updates it
        new_item = super().clone_with_state(main_state, pending_sub_state)
        # If the state was successfully updated from 'B' to the resolution marker, return it
        if new_item.state == main_state:
             return new_item

        # Otherwise, Task.clone_with_state didn't know how to handle 'B'
        if new_item.state == 'B':
             new_item.state = main_state

        return new_item

    @classmethod
    def from_line(cls, line, indent=0):
        state, content = cls._parse_common(line)
        if content:
            # First try parent Meeting parser
            meeting = super().from_line(line, indent)
            if meeting:
                 return cls(content, indent, state, meeting.start_time, meeting.end_time, meeting.duration)

            # If no time schedule, check for duration pattern
            duration = cls.parse_duration(content)
            return cls(content, indent, state, duration=duration)
        return None

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

    @classmethod
    def random_quote(cls):
        return random.choice(cls.BREAK_QUOTES)

    @property
    def is_pending(self):
        return self.state in ['B'] or super().is_pending
      
    @classmethod
    def from_attributes(cls, content, start_time=None, end_time=None, duration=None):
        return super().from_attributes(content, 0, 'B', start_time, end_time, duration)

class ItemFactory():
    """Determines what type of Item object is returned based on a line"""

    @classmethod
    def from_line(cls, line):
        indent_match = re.match(r'^(\s*)', line)
        indent = len(indent_match.group(1)) if indent_match else 0
        clean = line.strip()

        header = Header.from_line(clean, indent)
        if header:
            return header

        break_item = Break.from_line(clean, indent)
        if break_item:
            return break_item

        meeting = Meeting.from_line(clean, indent)
        if meeting:
            return meeting

        task = Task.from_line(clean, indent)
        if task:
            return task

        return Note(clean, indent)

class Header(Item):
    """A ledger marker line like ------- LABEL TIMESTAMP -------"""
    REGEX = re.compile(r'^------- (.*?) ([0-9/:\sAPM]+) -------$')

    def __init__(self, label, timestamp, indent=0):
        super().__init__(label, indent)
        self.label = label
        self.timestamp = timestamp

    @classmethod
    def from_line(cls, line, indent=0):
        match = cls.REGEX.match(line)
        if match:
            return cls(match.group(1).strip(), match.group(2).strip(), indent)

        if line.startswith('-------') and line.endswith('-------'):
            label = line.strip('-').strip()
            return cls(label, "", indent)
        return None

    def to_ledger(self):
        return f"{' ' * self.indent}------- {self.label} {self.timestamp} -------"


class BaseTimer:
    """Base class for all timing logic."""
    def __init__(self):
        self.start_time = None
        self.is_active = False

    def start(self, start_time=None):
        self.start_time = start_time if start_time else time.time()
        self.is_active = True

    def stop(self):
        self.is_active = False

    def elapsed(self):
        if not self.start_time:
            return 0
        return time.time() - self.start_time


class Stopwatch(BaseTimer):
    """Simple elapsed time tracker (e.g., Task Timer)."""
    pass


class ThresholdTimer(BaseTimer):
    """Tracks elapsed time against a threshold (e.g., Focus Timer)."""
    def __init__(self, threshold_seconds):
        super().__init__()
        self.threshold = threshold_seconds

    def is_exceeded(self):
        return self.elapsed() > self.threshold

    def remaining(self):
        return self.threshold - self.elapsed()


class CountdownTimer(BaseTimer):
    """Tracks remaining time from a duration (e.g., Mini Timer)."""
    def __init__(self, duration_seconds=0):
        super().__init__()
        self.duration = duration_seconds
        self.remaining_seconds = duration_seconds
        self.last_tick = 0
        self.last_chime_timestamp = 0

    def start(self, duration_seconds=None):
        if duration_seconds is not None:
            self.duration = duration_seconds
            self.remaining_seconds = duration_seconds
        self.last_tick = time.time()
        self.last_chime_timestamp = 0
        self.is_active = True

    def tick(self):
        if not self.is_active:
            return
        now = time.time()
        if self.last_tick == 0:
            self.last_tick = now
        elapsed = now - self.last_tick
        if elapsed >= 1.0:
            ticks = int(elapsed)
            self.remaining_seconds -= ticks
            self.last_tick += ticks

    def reset(self, duration_seconds=None):
        if duration_seconds is not None:
            self.duration = duration_seconds
        self.remaining_seconds = self.duration
        self.last_tick = time.time()
        self.last_chime_timestamp = 0

    def pause(self):
        """Sets last_tick to current time to 'pause' any accumulated drift when logic stops/starts."""
        self.last_tick = time.time()

    def should_chime(self, interval_seconds=30):
        if self.is_active and self.remaining_seconds <= 0:
            now = time.time()
            if now - self.last_chime_timestamp >= interval_seconds:
                self.last_chime_timestamp = now
                return True
        return False



class Chimer(CountdownTimer):
    """Specialized timer for meeting alerts that repeat every 15s."""
    def __init__(self):
        super().__init__(duration_seconds=0)
        self.meeting_name = ""

    def load(self, meeting_name):
        self.meeting_name = meeting_name
        self.start(0)
        self.last_chime_timestamp = time.time()

    def stop(self):
        self.is_active = False

    def should_chime(self, interval_seconds=15):
        return super().should_chime(interval_seconds)

class TimerManager:
    """Encapsulates all timer-related state and logic."""
    def __init__(self, focus_threshold_seconds):
        self.task_timer = Stopwatch()
        self.focus_timer = ThresholdTimer(focus_threshold_seconds)
        self.mini_timer = CountdownTimer()
        self.chimer = Chimer()
        self.last_chime_timestamp = 0

    def update(self, mode):
        if mode == "FOCUS":
            self.mini_timer.tick()
            self.chimer.tick()
        elif mode == "BREAK":
            self.chimer.tick()

    def should_chime(self, interval_seconds=60, update_timestamp=True):
        now = time.time()
        if now - self.last_chime_timestamp >= interval_seconds:
            if update_timestamp:
                self.last_chime_timestamp = now
            return True
        return False

    def reset_chime(self, offset=0):
        if offset == 0:
            self.last_chime_timestamp = 0
        else:
            self.last_chime_timestamp = time.time() - offset



class TaskStack:
    """Manages the separation of focus_queue and meeting_timeline."""
    def __init__(self):
        self.focus_queue = []
        self.meeting_timeline = []

    def populate(self, items):
        """Sorts a list of items into the queue and timeline."""
        self.focus_queue = []
        self.meeting_timeline = []
        now = datetime.now()

        # No Takeover Rule:
        # 1. Any active meeting already at items[0] stays in focus_queue
        # 2. Other active meetings go to meeting_timeline

        active_focus = []
        active_due = []
        future_meetings = []
        regular_items = []

        # Deduplicate while preserving order to prevent an item from being in both lists
        seen_ids = set()
        unique_items = []
        for item in items:
            if id(item) not in seen_ids:
                unique_items.append(item)
                seen_ids.add(id(item))

        for i, item in enumerate(unique_items):
            if isinstance(item, Meeting):
                if item.is_active(now=now):
                    if i == 0:
                        active_focus.append(item)
                    else:
                        active_due.append(item)
                elif item.start_time and item.start_time > now:
                    future_meetings.append(item)
                else:
                    regular_items.append(item)
            else:
                regular_items.append(item)

        self.focus_queue = active_focus + regular_items
        self.meeting_timeline = active_due + future_meetings
        self.meeting_timeline.sort(key=lambda m: m.start_time)

    def get_focus_queue(self): return self.focus_queue
    def get_meeting_timeline(self): return self.meeting_timeline
    def get_all(self): return self.focus_queue + self.meeting_timeline
    def __len__(self): return len(self.get_all())
    def __getitem__(self, i): return self.get_all()[i]
    def __setitem__(self, i, val):
        items = self.get_all()
        items[i] = val
        self.populate(items)
    def __iter__(self): return iter(self.get_all())
    def __bool__(self): return len(self) > 0
    def pop(self, i=-1):
        items = self.get_all()
        item = items.pop(i)
        self.populate(items)
        return item
    def insert(self, i, item):
        items = self.get_all()
        items.insert(i, item)
        self.populate(items)
    def append(self, item):
        items = self.get_all()
        items.append(item)
        self.populate(items)
    def extend(self, items_to_add):
        items = self.get_all()
        items.extend(items_to_add)
        self.populate(items)
    def __iadd__(self, other):
        self.extend(other)
        return self
    def __add__(self, other):
        return self.get_all() + list(other)
    def __eq__(self, other):
        if isinstance(other, list): return self.get_all() == other
        return id(self) == id(other)

    def check_for_due_meetings(self):
        """Returns the first meeting from the timeline if it is currently active."""
        if not self.meeting_timeline: return None
        now = datetime.now()
        if self.meeting_timeline[0].is_active(now=now):
            return self.meeting_timeline[0]
        return None

    def promote_due_meetings(self):
        """Moves active meetings from the timeline to the focus position."""
        now = datetime.now()
        promoted = []
        while self.meeting_timeline and self.meeting_timeline[0].is_active(now=now):
             due_meeting = self.meeting_timeline.pop(0)
             self.focus_queue.insert(0, due_meeting)
             promoted.append(due_meeting)
        return promoted[0] if promoted else None

class Command:
    """Base class for all CLI commands."""
    def __init__(self, parts, original_cmd=""):
        self.parts = parts
        self.original_cmd = original_cmd

    def execute(self, cli):
        """Execute the command logic against the FocusCLI instance."""
        raise NotImplementedError


class QuitCommand(Command):
    def execute(self, cli):
        if cli.mode == "EXIT":
            return "QUIT"

        if cli.triage_stack:
            cli.commit_to_ledger("Triage", cli.triage_stack)
        else:
            if cli.mode in ["FOCUS", "BREAK"]:
                cli.commit_to_ledger("Focus Session Complete", [])
            else:
                cli.commit_to_ledger("Triage", [])
        cli.mode = "EXIT"
        return "REDRAW"


class TriageCommand(Command):
    def execute(self, cli):
        cli.commit_to_ledger("Triage Session Started at", [])
        cli.sort_triage_stack()
        cli.mode = "TRIAGE"
        cli.timers.task_timer.stop()
        if not cli.timers.focus_timer.is_active:
            cli.timers.focus_timer.start()


class FreeWriteCommand(Command):
    def execute(self, cli):
        if cli.mode in ["FOCUS", "TRIAGE", "EXIT"]:
            cli.enter_free_write()
            return "REDRAW"


class AddCommand(Command):
    def execute(self, cli):
        base_cmd_orig = self.parts[0]
        base_cmd = base_cmd_orig.lower()
        target_idx = None
        if len(self.parts) > 1 and self.parts[1].isdigit():
            target_idx = int(self.parts[1])
            remaining_parts = self.parts[2:]
        else:
            remaining_parts = self.parts[1:]

        items = []
        if remaining_parts is not None:
            if len(remaining_parts) == 1 and not re.search(r'\s', remaining_parts[0]):
                template_name = os.path.basename(remaining_parts[0])
                template_dir = cli.templates_dir
                if not os.path.exists(template_dir):
                    os.makedirs(template_dir)
                template_path = os.path.join(template_dir, f"{template_name}.txt")

                initial_content = None
                exists = os.path.exists(template_path)
                if exists:
                    with open(template_path, 'r') as f:
                        initial_content = f.read()

                lines = cli._get_multi_line_input(
                    initial_content=initial_content,
                    start_insert=not exists,
                    add_open_line=not exists
                )

                # Filter out blank lines to check if we have actual content
                has_content = any(l.strip() for l in lines)
                if has_content:
                    # Save back to template
                    with open(template_path, 'w') as f:
                        f.write("\n".join(lines) + "\n")
                    items = cli._process_multi_line_input(lines)
                else:
                    if os.path.exists(template_path):
                        os.remove(template_path)
                    cli.last_msg = "Empty template discarded."
                    return
            elif remaining_parts:
                full_line = " ".join(remaining_parts)
                items = cli._process_multi_line_input([full_line])
            else:
                context = None
                if (cli.mode in ["FOCUS", "BREAK"] or target_idx is not None) and cli.triage_stack:
                    if target_idx is None or target_idx == 0:
                        top_item = cli.triage_stack[0]
                        focus_item, _, focus_path = cli._get_recursive_focus(top_item)
                        context = []
                        if focus_path:
                            indent = ""
                            curr = top_item
                            for idx in focus_path:
                                indent += "  "
                                curr = curr.children[idx]
                            context.append(f"{indent}{focus_item.to_ledger().strip()}")
                    elif target_idx < len(cli.triage_stack):
                        target_task = cli.triage_stack[target_idx]
                        context = [target_task.to_ledger().strip()]
                lines = cli._get_multi_line_input(context_lines=context)
                items = cli._process_multi_line_input(lines)

            if not items:
                return

        cli._handle_hierarchical_new_items(base_cmd_orig, items, target_index=target_idx)
        if (base_cmd_orig == 'N' or target_idx is not None) and cli.mode == "FOCUS":
            if cli.timers.mini_timer.is_active:
                cli.timers.mini_timer.reset(cli.mini_timer_duration * 60)
            cli.check_meetings()
        cli.initial_stack = copy.deepcopy(cli.triage_stack)


class FocusCommand(Command):
    def execute(self, cli):
        if cli.mode == "BREAK":
            is_break_obj = cli.triage_stack and isinstance(cli.triage_stack[0], Break)
            if is_break_obj:
                break_item = cli.triage_stack.pop(0)
                break_item.state = 'x'
                cli.commit_to_ledger("Break Completed", [break_item])
                cli._transition_from_break_to_focus(break_item=break_item)
            else:
                cli._transition_from_break_to_focus()
            return "REDRAW"
        elif cli.mode == "TRIAGE":
            now = datetime.now()
            new_stack = []
            for item in cli.triage_stack:
                if isinstance(item, Meeting) and item.end_time and item.end_time < now:
                    item.state = 'x'
                    for child in item.children:
                        if isinstance(child, Task):
                            child.state = 'x'
                    cli.commit_to_ledger("Meeting Auto-Completed", [item])
                    continue
                new_stack.append(item)
            cli.triage_stack = new_stack
            active = cli.triage_stack
            items_to_write = active if active != cli.initial_stack else []
            cli.commit_to_ledger("Triage", items_to_write)
            cli.mode = "FOCUS"
            cli.last_msg = ""
            if cli.timers.mini_timer.is_active:
                cli.timers.mini_timer.pause()
            cli.initial_stack = copy.deepcopy(cli.triage_stack)


class ResolveCommand(Command):
    def execute(self, cli):
        base_cmd = self.parts[0].lower()
        if not cli.triage_stack:
            return

        has_index = len(self.parts) > 1 and (self.parts[1].isdigit() or '.' in self.parts[1])

        if has_index:
            target_item, parent_item, target_path = cli._get_item_by_hierarchical_index(self.parts[1])
            if target_item is None:
                return "REDRAW"
            top_level_idx = int(self.parts[1].split('.')[0])
            top_item = cli.triage_stack[top_level_idx]
        else:
            top_item = cli.triage_stack[0]
            if cli.mode in ["FOCUS", "BREAK"]:
                target_item, parent_item, target_path = cli._get_recursive_focus(top_item)
            else:
                # TRIAGE mode default
                target_item, parent_item, target_path = top_item, None, []

        is_note = isinstance(target_item, Note)

        if is_note and base_cmd in ['x', '-', 'i']:
            cli.last_msg = "Invalid action for a note."
            return "REDRAW"

        if base_cmd in ['x', '-', 'i']:
            effective_cmd = '-' if base_cmd == 'i' else base_cmd
            marker = 'x' if effective_cmd == 'x' else ('-' if effective_cmd == '-' else '>')
            ledger_label = 'Task Completed' if effective_cmd == 'x' else (
                'Task Cancelled' if effective_cmd == '-' else 'Task Deferred')

            resolved_item = target_item.clone_with_state(marker, marker) if isinstance(target_item, Task) else target_item

            is_top_level = not target_path
            if is_top_level:
                # If target_path is empty, we are resolving a top-level item in triage_stack
                # Use the calculated top_level_idx if we had an index, else 0
                idx_to_pop = top_level_idx if has_index else 0
                item_to_record = cli.triage_stack.pop(idx_to_pop)
                if idx_to_pop == 0:
                    cli.triage_stack.promote_due_meetings()

                resolved_top = item_to_record.clone_with_state(marker, marker) if isinstance(item_to_record, Task) else item_to_record
                cli.commit_to_ledger(ledger_label, [resolved_top])
            else:
                cli._update_recursive_item(top_item, target_path, resolved_item)
                hierarchical_context = cli._get_path_pruned_item(top_item, target_path, resolved_item)
                if isinstance(hierarchical_context, Task) and target_path != []:
                    hierarchical_context.state = ' '
                cli.commit_to_ledger(ledger_label, [hierarchical_context])

            # Logic for when the focused task was resolved
            if not has_index or top_level_idx == 0:
                if cli.timers.mini_timer.is_active:
                    cli.timers.mini_timer.reset(cli.mini_timer_duration * 60)
                cli.timers.task_timer.stop()

            cli.initial_stack = copy.deepcopy(cli.triage_stack)

            if not cli.triage_stack and cli.mode == "FOCUS":
                cli.commit_to_ledger("Focus Session Complete", [])
                cli.mode = "EXIT"
                return "REDRAW"

            if cli.mode in ["FOCUS", "BREAK"]:
                cli.check_meetings()

            is_break_obj = isinstance(resolved_item, Break)
            if cli.mode == "BREAK" and is_break_obj and (not has_index or top_level_idx == 0):
                cli._transition_from_break_to_focus(break_item=resolved_item)
                return "REDRAW"


class EditCommand(Command):
    def execute(self, cli):
        if not cli.triage_stack:
            return

        has_index = len(self.parts) > 1 and (self.parts[1].isdigit() or '.' in self.parts[1])

        if has_index:
            target_item, parent_item, target_path = cli._get_item_by_hierarchical_index(self.parts[1])
            if target_item is None:
                return "REDRAW"
            top_level_idx = int(self.parts[1].split('.')[0])
            top_item = cli.triage_stack[top_level_idx]
        else:
            top_item = cli.triage_stack[0]
            if cli.mode in ["FOCUS", "BREAK"]:
                target_item, parent_item, target_path = cli._get_recursive_focus(top_item)
            else:
                # TRIAGE mode default
                target_item, parent_item, target_path = top_item, None, []

        new_item = cli._edit_item_obj(target_item)
        if new_item != target_item:
            if not target_path:
                # Top level edit
                idx = top_level_idx if has_index else 0
                cli.triage_stack[idx] = new_item
            else:
                cli._update_recursive_item(top_item, target_path, new_item)
            cli.initial_stack = copy.deepcopy(cli.triage_stack)

        return "REDRAW"


class DeferCommand(Command):
    def execute(self, cli):
        base_cmd = self.parts[0].lower()
        if not cli.triage_stack:
            return

        remaining = " ".join(self.parts[1:]).strip()

        # 1. Check for date/file deferral (>> or > tomorrow)
        # Try parsing as date first to avoid confusing YYYYMMDD with index
        target_date = None
        if base_cmd == '>>':
            target_date = parse_defer_date(remaining)
        elif remaining:
            target_date = parse_defer_date(remaining)

        # 2. Parse target index if provided (e.g., >1, >-1)
        target_idx = None
        if target_date is None and base_cmd == '>' and len(self.parts) == 2:
            arg = self.parts[1]
            if arg.isdigit() or (arg.startswith('-') and arg[1:].isdigit()):
                target_idx = int(arg)
                if target_idx <= 0:
                    target_idx = 1

        if target_date:
            ledger_items = []
            target_items = []
            target_res = None

            def prepare_defer(item):
                is_target_today = target_date.date() == datetime.now().date()
                today_str = datetime.now().strftime(DATE_FORMAT)
                is_current_file_today = today_str in cli.filename

                if isinstance(item, Task):
                    target = item.clone_with_state(' ', ' ')
                    ledger = item.clone_with_state('>', '>')
                else:
                    target = copy.deepcopy(item)
                    ledger = copy.deepcopy(item)

                res = "today" if (is_target_today and is_current_file_today) else get_target_file(target_date)
                return ledger, target, res

            if base_cmd == '>>':
                while cli.triage_stack:
                    item = cli.triage_stack.pop(0)
                    l_item, t_item, res = prepare_defer(item)
                    ledger_items.append(l_item)
                    target_items.append(t_item)
                    target_res = res

                if target_res == "today":
                    cli.commit_to_ledger("Deferred", ledger_items)
                    cli.triage_stack.extend(target_items)
                else:
                    cli.commit_to_ledger("Deferred from last session", target_items, target_file=target_res)
                    cli.commit_to_ledger(f"Deferred to {target_res}", ledger_items)
            else: # '>'
                item = cli.triage_stack.pop(0)
                l_item, t_item, res = prepare_defer(item)
                if res == "today":
                    cli.commit_to_ledger("Deferred", [l_item])
                    cli.triage_stack.append(t_item)
                else:
                    cli.commit_to_ledger("Deferred from last session", [t_item], target_file=res)
                    cli.commit_to_ledger(f"Deferred to {res}", [l_item])

            cli.commit_to_ledger("Triage", cli.triage_stack)
            cli.timers.task_timer.stop()
            cli.initial_stack = copy.deepcopy(cli.triage_stack)
            return

        # 3. Handle Meeting/Break rescheduling or conversion
        top_item = cli.triage_stack[0]
        if isinstance(top_item, Meeting) and base_cmd == '>':
            # Reschedule if we have a string that wasn't a date or a single index
            if remaining and target_idx is None:
                old_meeting = copy.deepcopy(top_item)
                if top_item.reschedule(remaining):
                    old_meeting.state = 'e'
                    cli.commit_to_ledger("Rescheduled", [old_meeting, top_item])
                    cli.last_msg = "Meeting Rescheduled"
                    cli.timers.task_timer.stop()
                    cli.initial_stack = copy.deepcopy(cli.triage_stack)
                    cli.triage_stack.populate(cli.triage_stack.get_all())
                    return

            # If rescheduling failed OR no time string was provided (just > or >#)
            # Convert to Task
            old_meeting = copy.deepcopy(top_item)
            old_meeting.state = 'e'
            new_item = top_item.to_task()
            cli.triage_stack.pop(0)
            cli.triage_stack.promote_due_meetings()
            cli.commit_to_ledger("Converted to Task", [old_meeting, new_item])
            cli.last_msg = "Converted Meeting to Task"

            if target_idx is not None:
                if target_idx >= len(cli.triage_stack):
                    cli.triage_stack.append(new_item)
                else:
                    cli.triage_stack.insert(target_idx, new_item)
            else:
                cli.triage_stack.append(new_item)

            cli.commit_to_ledger("Triage", cli.triage_stack)
            cli.timers.task_timer.stop()
            cli.initial_stack = copy.deepcopy(cli.triage_stack)
            cli.triage_stack.populate(cli.triage_stack.get_all())
            return

        # 4. Handle regular Task Deferral
        if base_cmd == '>':
            item = cli.triage_stack.pop(0)
            cli.triage_stack.promote_due_meetings()
            if target_idx is not None:
                if target_idx >= len(cli.triage_stack):
                    cli.triage_stack.append(item)
                else:
                    cli.triage_stack.insert(target_idx, item)
            else:
                # Default single defer: to the end of the focus_queue
                cli.triage_stack.append(item)

            cli.commit_to_ledger("Triage", cli.triage_stack)
            cli.timers.task_timer.stop()
            cli.initial_stack = copy.deepcopy(cli.triage_stack)
            return

class MiniTimerCommand(Command):
    def execute(self, cli):
        if cli.mode != "FOCUS":
            return
        if len(self.parts) > 1:
            try:
                duration = int(self.parts[1])
                if duration <= 0:
                    cli.timers.mini_timer.stop()
                    cli.last_msg = "Mini Timer Stopped"
                else:
                    cli.timers.mini_timer.start(duration * 60)
                    cli.mini_timer_duration = duration
                    cli.last_msg = f"Mini Timer Started: {duration}m"
            except ValueError:
                cli.last_msg = f"Invalid mini timer duration: {self.parts[1]}"
        else:
            if cli.timers.mini_timer.is_active:
                cli.timers.mini_timer.stop()
                cli.last_msg = "Mini Timer Stopped"
            else:
                cli.timers.mini_timer.start(2 * 60)
                cli.mini_timer_duration = 2
                cli.last_msg = "Mini Timer Started: 2m"


class BreakCommand(Command):
    def execute(self, cli):
        cli._transition_from_focus_to_break(self.parts)


class ReorderCommand(Command):
    def execute(self, cli):
        if cli.mode != "TRIAGE" or len(self.parts) < 2:
            return
        src = int(self.parts[1])
        dest = int(self.parts[2]) if len(self.parts) > 2 else 0
        if 0 <= src < len(cli.triage_stack) and 0 <= dest < len(cli.triage_stack):
            cli.triage_stack.insert(dest, cli.triage_stack.pop(src))


class AssignCommand(Command):
    def execute(self, cli):
        if cli.mode != "TRIAGE" or len(self.parts) < 3:
            return
        src_str, dest_idx = self.parts[1], int(self.parts[2])
        if 0 <= dest_idx < len(cli.triage_stack):
            if '.' in src_str:
                p_idx, c_idx = map(int, src_str.split('.'))
                if 0 <= p_idx < len(cli.triage_stack) and 0 <= c_idx < len(cli.triage_stack[p_idx].children):
                    item = cli.triage_stack[p_idx].children.pop(c_idx)
                    item.parent = cli.triage_stack[dest_idx]
                    cli.triage_stack[dest_idx].children.append(item)
            else:
                src_idx = int(src_str)
                if 0 <= src_idx < len(cli.triage_stack):
                    item = cli.triage_stack.pop(src_idx)
                    item.parent = cli.triage_stack[dest_idx]
                    cli.triage_stack[dest_idx].children.append(item)


class IgnoreCommand(Command):
    def execute(self, cli):
        return ResolveCommand(self.parts).execute(cli)


class CommandParser:
    @staticmethod
    def parse(cli, cmd_string, mode):
        # Split symbol commands from their numeric arguments (e.g., >1, n5, x2, x0.1.2)
        cmd_clean = re.sub(r'^(>>|>|[a-zA-Z]|[-x])(-?[\d\.]+)', r'\1 \2', cmd_string)

        def safe_split(s):
            lex = shlex.shlex(s, posix=True)
            lex.quotes = '"'
            lex.whitespace_split = True
            lex.commenters = ''
            return list(lex)

        try:
            parts = safe_split(cmd_clean)
        except ValueError:
            if '"' in cmd_clean:
                try:
                    parts = safe_split(cmd_clean + '"')
                    cli.last_msg = "Note: Added missing closing quote."
                except ValueError:
                    cli.last_msg = "Error: Unbalanced quotes."
                    return None
            else:
                parts = cmd_clean.split()

        if not parts:
            return None

        base_cmd = parts[0].lower()

        if mode == "EXIT":
            if base_cmd == 'q': return QuitCommand(parts)
            if base_cmd == 'w': return FreeWriteCommand(parts)
            return None

        if base_cmd == 'q': return QuitCommand(parts)
        if base_cmd == 't': return TriageCommand(parts)
        if base_cmd == 'w': return FreeWriteCommand(parts)
        if base_cmd in ['n', 'N']: return AddCommand(parts)
        if base_cmd == 'f': return FocusCommand(parts)
        if base_cmd == 'e': return EditCommand(parts)
        if base_cmd == 'b': return BreakCommand(parts)
        if base_cmd in ['x', '-']: return ResolveCommand(parts)
        if base_cmd in ['>', '>>']: return DeferCommand(parts)
        if base_cmd == 'i': return IgnoreCommand(parts)
        if base_cmd == 'm': return MiniTimerCommand(parts)
        if base_cmd == 'p': return ReorderCommand(parts)
        if base_cmd == 'a': return AssignCommand(parts)

        return None


class FocusCLI:
    @property
    def task_start_time(self):
        return self.timers.task_timer.start_time

    @task_start_time.setter
    def task_start_time(self, value):
        self.timers.task_timer.start_time = value
        if value is not None:
            self.timers.task_timer.is_active = True
        else:
            self.timers.task_timer.is_active = False

    @property
    def focus_start_time(self):
        return self.timers.focus_timer.start_time

    @focus_start_time.setter
    def focus_start_time(self, value):
        self.timers.focus_timer.start_time = value
        if value is not None:
            self.timers.focus_timer.is_active = True
        else:
            self.timers.focus_timer.is_active = False

    @property
    def focus_threshold(self):
        return self.timers.focus_timer.threshold

    @focus_threshold.setter
    def focus_threshold(self, value):
        self.timers.focus_timer.threshold = value

    @property
    def triage_stack(self): return self.stack
    @triage_stack.setter
    def triage_stack(self, value): self.stack.populate(value)


    def __init__(self, filename=None, log_file="focus_activity.log", templates_dir="templates"):
        self.filename = filename if filename else (sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime(f'{DATE_FORMAT}-plan.txt'))
        self.log_file = log_file
        self.templates_dir = templates_dir

        self.setup_logging()

        self.mode = "TRIAGE"
        self.stack = TaskStack()

        self.initial_stack = []
        self.last_msg = "FocusCLI Ready."
        self.timers = TimerManager(ALERT_THRESHOLD)
        self.timers.last_chime_timestamp = 0
        self.chimed_meetings = set()
        self.original_termios = None
        self.mini_timer_duration = 2
        self.mini_timer_was_meeting = False
        self.last_recorded_focus = None
        self.break_meeting_interrupted = False

    def setup_logging(self):
        # Clear existing handlers if any
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()

        logging.basicConfig(
            filename=self.log_file,
            level=logging.INFO,
            format='%(asctime)s | %(levelname)s | %(message)s'
        )

    def get_daily_summary(self):
        """Returns a dictionary of counts for top-level tasks and subtasks."""
        counts = {
            'top': {'[x]': 0, '[-]': 0, '[>]': 0},
            'sub': {'[x]': 0, '[-]': 0, '[>]': 0}
        }
        if not os.path.exists(self.filename): return counts

        with open(self.filename, 'r') as f:
            lines = f.readlines()

        latest_states = {} # full_path_key -> (state, is_top)
        stack = [] # current hierarchy of (content, indent)

        for line in lines:
            line_raw = line.rstrip()
            if not line_raw.strip(): continue

            if "-------" in line_raw: continue

            item = ItemFactory.from_line(line_raw)

            while stack and stack[-1][1] >= item.indent:
                stack.pop()

            parent_path = tuple(c for c, i in stack)
            full_key = parent_path + (item.content,)
            is_top = not stack

            if isinstance(item, Task):
                latest_states[full_key] = (item.state, is_top)

            stack.append((item.content, item.indent))

        for key, (state, is_top) in latest_states.items():
            if state in ['x', '-', '>']:
                label = f"[{state}]"
                category = 'top' if is_top else 'sub'
                if label in counts[category]:
                    counts[category][label] += 1
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
        if self.triage_stack:
            self.commit_to_ledger("Triage", self.triage_stack)

        with open(self.filename, 'a') as f:
            f.write(f"\n------- Free Write {get_timestamp()} -------\n\n")

        self._run_with_vi(["+$", "+startinsert", self.filename])

        self.mode = "TRIAGE"
        self.load_context()
        self.sort_triage_stack()
        self.initial_stack = copy.deepcopy(self.triage_stack)
        if not self.timers.focus_timer.is_active:
            self.timers.focus_timer.start()


    def sort_triage_stack(self):
        """Move non-active meetings to the bottom."""
        self.stack.populate(self.triage_stack.get_all())



    def load_context(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w') as f: f.write(f"Session Start - {get_timestamp()}\n")
            self.triage_stack.populate([])
            return
        self.triage_stack.populate(self._parse_file(self.filename))


    def rescue_previous_tasks(self):
        """Scans the last 7 days for pending tasks and defers them to today."""
        # Only rescue if we are using the default daily plan format
        today_str = datetime.now().strftime(DATE_FORMAT)
        if self.filename != f"{today_str}-plan.txt":
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
                    label = f"Deferred to {self.filename}"

                    # Prepare the deferred version for the old file
                    ledger_items = []
                    for task in pending_tasks:
                        # Current ledger version: main task [>], pending subtasks [>], others preserve
                        l_task = task.clone_with_state('>', '>')
                        ledger_items.append(l_task)

                    self.commit_to_ledger(label, ledger_items, target_file=prev_file)

                    # Prepare the rescued tasks for today's file
                    # Requirement: Include full hierarchy of pending subtasks
                    for task in pending_tasks:
                        # Deep copy and strip meeting times
                        rescued_task = copy.deepcopy(task)
                        rescued_task.content = strip_meeting_time(rescued_task.content)
                        # Target version: main task [], subtasks preserve status
                        t_task = rescued_task.clone_with_state(' ', ' ')
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
        top_level_contents = [] # Order of top-level items in current Triage block
        old_top_level = [] # Order of top-level items from earlier blocks
        current_path = [] # list of Item objects

        for line in lines:
            line_raw = line.rstrip()
            if not line_raw.strip(): continue

            if "------- Triage" in line_raw:
                for c in top_level_contents:
                    if c not in old_top_level:
                        old_top_level.append(c)
                top_level_contents = []
                continue

            if "-------" in line_raw: continue

            item = ItemFactory.from_line(line_raw)

            # Adjust current_path
            while current_path and current_path[-1].indent >= item.indent:
                current_path.pop()

            parent_path = tuple(i.content for i in current_path)
            full_path = parent_path + (item.content,)

            if isinstance(item, Task):
                if item.is_pending:
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
                    # This `else` is for 'x', '-', '>', 'e'.
                    active_items.pop(full_path, None)
                    if not current_path:
                        if item.content in top_level_contents:
                            top_level_contents.remove(item.content)
                        if item.content in old_top_level:
                            old_top_level.remove(item.content)
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

        final_order = top_level_contents + [c for c in old_top_level if c not in top_level_contents]
        return [active_items[(c,)] for c in final_order if (c,) in active_items]

    def _get_multi_line_input(self, context_lines=None, initial_content=None, start_insert=True, add_open_line=True):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            if add_open_line:
                tf.write("\n")
            if initial_content:
                if isinstance(initial_content, list):
                    tf.write("\n".join(l.rstrip() for l in initial_content))
                else:
                    tf.write(initial_content.rstrip())
                tf.write("\n")
            tf.write("# Enter one task or note per line\n")
            if context_lines:
                for cl in context_lines:
                    tf.write(f"#{cl}\n")
            temp_path = tf.name

        try:
            vi_args = [temp_path]
            if start_insert:
                vi_args.insert(0, "+startinsert")
            self._run_with_vi(vi_args)
            with open(temp_path, 'r') as f:
                lines = [l.rstrip() for l in f.readlines() if not l.startswith('#')]

            # Strip leading/trailing blank lines from the captured session
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()

            return lines
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _process_multi_line_input(self, lines):
        """Parse multi-line input into Item objects."""
        return Item.from_lines(lines)

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

            new_root_items = Item.from_lines(new_lines)
            if not new_root_items: return item
            new_item = new_root_items[0]
            new_item.indent = original_item.indent

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

    def _get_item_by_hierarchical_index(self, index_str):
        """
        Traverse the task stack using a dot-separated index string (e.g., '0.1.2').
        Returns (item, parent, path) or (None, None, None) if invalid.
        """
        try:
            parts = [int(p) for p in index_str.split('.')]
        except ValueError:
            self.last_msg = f"Error: Invalid index format '{index_str}'."
            return None, None, None

        if not parts:
            return None, None, None

        # Top level
        idx = parts[0]
        if idx < 0 or idx >= len(self.triage_stack):
            self.last_msg = f"Error: Invalid top-level index {idx}."
            return None, None, None

        current_item = self.triage_stack[idx]
        parent = None
        path = []

        # Sub levels
        for sub_idx in parts[1:]:
            if not hasattr(current_item, 'children') or sub_idx < 0 or sub_idx >= len(current_item.children):
                self.last_msg = f"Error: Invalid sub-index {sub_idx} for item '{current_item.content}'."
                return None, None, None
            parent = current_item
            current_item = current_item.children[sub_idx]
            path.append(sub_idx)

        return current_item, parent, path

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

    def _handle_hierarchical_new_items(self, base_cmd_orig, items, target_index=None):
        """Processes a batch of items and inserts them into the task tree."""
        if target_index is not None:
            mode_label = f"New Entry(s) at index {target_index}"
        else:
            mode_label = "Prioritized Entry(s)" if base_cmd_orig == 'N' else "New Entry(s)"

        any_changed = False

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
                    self.timers.task_timer.stop()

                if self.last_msg.startswith("Note:"):
                    self.last_msg = f"{msg} ({self.last_msg})"
                else:
                    self.last_msg = msg

        if top_level_items:
            any_changed = True
            if self.mode == "BREAK" and self.triage_stack and isinstance(self.triage_stack[0], Break):
                if target_index == 0 or (target_index is None and base_cmd_orig == 'N' and not hier_items):
                    old_break = self.triage_stack.pop(0)
                    old_break.state = 'x'
                    self.commit_to_ledger("Break Completed", [old_break])
                    self._transition_from_break_to_focus(break_item=old_break)

            self.commit_to_ledger(mode_label, top_level_items)
            top_level_tasks = [it for it in top_level_items if isinstance(it, Task) and it.is_pending]

            if target_index is not None:
                insert_idx = target_index
                if hier_items and target_index < len(self.triage_stack):
                    insert_idx += 1
                insert_idx = min(insert_idx, len(self.triage_stack))

                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)

                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0].content.strip()
                    self.timers.task_timer.stop()
                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"
            elif base_cmd_orig == 'N':
                insert_idx = 1 if (hier_items and self.triage_stack) else 0
                for it in reversed(top_level_tasks):
                    self.triage_stack.insert(insert_idx, it)
                if insert_idx == 0 and top_level_tasks:
                    self.last_recorded_focus = self.triage_stack[0].content.strip()
                    self.timers.task_timer.stop()
                msg = "Task(s) Added & Prioritized" if top_level_tasks else "Note(s) Added & Prioritized"
            else:
                self.triage_stack.extend(top_level_tasks)
                msg = "Task(s) Added" if top_level_tasks else "Note(s) Added"

            self.last_msg = f"{msg} ({self.last_msg})" if self.last_msg.startswith("Note:") else msg

        return any_changed

    def _transition_from_break_to_focus(self, break_item=None):
        now = time.time()
        if break_item and break_item.start_time:
            break_total_time = (datetime.now() - break_item.start_time).total_seconds()
            if self.timers.task_timer.is_active:
                self.timers.task_timer.start_time += break_total_time

        self.timers.focus_timer.start(now)
        self.mode = "FOCUS"
        self.break_meeting_interrupted = False
        if self.timers.mini_timer.is_active:
            self.timers.mini_timer.reset(self.mini_timer_duration * 60)
        self.commit_to_ledger("Focus Session Re-started at", [])
        self.last_msg = "Focus Resumed"

    def _transition_from_focus_to_break(self, parts):
        if self.mode == "BREAK":
            if self.triage_stack and isinstance(self.triage_stack[0], Break):
                old_break = self.triage_stack.pop(0)
                old_break.state = 'x'
                self.commit_to_ledger("Break Completed", [old_break])
                self._transition_from_break_to_focus(break_item=old_break)
            else:
                self.last_msg = "Break time overload! Doing nothing."
                return
        duration = 5
        if len(parts) > 1:
            try: duration = int(parts[1])
            except ValueError:
                self.last_msg = f"Invalid break duration: {parts[1]}"
                return
        if duration <= 0:
            self.last_msg = "Seriously? Take a real break! 0 minutes is too short."
            return

        break_item = Break.from_attributes(
            content=Break.random_quote(),
            start_time=datetime.now(),
            duration=duration
        )
        self.triage_stack.insert(0, break_item)

        # Add to chimed_meetings to prevent redundant chime for manual breaks
        self.chimed_meetings.add(break_item.get_meeting_id())

        self.mode = "BREAK"
        self.break_meeting_interrupted = False
        self.commit_to_ledger(f"Break for {duration} at", [break_item])
        return


    def _get_progress_stats(self, focus_item, parent_item):
        completed = 0
        total = 0

        if parent_item is None:
            summary = self.get_daily_summary()
            completed = sum(summary['top'].values())
            pending = 0
            for it in self.triage_stack:
                if isinstance(it, Task) and it.is_pending:
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
        dest = target_file if target_file else self.filename
        with open(dest, 'a') as f:
            f.write(f"\n------- {mode_label} {get_timestamp()} -------\n")
            if items:
                for item in items:
                    f.write(f"{item.to_ledger()}\n")

    def update_mini_timer(self):
        if not self.timers.mini_timer.is_active:
            return
        if self.mode == "FOCUS" and self.triage_stack:
            if self.timers.mini_timer.should_chime(interval_seconds=30):
                self.play_chime(sound='tick')

    def play_session_chime(self, sound='chime', interval_seconds=2):
        """Plays a chime and updates the last_chime_timestamp to prevent redundant alerts."""
        if self.timers.should_chime(interval_seconds, update_timestamp=True):
             self.play_chime(sound=sound)

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
        if self.mode == "BREAK":
            remaining = None
            if self.triage_stack and isinstance(self.triage_stack[0], Break):
                break_item = self.triage_stack[0]
                if break_item.end_time:
                    remaining = int((break_item.end_time - datetime.now()).total_seconds())

            if (remaining is not None and remaining <= 0) or self.break_meeting_interrupted:
                self.play_session_chime(interval_seconds=60)
                if remaining is not None and remaining <= 0:
                    self.last_msg = "!! BREAK EXPIRED !!"
        elif self.mode in ["FOCUS", "TRIAGE"]:
            is_meeting = False
            if self.mode == "FOCUS" and self.triage_stack:
                is_meeting = isinstance(self.triage_stack[0], Meeting)
            if self.timers.focus_timer.is_exceeded():
                if not is_meeting:
                    self.play_session_chime(interval_seconds=60)


    def check_meetings(self):
        if self.mode not in ["FOCUS", "BREAK"]: return
        if not self.triage_stack: return
        now = datetime.now()
        top_item = self.triage_stack[0]
        if isinstance(top_item, Break):
            if not top_item.end_time:
                top_item.start_time = datetime.now()
                duration = top_item.duration if top_item.duration is not None else 5
                top_item.duration = duration
                top_item.end_time = top_item.start_time + timedelta(minutes=duration)
                # Suppress chime for auto-initialized breaks at the top of the stack
                self.chimed_meetings.add(top_item.get_meeting_id())

            if self.mode != "BREAK":
                self.mode = "BREAK"
                self.last_msg = f"Break Meeting Started: {top_item.content}"

        # 2. Initial "Meeting Starting" chime (for focused OR due meetings)
        all_active_meetings = [m for m in self.triage_stack if isinstance(m, Meeting) and m.is_active(now=now)]
        for m in all_active_meetings:
             meeting_id = m.get_meeting_id()
             if meeting_id not in self.chimed_meetings:
                 if self.mode == "BREAK" and not isinstance(m, Break):
                     self.break_meeting_interrupted = True
                 self.play_session_chime()
                 self.chimed_meetings.add(meeting_id)
                 self.timers.chimer.load(m.content)
                 self.last_msg = f"Meeting Starting: {m.content}"

        # 3. Handle 15s reminders only for the "due" meeting (meeting_timeline[0])
        # Note: TaskStack.populate ensures active non-focused meetings are in meeting_timeline
        due_meeting = self.stack.check_for_due_meetings()
        if due_meeting:
            # Handle recurring reminders for meeting in timeline:
            if self.timers.chimer.meeting_name != due_meeting.content:
                 self.timers.chimer.load(due_meeting.content)

            if self.timers.chimer.should_chime(interval_seconds=15):
                self.play_session_chime(interval_seconds=0)
        else:
             # If no due meeting is in timeline, focused meetings should NOT trigger reminders
             top_item = self.triage_stack[0]
             if isinstance(top_item, Meeting) and top_item.is_active(now=now):
                  if self.timers.chimer.meeting_name == top_item.content:
                       self.timers.chimer.stop()



    def render_break(self):
        remaining = 0
        break_quote = ""
        if self.triage_stack and isinstance(self.triage_stack[0], Break):
            break_item = self.triage_stack[0]
            remaining = int((break_item.end_time - datetime.now()).total_seconds()) if break_item.end_time else 0
            break_quote = break_item.content

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
        print(f"\n\033[1;32mFOCUS >> \033[0m{break_quote}")
        print("\n" + color + "-"*65 + "\033[0m")
        print("Cmds: [N#] prioritize, [n#] add, [t] triage, [f] focus, [q] quit")

    def update_timer_ui(self):
        sys.stdout.write("\033[s")
        now = time.time()
        if self.mode == "TRIAGE":
            focus_remaining = self.timers.focus_timer.remaining()
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(int(focus_remaining)), 60)
            f_color = "\033[1;31m" if focus_remaining <= 0 else ""
            timer_str = f" | Focus: {f_color}{f_sign}{fm:02d}:{fs:02d}\033[0m"
            sys.stdout.write("\033[1;1H" + f"\033[K--- TRIAGE: {os.path.basename(self.filename)}{timer_str} ---")
        elif self.mode == "FOCUS":
            if not self.triage_stack: return
            if not self.timers.task_timer.is_active: self.timers.task_timer.start(now)
            if not self.timers.focus_timer.is_active: self.timers.focus_timer.start(now)
            top_item = self.triage_stack[0]
            focus_remaining = self.timers.focus_timer.remaining()
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(int(focus_remaining)), 60)
            meeting_timer_str = ""
            if isinstance(top_item, Meeting) and top_item.end_time:
                now_dt = datetime.now()
                remaining = int((top_item.end_time - now_dt).total_seconds())
                m_sign = "-" if remaining < 0 else ""
                mm, ms = divmod(abs(remaining), 60)
                meeting_timer_str = f" | Meeting: {m_sign}{mm:02d}:{ms:02d}"
            mini_timer_str = ""
            is_mini_session = False
            if self.timers.mini_timer.is_active and self.triage_stack:
                is_mini_session = True
                sign = "-" if self.timers.mini_timer.remaining_seconds < 0 else ""
                mm, ms = divmod(abs(self.timers.mini_timer.remaining_seconds), 60)
                mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"
            task_timer_str = ""
            if not (meeting_timer_str and mini_timer_str):
                task_elapsed = int(self.timers.task_timer.elapsed())
                tm, ts = divmod(task_elapsed, 60)
                task_timer_str = f" | Task: {tm:02d}:{ts:02d}"
            color = "\033[1;34m"
            header = " MINI TASK SESSION " if is_mini_session else " FOCUS SESSION "
            if self.timers.focus_timer.is_exceeded():
                color = "\033[1;31;7m"
                header = " !! BREAK TIME !! "
            sys.stdout.write("\033[1;1H" + f"{color}{'='*65}\033[0m")
            sys.stdout.write("\033[2;1H" + f"{color}{header}\033[0m{task_timer_str} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
            sys.stdout.write("\033[3;1H" + f"{color}{'='*65}\033[0m")
        elif self.mode == "BREAK":
            remaining = 0
            if self.triage_stack and isinstance(self.triage_stack[0], Break):
                break_item = self.triage_stack[0]
                remaining = int((break_item.end_time - datetime.now()).total_seconds()) if break_item.end_time else 0
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
            if self.triage_stack:
                self.commit_to_ledger("Interrupted (SIGTERM)", self.triage_stack)
            if self.original_termios: termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
            sys.exit(0)
        signal.signal(signal.SIGTERM, signal_handler)
        if not os.path.exists(self.filename): self.rescue_previous_tasks()
        self.enter_free_write()
        try:
            tty.setcbreak(fd)
            buffer = ""; cursor_pos = 0; last_render_second = -1; last_buffer = None
            last_cursor_pos = None; last_mode = None; last_msg = None; last_task = None
            last_expired = False; last_exceeded = False
            while True:
                now = time.time(); current_second = int(now)
                current_task = self.triage_stack[0] if self.triage_stack else None
                is_expired = False
                if self.mode == "BREAK" and self.triage_stack and isinstance(self.triage_stack[0], Break):
                    break_item = self.triage_stack[0]
                    if break_item.end_time:
                        is_expired = (datetime.now() >= break_item.end_time)
                is_exceeded = False
                if self.mode == "FOCUS" and self.timers.focus_timer.is_active:
                    is_exceeded = self.timers.focus_timer.is_exceeded()
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
                if self.mode in ["FOCUS", "BREAK"]:
                    self.timers.update(self.mode)
                    self.check_meetings()
                    if self.mode == "FOCUS": self.update_mini_timer()
                if self.mode in ["FOCUS", "BREAK", "TRIAGE"]: self.check_chime()
                rlist, _, _ = select.select([fd], [], [], 0.1)
                if rlist:
                    char = self._read_keypress(fd)
                    if not char: continue
                    if char == ' ' and self.mode == "FOCUS" and self.timers.mini_timer.is_active and not buffer:
                        self.timers.mini_timer.reset(self.mini_timer_duration * 60)
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
        except KeyboardInterrupt:
            if self.triage_stack:
                self.commit_to_ledger("Interrupted", self.triage_stack)
        finally: termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

    def render_triage(self):
        focus_remaining = self.timers.focus_timer.remaining()
        f_sign = "-" if focus_remaining < 0 else ""; fm, fs = divmod(abs(int(focus_remaining)), 60)
        f_color = "\033[1;31m" if focus_remaining <= 0 else ""
        timer_str = f" | Focus: {f_color}{f_sign}{fm:02d}:{fs:02d}\033[0m"
        print(f"--- TRIAGE: {os.path.basename(self.filename)}{timer_str} ---")
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
        print(f"\n\033[1;32mDAILY SCORECARD ({os.path.basename(self.filename)})\033[0m")
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
        if not self.timers.task_timer.is_active: self.timers.task_timer.start(now)
        if not self.timers.focus_timer.is_active: self.timers.focus_timer.start(now)
        task_elapsed = int(self.timers.task_timer.elapsed()); tm, ts = divmod(task_elapsed, 60)
        focus_remaining = self.timers.focus_timer.remaining()
        f_sign = "-" if focus_remaining < 0 else ""; fm, fs = divmod(abs(int(focus_remaining)), 60)
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
        if self.timers.mini_timer.is_active and self.triage_stack:
            is_mini_session = True; sign = "-" if self.timers.mini_timer.remaining_seconds < 0 else ""
            mm, ms = divmod(abs(self.timers.mini_timer.remaining_seconds), 60); mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"
        task_timer_str = ""
        if not (meeting_timer_str and mini_timer_str):
            task_elapsed = int(self.timers.task_timer.elapsed()); tm, ts = divmod(task_elapsed, 60); task_timer_str = f" | Task: {tm:02d}:{ts:02d}"
        color = "\033[1;34m"; header = " MINI TASK SESSION " if is_mini_session else " FOCUS SESSION "
        if self.timers.focus_timer.is_exceeded(): color = "\033[1;31;7m"; header = " !! BREAK TIME !! "
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

    def handle_command(self, cmd_string):
        self.last_msg = ""
        try:
            command = CommandParser.parse(self, cmd_string, self.mode)
            if command:
                if self.mode == "BREAK":
                    is_break_obj = self.triage_stack and isinstance(self.triage_stack[0], Break)
                    base_cmd = command.parts[0].lower() if command.parts else ""
                    if not (base_cmd in ['f', 'b', 'n', 'N', 't', 'q'] or (is_break_obj and base_cmd in ['x', '-', 'i', '>', '>>', 'e'])):
                        self.last_msg = "Command disabled during break."
                        return

                return command.execute(self)
            elif self.mode != "EXIT":
                 if not cmd_string.strip(): return
                 self.last_msg = f"Unknown command: {cmd_string}"
        except Exception as e: self.last_msg = f"Error: {e}"
        return None

if __name__ == "__main__":
    FocusCLI().run()
