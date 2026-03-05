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
The program automatically starts in Free Write mode upon launch. It appends a `------- Free Write ... -------` marker to your daily journal file and opens it in `vi`. This is where you enter notes and tasks freely. Once you save and exit the editor (`:wq`), you will be dropped into Triage Mode.

#### Program Launch
For the best experience, add an alias to your shell configuration (e.g., `~/.alias` or `~/.bashrc`):
```bash
alias focus='python3 ~/projects/deep-work-cli/deepworkcli.py'
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
- `N`: **Prioritize.** Opens `vi` to add one or more tasks/notes to the top of the stack.
- `n`: **Add.** Opens `vi` to add one or more tasks/notes to the end of the stack.
- `b <mins>`: **Break.** Enters Break Mode.
- `w`: **Work.** Commits the triage session and enters Work Mode.
- `q`: **Quit.** Exits the CLI.

### 3. Work Mode
Entered by typing `w` from Triage Mode. It displays the top task along with its associated notes and subtasks.

**Features:**
- **Automatic Hierarchical Focus:** The system automatically drills down into the deepest pending subtask (`[]`). It presents the immediate parent as a header (`PARENT TASK >>`) and the sub-item as the active focus (`FOCUS >>`).
- **Automated Stack Rescue:** If the program is interrupted (e.g., via `Ctrl+C` or `SIGTERM`), it automatically "rescues" the current triage stack to the ledger to prevent data loss.
- **Task Timer:** Tracks time spent on the current task.
- **Focus Timer:** Countdown timer for the overall session.
- **Mini Task Timer:** A repeating timer for rapid completion of small focus items. (Mini timers remain active during meeting subtasks).
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
- `N`: **Prioritize.** Opens `vi` to add tasks/notes. If input is indented, they are prioritized hierarchically before the current focus. Otherwise, they go to the top of the stack.
- `n`: **Add.** Opens `vi` to add tasks/notes. If input is indented, they are added as sub-items after the current focus. Otherwise, they are appended to the stack.
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
- `------- Free Write <Timestamp> -------`
- `------- Triage Session Started at <Timestamp> -------`
- `------- Triage <Timestamp> -------`
- `------- Work <Timestamp> -------`
- `------- New Entry(s) <Timestamp> -------`
- `------- Prioritized Entry(s) <Timestamp> -------`
- `------- Cancelled <Timestamp> -------`
- `------- Interrupted <Timestamp> -------`
- `------- Work Session Complete <Timestamp> -------`
- `------- Work Session Re-started at <Timestamp> -------`
- `------- Break for <mins> at <Timestamp> -------`
- `------- Deferred from last session <Timestamp> -------`
- `------- Edited <Timestamp> -------`

## Development

### Running Tests
To run the unit tests, use the following command from the project root:
```bash
python3 -m unittest discover tests
```
