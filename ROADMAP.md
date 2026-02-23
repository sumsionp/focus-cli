# Future Roadmap

## UI & Interaction
- **Add Already Completed Task** Automatically complete top level notes that were entered prepended with '[x]'.
- **Improved Subnote Formatting** Automatically add '- ' to the beginning of subnotes if it isn't already there to improve markdown formatting of subnotes.
- **Subnotes and Subtasks while adding New Task** Implement the ability to add subnotes and subtasks while adding a new top-level note in Work or Triage Modes. Could use \n as a delimeter or Shift-Enter to move to a new line.
- **Search** Implement '/' command to search for matching tasks in the triage stack. This is primarily for verifying whether the user has already entered a matching new task to avoid duplicates.
- **Rename Work Mode to Focus Mode:** Update terminology throughout the app and documentation to use "Focus" instead of "Work".
- **Selector-based Navigation:** Implement `j/k` for navigation and `CTRL+hjkl` for reordering/indenting in Triage mode, replacing or supplementing the current numbered command system.
- **Deadline Timer:** Countdown in the Work Mode header for tasks with specific time-of-day deadlines.

## Ledger Improvements
- **Action-Specific Markers:** Transition from generic markers (like `------- Work -------`) to more specific ones like `------- Completed -------` or `------- Deferred -------` to improve ledger auditability.
- **Session Markers:** Implement markers for `Free Write Session` to better segment purely editorial time.

## Fixes & Polish
- **SIGINT Rescue:** Ensure that exiting via `Ctrl+C` (SIGINT) also triggers a rescue append of pending tasks, similar to the `q` command.
- **Improved Deduplication:** Further refine the `load_context` parser to handle complex reordering and nesting edge cases more robustly.

## Completed Items
- **Prioritized Add (N):** Implement the `N` command in Work mode to add a new task and immediately prioritize it as the active focus.
- **Task Timer vs Focus Timer:** Individual task tracking alongside overall session tracking.
- **Auditory Feedback:** System chimes for focus limits and break expirations.
- **Flexible Focus Duration:** The `f` command to adjust focus thresholds on the fly.
- **Meeting Support:** Time-aware tasks with auto-preemption and meeting timer.
- **Task Editing ('e'):** Drop to `vi` from Triage or Work mode to edit items.
- **Mini Task Session (m#):** Implement a manual-reset repeating timer for rapid completion of small focus items.
