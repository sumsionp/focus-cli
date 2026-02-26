# Future Roadmap

## UI & Interaction
- **Split-screen Task Editor** Explore the possibility of splitting the screen and only opening vi in the bottom part of the screen when adding or editing tasks and notes.
- **Triage as a Task** Currently, time spent on Triage isn't recorded in the ledger. The idea is to record this Triage time in the ledger.
- **Add Already Completed Task** Automatically complete top level notes that were entered prepended with '[x]'.
- **Improved Subnote Formatting** Automatically add '- ' to the beginning of subnotes if it isn't already there to improve markdown formatting of subnotes.
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
- **Multi-line Task Addition ('n' and 'N'):** Added the ability to enter multiple tasks and notes at once via `vi`, supporting hierarchical sub-items and prioritizing multiple tasks simultaneously.
- **Prioritized Add (N):** Implement the `N` command in Work mode to add a new task and immediately prioritize it as the active focus.
- **Task Timer vs Focus Timer:** Individual task tracking alongside overall session tracking.
- **Auditory Feedback:** System chimes for focus limits and break expirations.
- **Flexible Focus Duration:** The `f` command to adjust focus thresholds on the fly.
- **Meeting Support:** Time-aware tasks with auto-preemption and meeting timer.
- **Task Editing ('e'):** Drop to `vi` from Triage or Work mode to edit items.
- **Mini Task Session (m#):** Implement a manual-reset repeating timer for rapid completion of small focus items.
