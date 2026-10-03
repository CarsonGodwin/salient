"""Stub critic: saves the prompt it received, prints a canned response.
STUB_PROMPT_OUT: where to save the prompt. STUB_RESPONSE: what to print."""
import os
import sys

prompt = sys.stdin.read()
out = os.environ.get("STUB_PROMPT_OUT")
if out:
    with open(out, "a") as f:
        f.write(prompt + "\n=====\n")
sys.stdout.write(os.environ.get("STUB_RESPONSE", '{"findings": []}'))
