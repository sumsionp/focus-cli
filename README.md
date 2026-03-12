# FocusCLI

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
- **Multi-line Grouping:** When adding tasks via `n` or `N`, the parser uses indentation to group items. Any line indented more than the preceding line is automatically treated as a sub-item (note or subtask) of that parent, ensuring that a new task and its subtasks are added to the stack as a single unit.

### Meeting Support
Tasks can be time-aware by including a time block. Meetings automatically preempt the current task when their start time arrives.
Supported formats:
- `2:00-3:00 PM`
- `2-3 PM`
- `11:00 AM-1:00 PM`
- `2 PM 2h 15m`

## Modal Workflow
The program has three modes: **Free Write**, **Triage**, and **Focus**.

### 1. Free Write Mode
The program automatically starts in Free Write mode upon launch. It appends a `------- Free Write ... -------` marker to your daily journal file and opens it in `vi`. This is where you enter notes and tasks freely. Once you save and exit the editor (`:wq`), you will be dropped into Triage Mode.

#### Program Launch
For the best experience, add an alias to your shell configuration (e.g., `~/.alias` or `~/.bashrc`):
```bash
alias focus='python3 ~/projects/focus-cli/focuscli.py'
```
Running `focus` will start your session in the daily file (e.g., `YYYYMMDD-plan.txt`).

### 2. Triage Mode
Entered automatically after the initial Free Write session or by using the `t` command. It parses the end of the file for new notes and pending tasks.

**Features:**
- **Smart Sorting:** Meetings are automatically moved to the bottom in chronological order, while currently active meetings stay at the top.
- **Focus Timer:** The session timer is integrated into the header and counts down in real-time. It turns red if the focus limit is exceeded.

**Commands:**
- `p <src> <dest>`: **Prioritize/Reorder.** Moves item at index `<src>` to `<dest>`.
- `a <note_idx> <task_idx>`: **Assign.** Moves a note (or task) at `<note_idx>` to be a sub-item of task at `<task_idx>`.
- `e <idx>`: **Edit.** Opens the item and its sub-items in `vi` for editing.
- `f`: **Free Write.** Appends a Free Write marker and opens the journal file in `vi`.
- `i <idx>`: **Ignore.** Removes a note from the stack. If it's a task, marks it as cancelled `[-]`.
- `N`: **Prioritize.** Opens `vi` to add tasks/notes. Supports one-line addition: `N "[] Task"`. New top-level tasks are inserted at the top.
- `N#`: **Prioritize at index.** Same as `N` but inserts new top-level tasks starting at index `#`. Leading sub-items target the task at index `#`.
- `n`: **Add.** Opens `vi` to add tasks/notes. Supports one-line addition: `n "[] Task"`. New top-level tasks are appended to the end.
- `n#`: **Add at index.** Identical to `N#`.
- `b <mins>`: **Break.** Enters Break Mode.
- `w`: **Focus.** Commits the triage session and enters Focus Mode.
- `q`: **Quit.** Exits the CLI.

### 3. Focus Mode
Entered by typing `w` from Triage Mode. It displays the top task along with its associated notes and subtasks.

**Features:**
- **Automatic Hierarchical Focus:** The system automatically drills down into the deepest pending subtask (`[]`). It presents the immediate parent as a header (`PARENT TASK >>`) and the sub-item as the active focus (`FOCUS >>`).
- **Visual Progress Bar:** Tracks your completion progress for the current task level (top-level or subtasks). It shows a bar like `[###     ] Completed 3/10` right above your focus. Cancelled and deferred tasks are counted as completed.
- **Automated Stack Rescue:** If the program is interrupted (e.g., via `Ctrl+C` or `SIGTERM`), it automatically "rescues" the current triage stack to the ledger to prevent data loss.
- **Task Timer:** Tracks time spent on the current task.
- **Focus Timer:** Countdown timer for the overall session.
- **Mini Task Timer:** A repeating timer for rapid completion of small focus items. (Mini timers remain active during meetings).
- **Auditory Feedback:** Chimes when timers expire.

