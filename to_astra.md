# Review of `research-graph/tests.md`

Repository: `/home/ryan/research-graph`. Reviewed on 2026-09-23 against commit `6479afb` plus the uncommitted work in progress at that time.

It's an accurate map of the test suite as committed, but it has already drifted from the working tree. A few of its own claims about how it was made and what it measures don't hold up.

**What I checked, mechanically:**
- **Names:** all 228 test names the doc cites exist, and each is listed under the file that defines it.
- **Counts:** every bracketed case count matches what pytest collects.
- **"Not covered" items 1 and 2 are correct.** The stand-in integrator in the tests always returns `research_needed: False`, so the send-back path is never run. No test mentions `undisclosed_research_gap`.

The grouping by behavior and the "Not covered" section are the best parts of the doc.

## Problems

1. **It's stale against the working tree.** `tests/test_definitions.py` (4 tests) has no section. It and `tests/support.py` are new and uncommitted, and eight other test files have uncommitted edits. The totals are now 232 functions in 26 files with 344 cases, not 228, 25 and 340. The doc will be wrong as soon as that work is committed.

2. **Three of the four "Not covered" line references are off**, even against the committed code:
   - `workflow.py:525-529` stops before the send-back branch (lines 530-532).
   - `workflow.py:604` points at a `checkpoint()` call; the downgrade is on line 607.
   - `replay.py:341-363` starts inside `build_dataset`; `main()` begins at line 347.

   Commit `665be90` already fixed these references once. Naming the code instead, such as "the `research_needed` branch in `integrate()`", won't drift.

3. **The 92% coverage figure can't be reproduced.** `pytest --cov` needs `pytest-cov`, which isn't installed or declared in `pyproject.toml`, so I couldn't check the number. Add it as a dev dependency, or say where the figure came from.

4. **"Taken from each test's name and docstring" isn't accurate.** 190 of the 232 tests have no docstring, so most of the longer descriptions were written from the assertions. Those can overclaim without anyone noticing. For example, the verifier entry says thinking happens "only at `xhigh`/`max`", but the test only tries no setting, `high` and `xhigh`; `max` is never exercised.

5. **"No test calls a real model or the network" isn't enforced.** There's no `conftest.py` and nothing blocks sockets, so one careless test could break the claim silently. A small `conftest.py` that refuses outbound connections would make it true by design.

6. **One entry is easy to misread.** "A research request can take a second round" tests the verifier's route back to research (`decide->research`), not the integrator's. Next to "Not covered" item 1, a reader could easily think the integrator path is tested. Name the route in the entry.

## Suggested changes

- Add a section for `tests/test_definitions.py` once it is committed, and update the totals.
- Replace the line numbers under "Not covered" with function and branch names.
- Declare `pytest-cov` as a dev dependency, or remove or source the 92% figure.
- Correct "taken from each test's name and docstring", and the `xhigh`/`max` wording.
- Add a `conftest.py` that blocks outbound sockets.
- Name the `decide->research` route in the second-round entry.
- Stop maintaining the mechanical parts by hand: turn the check below into a test that fails whenever a test is missing from the doc, listed under the wrong file, or its case count changes. Then only the prose and the "Not covered" analysis need human upkeep.

## Appendix: the check script

Run it from the repository root. It compares the test names, files and bracketed counts in `tests.md` with the test functions in `tests/` and with pytest's collection.

```python
import ast, re, subprocess, collections
from pathlib import Path

root = Path(".")
doc = Path("tests.md").read_text()

# Doc: test name -> (file section, bracketed count)
section_file = None
doc_tests = {}
for line in doc.splitlines():
    m = re.search(r"`(tests/test_\w+\.py)`", line)
    if m:
        section_file = m.group(1)
    for name, count in re.findall(r"`(test_\w+)`(?:\s*\[(\d+)\])?", line):
        doc_tests.setdefault(name, []).append((section_file, int(count) if count else None))

# Code: test functions per file
code_tests = {}
for path in sorted(root.glob("tests/test_*.py")):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            code_tests.setdefault(node.name, []).append(str(path))

print("test files:", len(list(root.glob("tests/test_*.py"))),
      "| test functions:", sum(len(v) for v in code_tests.values()))
print("In doc, not in code:", sorted(set(doc_tests) - set(code_tests)))
print("In code, not in doc:")
for name in sorted(set(code_tests) - set(doc_tests)):
    print("  ", code_tests[name][0], name)
print("Listed under the wrong file:")
for name, entries in doc_tests.items():
    for section, _ in entries:
        if name in code_tests and section not in code_tests[name]:
            print("  ", name, "doc:", section, "code:", code_tests[name])

# Collected cases per test function
out = subprocess.run([".venv/bin/python", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                     capture_output=True, text=True).stdout
cases = collections.Counter()
for line in out.splitlines():
    m = re.match(r"(tests/test_\w+\.py)::(test_\w+)", line)
    if m:
        cases[m.group(2)] += 1
print("collected cases:", sum(cases.values()))
print("Bracket count mismatches (doc vs collected):")
for name, entries in doc_tests.items():
    for _, count in entries:
        if (count or 1) != cases.get(name, 0):
            print(f"   {name}: doc [{count}] collected {cases.get(name, 0)}")
```
