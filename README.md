# DeepWorkCLI

A Python-based modal CLI tool (TUI) to capture and organize thoughts into trackable tasks and then present those tasks in a way to promote focused completion.

## Core Philosophy: The Ledger
This program acts as a "lens" for a chronological journal stored in a plain text file.
- **Append Only:** We preserve history by primarily appending to the file rather than editing in place.
- **Markers:** We use dividers (e.g., `------- Triage ... -------`) to denote blocks of time and session transitions.
- **Free Write:** The "Free Write" area is conceptually the section after the very last marker in the file where notes and tasks are entered freely.

## Syntax & Hierarchy
- **Tasks:** Lines starting with `[]`, `[ ]`, `[x]`, `[-]`, `[>]`, or `[e]`.
- **Notes:** Any line that isn't a task.
- **Hierarchy:** Two leading spaces indicate a child relationship (subtask or note) to the task above.

### Meeting Support
Tasks can be time-aware by including a time block. Meetings automatically preempt the current task when their start time arrives.
Supported formats:
- `2:00-3:00 PM`
- `2-3 PM`
- `11:00 AM-1:00 PM`
- `2 PM 2h 15m`

## Modal Workflow
The program has three modes: **Free Write**, **Triage**, and **Work**.

### 1. Free Write Mode
Entered by opening your journal file in a text editor (like `vi`). This is where you enter notes and tasks freely.

#### Vim Integration
To integrate seamlessly with `vi`, add the following to your `.vimrc`:
```vim
" Press F5 to save and open current file in DeepWorkCLI
map <F5> :w<CR>:!python3 ~/projects/deep-work-cli/deepworkcli.py "%"<CR>
set autoread
" Trigger autoread when changing buffers or focusing vim
autocmd FocusGained,BufEnter,CursorHold,CursorHoldI * if mode() != 'c' | checktime | endif
```

### 2. Triage Mode
Entered by running the script with a filename. It parses the end of the file (after the last marker) for new notes and pending tasks.

**Commands:**
- `p <src> <dest>`: **Prioritize/Reorder.** Moves item at index `<src>` to `<dest>`.
- `a <note_idx> <task_idx>`: **Assign.** Moves a note (or task) at `<note_idx>` to be a sub-item of task at `<task_idx>`.
- `e <idx>`: **Edit.** Opens the item and its sub-items in `vi` for editing.
- `i <idx>`: **Ignore.** Removes a note from the stack. If it's a task, marks it as cancelled `[-]`.
- `w`: **Work.** Commits the triage session and enters Work Mode.
- `q`: **Quit.** Returns to Free Write (exits the CLI).

### 3. Work Mode
Entered by typing `w` from Triage Mode. It displays the top task along with its associated notes and subtasks.

**Features:**
- **Task Timer:** Tracks time spent on the current task.
- **Focus Timer:** Countdown timer for the overall session.
- **Mini Task Timer:** A repeating timer for rapid completion of small focus items.
- **Auditory Feedback:** Chimes when timers expire.

**Commands:**
- `x`: **Done.** Marks the current task and all subtasks as complete `[x]`. (Also resets the Mini Timer if active).
- `x<idx>`: **Subtask Done.** Marks the subtask at `<idx>` as complete `[x]`.
- `e`: **Edit.** Opens the current task and its sub-items in `vi` for editing.
- `-`: **Cancel.** Marks the current task as cancelled `[-]`.
- `>`: **Defer.** Marks the task as deferred `[>]` and appends it to a tomorrow-plan.txt file. (Also resets the Mini Timer if active).
- `f <mins>` or `f`: **Focus.** Sets/Changes the Focus Timer duration.
- `m <mins>` or `m`: **Mini Task.** Toggles Mini Task Session mode (default 2 minutes).
- `[Space]`: **Reset Mini Timer.** When in Mini Task Session mode, resets the timer to its full duration (only works when command buffer is empty).
- `n`: **Add.** Adds a new top-level task/note or a sub-item (if input starts with a space).
- `b <mins>`: **Break.** Enters Break Mode for specified minutes (default 5).
- `i`: **Ignore.** Skips the current item (marks as cancelled if it's a task).
- `t`: **Triage.** Returns to Triage Mode.
- `q`: **Quit.** Exits to Free Write.

### 4. Mini Task Session
This mode is designed for rapid-fire task completion (e.g., clearing an email inbox or reviewing case statuses).

**Behavior:**
- **Manual Reset:** Use the **Space Bar** to manually reset the timer for the next sub-task.
- **Auto-Reset:** The timer automatically resets to full duration whenever you complete (`x`), cancel (`-`), defer (`>`), or prioritize (`N`) a task.
- **Persistence:** Once started with `m`, the mode stays active as you move through multiple tasks.
- **Exceeding Time:** If you exceed the allotted time, the timer will count negative values and play a "tick" sound immediately, and every 30 seconds thereafter, until you reset it.
- **Auto-Pause:** The mini timer is automatically paused and hidden during scheduled meetings or breaks. Resuming work will automatically reset the mini timer to its full duration.

### 5. Break Mode
Entered via `b` in Work Mode. Displays inspirational quotes and a countdown.

**Commands:**
- `w`: **Work.** Resumes the Work session.
- `n`: **Add.** Add notes or tasks during your break.
- `t`: **Triage.** Return to Triage mode.
- `q`: **Quit.** Exits the CLI.

## Markers
The ledger uses the following markers (Timestamp format: `MM/DD/YYYY HH:MM:SS AM/PM`):
- `------- Triage <Timestamp> -------`
- `------- Work <Timestamp> -------`
- `------- New Entry <Timestamp> -------`
- `------- Cancelled <Timestamp> -------`
- `------- Interrupted <Timestamp> -------`
- `------- Work Session Complete <Timestamp> -------`
- `------- Work Session Re-started at <Timestamp> -------`
- `------- Break for <mins> at <Timestamp> -------`
- `------- Deferred from last session <Timestamp> -------`
- `------- Edited <Timestamp> -------`
