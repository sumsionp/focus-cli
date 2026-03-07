# FocusCLI Agent Instructions (v3.5)

## Core Philosophy: The Ledger
This program is a "lens" for a chronological journal. 
- **Rule 1:** Append, don't edit. We preserve history.
- **Rule 2:** Use Markers (e.g., `------- Triage ... -------`) to denote blocks of time.
- **Rule 3:** The "Free Write" is always the section after the very last marker in the file.

## Syntax & Hierarchy
- **Tasks:** Lines starting with `[]`, `[ ]`, `[x]`, `[-]`, or `[>]`.
- **Hierarchy:** Two leading spaces indicate a child relationship (subtask or note) to the task above.
- **Notes:** Anything not matching a task marker.

## Modal State Machine
- **Free Write (via vi):** User enters data. 
- **Triage:** Parsed from Free Write. Uses numbered commands: `p` (prioritize), `a` (assign), `i` (ignore).
- **Focus (w):** Focused UI. Commands: `x` (complete), `n` (add task/note), `-` (cancel), `>` (defer), `b` (break), `f` (focus duration).
- **Break (b):** Visual countdown. If a new meeting starts during a break, the UI turns red ("!!! MEETING STARTING !!!"), a chime sounds, and the meeting name is shown in the status bar. The user must manually resume Focus session with 'w'.
- **Exit Logic:** Exiting from Focus/Triage (via `q`) must trigger a "Rescue Append" of pending items under an `------- Interrupted -------` marker. (Note: `SIGINT/Ctrl+C` support is currently pending).