**Commands:**
- `x`: **Done.** Marks the current focused item (and its sub-items) as complete `[x]`. Automatically advances to the next pending subtask or parent. (Also resets the Mini Timer if active).
- `x<idx>`: **Subtask Done.** Marks the subtask at `<idx>` relative to current focus as complete `[x]`.
- `e`: **Edit.** Opens the current focused item and its nested sub-items in `vi` for editing.
- `-`: **Cancel.** Marks the current focused item as cancelled `[-]`.
- `>`: **Defer.** Marks the entire current top-level task tree as deferred `[>]` and appends it as a top-level task to the specified target file (defaulting to the end of today's stack). (Also resets the Mini Timer if active).
- `f`: **Free Write.** Appends a Free Write marker and opens the journal file in `vi`. After editing, you return to Triage Mode.
- `m <mins>` or `m`: **Mini Task.** Toggles Mini Task Session mode (default 2 minutes).
- `[Space]`: **Reset Mini Timer.** When in Mini Task Session mode, resets the timer to its full duration (only works when command buffer is empty).
- `N`: **Prioritize.** Opens `vi` to add tasks/notes. Supports one-line addition: `N "[] Task"`. Indented items are added relative to current focus. New top-level tasks are inserted at index 0 (becoming the new focus).
- `N#`: **Prioritize at index.** Same as `N` but inserts new top-level tasks starting at index `#`. Leading sub-items target the task at index `#`.
- `n`: **Add.** Opens `vi` to add tasks/notes. Supports one-line addition: `n "[] Task"`. Indented items are added relative to current focus. New top-level tasks are appended to the end.
- `n#`: **Add at index.** Identical to `N#`.
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
- **Auto-Pause:** The mini timer is automatically paused and hidden during breaks. Resuming Focus session will automatically reset the mini timer to its full duration.

### 5. Break Mode
Entered via `b` in Focus Mode. Displays inspirational quotes and a countdown.

If a scheduled meeting starts while you are on a break, the UI will turn red, a chime will sound, and the meeting details will appear in the status bar. The session remains in Break Mode, allowing you to finish your break or manually resume Focus session with `w`.

**Commands:**
- `w`: **Focus.** Resumes the Focus session.
- `n` / `N`: **Add.** Add notes or tasks during your break.
- `n#` / `N#`: **Add at index.** Add notes or tasks at a specific index.
- `t`: **Triage.** Return to Triage mode.
- `q`: **Quit.** Exits the CLI.

### Input Navigation
When entering commands or one-line tasks, the following keys are supported:
- **Left/Right Arrows:** Move cursor within the buffer.
- **Home / Ctrl+A:** Jump to the start of the line.
- **End / Ctrl+E:** Jump to the end of the line.
- **Delete / Ctrl+D:** Delete character at cursor.
- **Backspace:** Remove character before cursor (supports wrapped lines).

## Daily Scorecard
When you exit the CLI (via `q`), a **Daily Scorecard** is displayed. This provides a summary of your productivity for the session, categorized by:
- **Finished [x]:** Total tasks completed.
- **Cancelled [-]:** Total tasks cancelled.
- **Deferred [>]:** Total tasks deferred to another day or later in the stack.

Each category includes a detailed breakdown of **Top-level tasks** and **Subtasks**. Subtasks are uniquely identified by their parent path to ensure accurate counting even if multiple projects have subtasks with the same name (e.g., "Review").

## Markers
The ledger uses the following markers (Timestamp format: `MM/DD/YYYY HH:MM:SS AM/PM`):
- `------- Free Write <Timestamp> -------`
- `------- Triage Session Started at <Timestamp> -------`
- `------- Triage <Timestamp> -------`
- `------- Focus <Timestamp> -------`
- `------- New Entry(s) <Timestamp> -------`
- `------- Prioritized Entry(s) <Timestamp> -------`
- `------- Cancelled <Timestamp> -------`
- `------- Interrupted <Timestamp> -------`
- `------- Focus Session Complete <Timestamp> -------`
- `------- Focus Session Re-started at <Timestamp> -------`
- `------- Break for <mins> at <Timestamp> -------`
- `------- Deferred from last session <Timestamp> -------`
- `------- Edited <Timestamp> -------`

## Development

### Running Tests
To run the unit tests, use the following command from the project root:
```bash
python3 -m unittest discover tests
```
