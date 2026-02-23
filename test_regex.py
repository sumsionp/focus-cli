import re
cmd = "m5"
cmd_clean = re.sub(r'^([a-zA-Z])(\d)', r'\1 \2', cmd)
parts = cmd_clean.split()
print(f"cmd_clean: '{cmd_clean}'")
print(f"parts: {parts}")
