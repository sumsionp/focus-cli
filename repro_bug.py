import sys
import os
import copy
from deepworkcli import DeepWorkCLI

def print_stack(cli):
    print("\nStack state:")
    for i, t in enumerate(cli.triage_stack):
        print(f"{i}: {t['line']}")
        for n in t['notes']:
            print(f"  {n}")

def run_test_scenario(name, mode, initial_stack, lines, cmd='n'):
    print(f"\n--- {name} (Mode: {mode}, Command: {cmd}) ---")
    cli = DeepWorkCLI()
    cli.commit_to_ledger = lambda label, items: None
    cli.mode = mode
    cli.triage_stack = copy.deepcopy(initial_stack)

    print(f"Adding lines:\n" + "\n".join(lines))

    items = cli._process_multi_line_input(lines)
    cli._handle_hierarchical_new_items(cmd, items)
    print_stack(cli)

if __name__ == "__main__":
    initial = [
        {'line': '[] Task 1', 'notes': []},
        {'line': '[] Task 2', 'notes': []}
    ]

    leading_mixed = [
        "  [] Sub 1",
        "  [] Sub 2",
        "[] New Top Task"
    ]

    # Scenario 8: Triage Mode leading subtasks with 'n'
    run_test_scenario("Scenario 8: Triage Mode leading subtasks with 'n'", "TRIAGE", initial, leading_mixed, 'n')

    # Scenario 9: Triage Mode leading subtasks with 'N'
    run_test_scenario("Scenario 9: Triage Mode leading subtasks with 'N'", "TRIAGE", initial, leading_mixed, 'N')
