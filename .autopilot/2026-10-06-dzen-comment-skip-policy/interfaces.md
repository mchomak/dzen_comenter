# Interfaces and project constraints

## Boundaries decided in the spec

- `DameoPromptBuilder.build(PromptContext) -> str` owns prompt composition and the order of instruction blocks.
- Prompt configuration (`PromptBrandConfig`) owns editable brand instructions and task text. A fixed answer-default policy is appended after all configurable instructions so existing local runtime settings cannot override it.
- The prompt layer tests exercise `DameoPromptBuilder.build(...)` directly; no model, network, database, classifier, queue, or publication calls are needed.
- The fixed policy sits immediately before the existing output-format contract. Existing `SKIP` parsing and output syntax remain unchanged.
- The current restricted-topic list remains authoritative. `SKIP` is reserved for those topics, clear advertising spam, and empty or wholly unreadable comments. Partially understood comments receive a safe answer or a clarification question.

## Project rules

- Python 3.11; run focused tests with `.venv\Scripts\python.exe -m pytest -q tests/prompt` and the full suite with `.venv\Scripts\python.exe -m pytest -q`.
- `TEST_DATABASE_URL` must be unset for the full local suite; database fixtures reset the configured test schema.
- Do not edit the ignored `config/runtime_config.json`; do not access production or publish replies.
- Keep the change within the prompt builder/defaults, tracked prompt examples, prompt tests, and this run's audit artifact. Preserve the existing unrelated dirty worktree.
- Commit only the stage's own tracked source/test files after verification. The project workflow requires a separate read-only tester verdict against the stage note before marking it complete.

## Acceptance seam

Regression tests build prompts with both defaults and custom instructions containing the old broad `SKIP` guidance. They verify that the fixed answer-default policy follows those instructions, protects the suspended-toilet criticism example, preserves the output contract, and leaves the existing restricted-topic behavior intact.
