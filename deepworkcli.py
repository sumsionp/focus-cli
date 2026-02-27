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
import subprocess
import shlex
import tempfile
from datetime import datetime, timedelta

# --- CONFIG ---
DATE_FORMAT = '%Y%m%d'
FILENAME = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime(f'{DATE_FORMAT}-notes.txt')
LOG_FILE = "deepwork_activity.log"
ALERT_THRESHOLD = 25 * 60
CHIME_COMMAND = None # Set to a command string like "play /path/to/sound.wav" to override

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

class DeepWorkCLI:
    def __init__(self):
        self.mode = "TRIAGE"
        self.triage_stack = []
        self.initial_stack = []
        self.last_msg = "DeepWorkCLI Ready."
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
        self.subtask_mode = False
        self.active_subtask_idx = -1

    def get_daily_summary(self):
        counts = {'[x]': 0, '[-]': 0, '[>]': 0}
        if not os.path.exists(FILENAME): return counts

        seen_tasks = set()
        with open(FILENAME, 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                clean = line.strip()
                if not clean or "-------" in clean or line.startswith('  '):
                    continue

                marker_match = re.match(r'^\[([xe\->\s]?)\]', clean)
                if marker_match:
                    state = marker_match.group(1)
                    content = clean[marker_match.end():].strip()
                    if content not in seen_tasks:
                        if state in ['x', '-', '>']:
                            counts[f'[{state}]'] += 1
                        seen_tasks.add(content)
        return counts

    def load_context(self):
        """Whole-file aware parser with resolution logic. Resolutions are [x], [-], [>], and [e]."""
        if not os.path.exists(FILENAME):
            with open(FILENAME, 'w') as f: f.write(f"Session Start - {get_timestamp()}\n")
            self.triage_stack = []
            return
        
        with open(FILENAME, 'r') as f:
            lines = [l.rstrip() for l in f.readlines()]

        active_entries = {} # content -> {notes, is_task}
        entry_order = [] # list of contents
        last_entry_content = None
        
        for line in lines:
            if "------- Triage" in line:
                new_entry_order = []
                for content in entry_order:
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

        self.triage_stack = []
        for content in entry_order:
            entry = active_entries[content]
            self.triage_stack.append({
                'line': f"[] {content}" if entry['is_task'] else content,
                'notes': entry['notes']
            })

    def _prepare_defer_tasks(self, task, target_date):
        """Prepare tasks for ledger and target file without committing them."""
        is_target_today = target_date.date() == datetime.now().date()
        today_str = datetime.now().strftime(DATE_FORMAT)
        is_current_file_today = today_str in FILENAME

        if is_target_today and is_current_file_today:
            return copy.deepcopy(task), None, "today"
        else:
            target_file = get_target_file(target_date)
            # Target version: main task [], subtasks preserve status
            target_task = self._prepare_task_with_markers(task, '[]', '[]')
            # Current ledger version: main task [>], pending subtasks [>], others preserve
            ledger_task = self._prepare_task_with_markers(task, '[>]', '[>]')
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
                self.triage_stack.extend(ledger_items)
                self.last_msg = f"Deferred {count} items to end of today's stack"
            else:
                self.commit_to_ledger("Deferred from last session", target_items, target_file=target_res)
                self.commit_to_ledger("Deferred", ledger_items)
                self.last_msg = f"Deferred {count} items to {target_res}"
        else: # base_cmd == '>'
            task = self.triage_stack.pop(0)
            l_task, t_task, res = self._prepare_defer_tasks(task, target_date)
            if res == "today":
                self.commit_to_ledger("Deferred", [l_task])
                self.triage_stack.append(l_task)
                self.last_msg = "Deferred to end of today's stack"
            else:
                self.commit_to_ledger("Deferred from last session", [t_task], target_file=res)
                self.commit_to_ledger("Deferred", [l_task])
                self.last_msg = f"Deferred to {res}"

        self.task_start_time = None
        self.initial_stack = copy.deepcopy(self.triage_stack)
        return True

    def _get_multi_line_input(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            tf.write("\n# Enter one task or note per line\n")
            temp_path = tf.name

        try:
            if self.original_termios:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

            os.system(f"vi +startinsert {temp_path}")

            with open(temp_path, 'r') as f:
                lines = [l.rstrip() for l in f.readlines() if not l.startswith('#')]

            return lines
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _process_multi_line_input(self, lines):
        if not lines:
            return [], False

        items = []
        current_item = None

        # Check if all non-empty lines are indented
        only_indented = all(l.startswith(' ') for l in lines if l.strip())

        if only_indented:
            if self.mode in ["WORK", "BREAK"] and self.triage_stack:
                # Add as sub-items to the active task
                active_task = self.triage_stack[0]
                added_any = False
                for l in lines:
                    if l.strip():
                        # Remove only the first 2 spaces to preserve deeper nesting
                        note = l[2:] if l.startswith('  ') else l.lstrip()
                        active_task['notes'].append(note)
                        added_any = True
                return [], added_any
            else:
                # Treat as top-level items
                for l in lines:
                    if l.strip():
                        items.append({'line': l.strip(), 'notes': []})
                return items, False

        for l in lines:
            if not l.strip(): continue
            if not l.startswith(' '):
                current_item = {'line': l.strip(), 'notes': []}
                items.append(current_item)
            else:
                if current_item:
                    # Remove only the first 2 spaces to preserve deeper nesting
                    note = l[2:] if l.startswith('  ') else l.lstrip()
                    current_item['notes'].append(note)
                else:
                    # Indented line before any top-level item in this batch
                    # Treat as top-level
                    current_item = {'line': l.strip(), 'notes': []}
                    items.append(current_item)

        return items, False

    def _edit_item(self, item):
        original_item = copy.deepcopy(item)

        content = [item['line']]
        for note in item['notes']:
            content.append(f"  {note}")

        with tempfile.NamedTemporaryFile(suffix=".txt", mode='w+', delete=False) as tf:
            tf.write("\n".join(content))
            temp_path = tf.name

        try:
            # We must restore terminal settings before calling vi
            if self.original_termios:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

            os.system(f"vi {temp_path}")

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
        content = re.sub(r'^\[[xe\->\s]?\]\s*', '', task['line'])
        new_task['line'] = f"{main_marker} {content}"
        new_notes = []
        for n in task['notes']:
            m = re.match(r'^\[([xe\->\s]?)\]\s*', n)
            if m:
                state = m.group(1).strip()
                if not state: # pending
                    sub_content = n[m.end():].strip()
                    new_notes.append(f"{pending_sub_marker} {sub_content}")
                else:
                    new_notes.append(n) # keep [x], [-], etc.
            else:
                new_notes.append(n) # keep notes
        new_task['notes'] = new_notes
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
        is_active_meeting = self.is_meeting_active()

        # Reset if transitioning back from a meeting
        if self.mini_timer_was_meeting and not is_active_meeting:
            self.mini_timer_remaining = self.mini_timer_duration * 60
            self.mini_timer_last_tick = now
            self.mini_timer_last_chime_timestamp = 0
        self.mini_timer_was_meeting = is_active_meeting

        if self.mode == "WORK" and self.triage_stack and not is_active_meeting:
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

            if remaining <= 0:
                if now - self.last_chime_timestamp >= 60:
                    self.play_chime()
                    self.last_chime_timestamp = now
                    self.last_msg = "!!! BREAK EXPIRED !!!"
        elif self.mode == "WORK":
            is_meeting = False
            if self.triage_stack:
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
        if self.mode != "WORK": return
        if not self.triage_stack: return

        now = datetime.now()
        found_active_meeting = False
        for i, task in enumerate(self.triage_stack):
            m_time = parse_meeting_time(task['line'])
            if m_time and m_time[0] <= now < m_time[1]:
                meeting_id = f"{task['line']}_{m_time[0]}"
                if meeting_id not in self.chimed_meetings:
                    self.play_chime()
                    self.chimed_meetings.add(meeting_id)
                    task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', task['line'])
                    self.last_msg = f"Meeting Starting: {task_content}"

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
        if remaining <= 0:
            color = "\033[1;31;7m"
            header = " !!! BREAK EXPIRED !!! "

        print(color + "="*65 + "\033[0m")
        print(f"{color}{header}\033[0m | Remaining: {time_str}")
        print(color + "="*65 + "\033[0m")

        print(f"\n\033[1;32mFOCUS >> \033[0m{self.break_quote}")

        print("\n" + color + "-"*65 + "\033[0m")
        print("Cmds: [N] prioritize, [n] add, [t] triage, [w] work, [q] quit")

    def update_timer_ui(self):
        """Minimal redraw of just the header to preserve terminal selection."""
        sys.stdout.write("\033[s") # Save cursor
        now = time.time()
        if self.mode == "WORK":
            if not self.triage_stack: return
            if self.task_start_time is None: self.task_start_time = now
            if self.focus_start_time is None: self.focus_start_time = now

            task_elapsed = int(now - self.task_start_time)
            tm, ts = divmod(task_elapsed, 60)

            focus_elapsed = int(now - self.focus_start_time)
            focus_remaining = self.focus_threshold - focus_elapsed
            f_sign = "-" if focus_remaining < 0 else ""
            fm, fs = divmod(abs(focus_remaining), 60)

            meeting_time = None
            if self.triage_stack:
                meeting_time = parse_meeting_time(self.triage_stack[0]['line'])
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
                if not self.is_meeting_active():
                    is_mini_session = True
                    sign = "-" if self.mini_timer_remaining < 0 else ""
                    mm, ms = divmod(abs(self.mini_timer_remaining), 60)
                    mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"

            color = "\033[1;34m"
            header = " MINI TASK SESSION " if is_mini_session else " DEEP WORK SESSION "
            if focus_elapsed > self.focus_threshold:
                color = "\033[1;31;7m"
                header = " !!! FOCUS LIMIT EXCEEDED !!! "

            sys.stdout.write("\033[1;1H" + f"{color}{'='*65}\033[0m")
            sys.stdout.write("\033[2;1H" + f"{color}{header}\033[0m | Task: {tm:02d}:{ts:02d} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
            sys.stdout.write("\033[3;1H" + f"{color}{'='*65}\033[0m")
        elif self.mode == "BREAK":
            elapsed_break = time.time() - self.break_start_time
            remaining = int(self.break_duration * 60 - elapsed_break)
            sign = "-" if remaining < 0 else ""
            m, s = divmod(abs(remaining), 60)
            color = "\033[1;34m"
            header = " BREAK SESSION "
            if remaining <= 0:
                color = "\033[1;31;7m"
                header = " !!! BREAK EXPIRED !!! "

            sys.stdout.write("\033[1;1H" + f"{color}{'='*65}\033[0m")
            sys.stdout.write("\033[2;1H" + f"{color}{header}\033[0m | Remaining: {sign}{m:02d}:{s:02d}")
            sys.stdout.write("\033[3;1H" + f"{color}{'='*65}\033[0m")

        sys.stdout.write("\033[u") # Restore cursor
        sys.stdout.flush()

    def run(self):
        self.load_context()
        self.initial_stack = copy.deepcopy(self.triage_stack)

        fd = sys.stdin.fileno()
        self.original_termios = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            buffer = ""
            last_render_second = -1
            last_buffer = None
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
                if self.mode == "WORK" and self.focus_start_time:
                    focus_elapsed = now - self.focus_start_time
                    is_exceeded = (focus_elapsed > self.focus_threshold)

                structural_change = (
                    buffer != last_buffer or
                    self.mode != last_mode or
                    self.last_msg != last_msg or
                    current_task != last_task or
                    is_expired != last_expired or
                    is_exceeded != last_exceeded
                )

                if current_task != last_task and self.mode == "WORK":
                    # If the top-level task changed, we should probably exit subtask mode
                    # to avoid weird state issues unless we explicitly want to stay in it.
                    # Per requirements, top-level changes (like N or meeting preemption)
                    # should likely reset subtask mode focus.
                    if self.subtask_mode:
                        self.subtask_mode = False
                        self.active_subtask_idx = -1

                if structural_change:
                    sys.stdout.write("\033[H\033[J")
                    if self.mode == "TRIAGE":
                        self.render_triage()
                    elif self.mode == "WORK":
                        self.render_work()
                    elif self.mode == "BREAK":
                        self.render_break()

                    print(f"\n\033[90mStatus: {self.last_msg}\033[0m")
                    sys.stdout.write(f"\033[1;37m>> \033[0m{buffer}")
                    sys.stdout.flush()

                    last_render_second = current_second
                    last_buffer = buffer
                    last_mode = self.mode
                    last_msg = self.last_msg
                    last_task = copy.deepcopy(current_task)
                    last_expired = is_expired
                    last_exceeded = is_exceeded

                elif current_second != last_render_second:
                    if self.mode in ["WORK", "BREAK"]:
                        self.update_timer_ui()
                    last_render_second = current_second

                if self.mode in ["WORK", "BREAK"]:
                    self.check_chime()

                if self.mode == "WORK":
                    self.check_meetings()
                    self.update_mini_timer()

                rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                if rlist:
                    char = sys.stdin.read(1)
                    if char == ' ' and self.mode == "WORK" and self.mini_timer_active and not buffer:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0
                        self.last_msg = "Mini Timer Reset"
                    elif char == '\n' or char == '\r':
                        cmd = buffer.strip()
                        buffer = ""
                        termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)
                        print()
                        result = self.handle_command(cmd)
                        if result == "QUIT":
                            summary = self.get_daily_summary()
                            print("\n" + "="*35)
                            print(f"\033[1;32mDAILY SCORECARD ({os.path.basename(FILENAME)})\033[0m")
                            print(f"  Finished  [x]: {summary['[x]']}")
                            print(f"  Cancelled [-]: {summary['[-]']}")
                            print(f"  Deferred  [>]: {summary['[>]']}")
                            print("="*35)
                            input("\nTake a break. Press Enter to return to Free Write...")
                            break
                        tty.setcbreak(fd)
                    elif char in ['\x7f', '\x08']:
                        buffer = buffer[:-1]
                    elif ord(char) == 3: # Ctrl+C
                        raise KeyboardInterrupt
                    else:
                        buffer += char
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, self.original_termios)

    def render_triage(self):
        print(f"--- TRIAGE: {os.path.basename(FILENAME)} ---")

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
                color = "\033[1;31m"
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
            print("\nCmds: [p# #] reorder, [a# #] assign, [e#] edit, [i#] ignore, [N] prioritize, [n] add, [>>] defer all, [w] work, [q] quit")

    def render_work(self):
        if not self.triage_stack:
            print("\n\033[1;32m[FLOW COMPLETE]\033[0m Press 'q' to return to vi.")
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
        
        t = self.triage_stack[0]
        meeting_time = parse_meeting_time(t['line'])
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
            if not self.is_meeting_active():
                is_mini_session = True
                sign = "-" if self.mini_timer_remaining < 0 else ""
                mm, ms = divmod(abs(self.mini_timer_remaining), 60)
                mini_timer_str = f" | Mini: {sign}{mm:02d}:{ms:02d}"

        color = "\033[1;34m"
        header = " MINI TASK SESSION " if is_mini_session else " DEEP WORK SESSION "
        if focus_elapsed > self.focus_threshold:
            color = "\033[1;31;7m"
            header = " !!! FOCUS LIMIT EXCEEDED !!! "

        is_task = t['line'].startswith('[]')
        print(color + "="*65 + "\033[0m")
        print(f"{color}{header}\033[0m | Task: {tm:02d}:{ts:02d} | Focus: {f_sign}{fm:02d}:{fs:02d}{meeting_timer_str}{mini_timer_str}")
        print(color + "="*65 + "\033[0m")
        
        if self.subtask_mode and 0 <= self.active_subtask_idx < len(t['notes']):
            parent_display = re.sub(r'^\[\s?\]\s*', '', t['line'])
            print(f"\n\033[1;34mPARENT >> {parent_display}\033[0m")

            # Display parent's general notes (not subtasks)
            for i, n in enumerate(t['notes']):
                if not re.match(r'^\[[xe\->\s]?\]', n):
                    print(f"  {i}: {n}")

            subtask_line = t['notes'][self.active_subtask_idx]
            sub_display = re.sub(r'^\[\s?\]\s*', '', subtask_line)
            print(f"\n\033[1;32mFOCUS (Subtask) >> {sub_display}\033[0m")

            # Display nested notes for this subtask
            # They are lines following self.active_subtask_idx that are indented
            for i in range(self.active_subtask_idx + 1, len(t['notes'])):
                n = t['notes'][i]
                if n.startswith('  '):
                    n_color = "\033[1;36m" if '[]' in n else ""
                    print(f"  {i}: {n_color}{n}\033[0m")
                elif re.match(r'^\[[xe\->\s]?\]', n) or not n.startswith('  '):
                    # Next subtask or a non-indented note (which should be ignored per req)
                    break
        else:
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
        sub_mode_cmd = ", [s] subtask mode" if not self.subtask_mode else ", [s] exit subtask mode"
        print(f"Cmds: [x] done, [x#] subtask, [e] edit, [-] cancel, [>] defer, [>>] defer all, [f#] focus, [m#] mini{extra_cmds}{sub_mode_cmd}, [N] prioritize, [n] add, [i] ignore, [t] triage, [q] quit")

    def handle_command(self, cmd):
        try:
            cmd_clean = re.sub(r'^([a-zA-Z])(\d)', r'\1 \2', cmd)
            parts = cmd_clean.split()
            if not parts: return
            base_cmd_orig = parts[0]
            base_cmd = base_cmd_orig.lower()
            
            if base_cmd == 'q':
                active = self.triage_stack
                if active:
                    print(f"\n\033[1;33m[!] Session Interrupted.\033[0m")
                    if input("Rescue remaining tasks to Free Write? (y/n): ").lower() == 'y':
                        self.commit_to_ledger("Interrupted", active)
                    else:
                        self.commit_to_ledger("Interrupted", [])
                else:
                    if self.mode in ["WORK", "BREAK"]:
                        print(f"\n\033[1;32m[+] Work Session Complete.\033[0m")
                        self.commit_to_ledger("Work Session Complete", [])
                    else:
                        self.commit_to_ledger("Triage", [])
                return "QUIT"

            if base_cmd == 't': 
                self.mode = "TRIAGE"; self.task_start_time = None
                self.break_start_time = None
                return

            if base_cmd == 'n' and self.mode in ["WORK", "BREAK", "TRIAGE"]:
                lines = self._get_multi_line_input()
                items, added_to_active = self._process_multi_line_input(lines)

                if not items and not added_to_active:
                    return

                mode_label = "Prioritized Entry(s)" if base_cmd_orig == 'N' else "New Entry(s)"

                if added_to_active:
                    self.commit_to_ledger(mode_label, [self.triage_stack[0]])
                    self.last_msg = "Sub-items Added"
                else:
                    self.commit_to_ledger(mode_label, items)

                    # Only add tasks to triage_stack
                    new_tasks = [it for it in items if it['line'].startswith('[]')]

                    if base_cmd_orig == 'N':
                        for it in reversed(new_tasks):
                            self.triage_stack.insert(0, it)
                        if new_tasks:
                            self.last_msg = "Task(s) Added & Prioritized"
                            self.task_start_time = None
                        else:
                            self.last_msg = "Note(s) Added"

                        if self.mode == "WORK":
                            if self.mini_timer_active:
                                self.mini_timer_remaining = self.mini_timer_duration * 60
                                self.mini_timer_last_tick = time.time()
                                self.mini_timer_last_chime_timestamp = 0
                            self.check_meetings()
                    else:
                        self.triage_stack.extend(new_tasks)
                        if new_tasks:
                            self.last_msg = "Task(s) Added"
                        else:
                            self.last_msg = "Note(s) Added"

                self.initial_stack = copy.deepcopy(self.triage_stack)
                return

            if self.mode == "BREAK":
                if base_cmd == 'w':
                    now = time.time()
                    break_total_time = now - self.break_start_time
                    if self.task_start_time:
                        self.task_start_time += break_total_time
                    self.focus_start_time = now
                    self.mode = "WORK"
                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = now
                        self.mini_timer_last_chime_timestamp = 0
                    self.commit_to_ledger("Work Session Re-started at", [])
                    self.last_msg = "Work Resumed"
                    self.last_chime_timestamp = 0
                    return
                elif base_cmd == 'b':
                    self.last_msg = "Break time overload! Doing nothing."
                    self.break_quote = random.choice(BREAK_QUOTES)
                    return
                elif base_cmd in ['n', 'N']:
                    pass # Handled by shared WORK/BREAK logic
                elif base_cmd in ['t', 'q']:
                    pass # Handled by common logic
                else:
                    self.last_msg = "Command disabled during break."
                    return

            if self.mode == "TRIAGE":
                if base_cmd == 'w':
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
                    self.mode = "WORK"; self.last_msg = ""
                    self.subtask_mode = False
                    self.active_subtask_idx = -1
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
                    if item['line'].startswith('[]'):
                        # It's a task, mark as cancelled
                        task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', item['line'])
                        item['line'] = f"[-] {task_content}"
                        new_notes = []
                        for n in item['notes']:
                            if re.match(r'^\[[xe>]\]', n):
                                new_notes.append(n)
                            else:
                                clean_note = re.sub(r'^\[[xe\->\s]?\]\s*', '', n)
                                new_notes.append(f"[-] {clean_note}")
                        item['notes'] = new_notes
                        self.commit_to_ledger("Cancelled", [item])
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

                elif base_cmd in ['>', '>>']:
                    if self._handle_defer_command(base_cmd, parts):
                        return

            elif self.mode in ["WORK", "BREAK"]:
                if not self.triage_stack:
                    if base_cmd == 'q':
                        return "QUIT"
                    if base_cmd != 'n':
                        return

                task = self.triage_stack[0]
                is_note = not task['line'].startswith('[]')

                if self.subtask_mode and base_cmd in ['x', '-', 'i', '>', 'e']:
                    is_indexed_x = (base_cmd == 'x' and (len(parts) > 1 or re.match(r'^x\d+', cmd)))
                    if is_indexed_x:
                        # Allow fall-through to match_x or other indexed logic
                        pass
                    else:
                        if self.mode == "BREAK":
                            self.last_msg = "Command disabled during break."
                            return

                        sub_idx = self.active_subtask_idx
                    sub_item, sub_end_idx = self._get_subtask_as_item(task, sub_idx)

                    if base_cmd == 'e':
                        new_sub_item = self._edit_item(sub_item)
                        if new_sub_item != sub_item:
                            self._update_subtask_from_item(task, sub_idx, sub_end_idx, new_sub_item)
                            self.initial_stack = copy.deepcopy(self.triage_stack)
                        return

                    # Resolve subtask
                    effective_cmd = '-' if base_cmd == 'i' else base_cmd
                    marker = {'x': '[x]', '-': '[-]', '>': '[>]'}[effective_cmd]

                    if base_cmd == '>':
                        defer_date_str = " ".join(parts[1:])
                        target_date = parse_defer_date(defer_date_str)
                        if not target_date:
                            self.last_msg = f"Invalid date: {defer_date_str}"
                            return

                        l_task, t_task, res = self._prepare_defer_tasks(sub_item, target_date)
                        if res != "today":
                            self.commit_to_ledger("Deferred from last session", [t_task], target_file=res)
                            self.last_msg = f"Subtask deferred to {res}"
                        else:
                            # When deferring to today, we want it at the end of the stack as a pending task
                            # l_task is already a copy from _prepare_defer_tasks (with the fix)
                            self.triage_stack.append(l_task)
                            self.last_msg = "Subtask deferred to end of today's stack"

                    # Update memory (mark subtask as deferred in parent)
                    sub_item['line'] = re.sub(r'^\[[xe\->\s]?\]', marker, sub_item['line'])
                    sub_item['notes'] = [f"{marker} " + re.sub(r'^\[[xe\->\s]?\]\s*', '', n) for n in sub_item['notes']]

                    self._update_subtask_from_item(task, sub_idx, sub_end_idx, sub_item)
                    self.commit_to_ledger("Work", [task])

                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0

                    # Advance to next pending
                    found_next = False
                    for i in range(len(task['notes'])):
                        if re.match(r'^\[\s?\]', task['notes'][i]):
                            self.active_subtask_idx = i
                            found_next = True
                            break

                    if not found_next:
                        self.subtask_mode = False
                        self.active_subtask_idx = -1
                        self.last_msg = "All subtasks resolved. Returning to parent."

                    self.initial_stack = copy.deepcopy(self.triage_stack)
                    return

                if base_cmd == 'b' and self.mode == "WORK":
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
                    self.break_duration = duration
                    self.break_start_time = time.time()
                    self.break_quote = random.choice(BREAK_QUOTES)
                    self.last_chime_timestamp = 0
                    self.commit_to_ledger(f"Break for {duration} at", [])
                    return

                if base_cmd == 'f' and self.mode == "WORK":
                    if len(parts) > 1:
                        try:
                            self.focus_threshold = int(parts[1]) * 60
                            self.last_chime_timestamp = 0
                            self.last_msg = f"Focus threshold set to {parts[1]}m"
                        except ValueError:
                            self.last_msg = f"Invalid focus duration: {parts[1]}"
                    else:
                        val = input("Enter focus length (mins): ")
                        try:
                            self.focus_threshold = int(val) * 60
                            self.last_chime_timestamp = 0
                            self.last_msg = f"Focus threshold set to {val}m"
                        except ValueError:
                            self.last_msg = f"Invalid focus duration: {val}"
                    return

                if base_cmd == 'e':
                    self.triage_stack[0] = self._edit_item(self.triage_stack[0])
                    self.initial_stack = copy.deepcopy(self.triage_stack)
                    return

                if base_cmd == 'm' and self.mode == "WORK":
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

                if base_cmd == 's' and self.mode == "WORK":
                    if self.subtask_mode:
                        self.subtask_mode = False
                        self.active_subtask_idx = -1
                        self.last_msg = "Subtask Mode Off"
                        return

                    # Entering Subtask Mode
                    has_subtasks = any(re.match(r'^\[\s?\]', n) for n in task['notes'])

                    if not has_subtasks:
                        lines = self._get_multi_line_input()
                        added = False
                        for l in lines:
                            if l.strip():
                                clean = l.strip()
                                # If it doesn't start with a marker, add []
                                if not re.match(r'^\[[xe\->\s]?\]', clean):
                                    clean = f"[] {clean}"
                                task['notes'].append(clean)
                                added = True
                        if added:
                            self.commit_to_ledger("New Entry(s)", [task])
                            self.last_msg = "Subtasks Added"
                        else:
                            return

                    # Find first pending subtask
                    for i, n in enumerate(task['notes']):
                        if re.match(r'^\[\s?\]', n):
                            self.active_subtask_idx = i
                            self.subtask_mode = True
                            self.last_msg = "Subtask Mode On"
                            return

                    self.last_msg = "No pending subtasks found"
                    return

                match_x = re.match(r'^x(\d+)', cmd)
                if match_x:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return
                    idx = int(match_x.group(1))
                    if 0 <= idx < len(task['notes']):
                        task['notes'][idx] = re.sub(r'^\[\s?\]', '[x]', task['notes'][idx])
                        if self.subtask_mode:
                            self.commit_to_ledger("Work", [task])
                        if self.mini_timer_active:
                            self.mini_timer_remaining = self.mini_timer_duration * 60
                            self.mini_timer_last_tick = time.time()
                            self.mini_timer_last_chime_timestamp = 0
                    return

                if is_note and base_cmd in ['x', '-', 'i']:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return
                    self.triage_stack.pop(0)
                    self.task_start_time = None
                    self.initial_stack = copy.deepcopy(self.triage_stack)
                    return

                if base_cmd in ['x', '-', '>', '>>', 'i']:
                    if self.mode == "BREAK":
                        self.last_msg = "Command disabled during break."
                        return

                    if base_cmd in ['>', '>>']:
                        if self._handle_defer_command(base_cmd, parts):
                            return

                    effective_cmd = '-' if base_cmd == 'i' else base_cmd
                    marker = {'x': '[x]', '-': '[-]', '>': '[>]'}[effective_cmd]
                    task_content = re.sub(r'^\[[xe\->\s]?\]\s*', '', task['line'])
                    
                    task['line'] = f"{marker} {task_content}"
                    task['notes'] = [f"{marker} " + re.sub(r'^\[[xe\->\s]?\]\s*', '', n) for n in task['notes']]
                    
                    self.commit_to_ledger("Work", [self.triage_stack.pop(0)])
                    if self.mini_timer_active:
                        self.mini_timer_remaining = self.mini_timer_duration * 60
                        self.mini_timer_last_tick = time.time()
                        self.mini_timer_last_chime_timestamp = 0
                    self.task_start_time = None
                    self.subtask_mode = False
                    self.active_subtask_idx = -1
                    self.initial_stack = copy.deepcopy(self.triage_stack)

        except Exception as e:
            self.last_msg = f"Error: {e}"
        return None

if __name__ == "__main__":
    DeepWorkCLI().run()
