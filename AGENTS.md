# Repository Guidelines

## Specification-Driven Workflow

Treat `n1-daily-email-design.md` as the product requirements. For every system change, update the specification first, present it for review, and pause until explicit user approval. Then begin implementation and tests. During review, edit only the specification and contributor guidance. Choose straightforward implementations and record decisions in `README.md`. Ask clarification questions only when a decision materially changes the requirements.

## Project Structure & Module Organization

Modules live in `n1_daily/`, tests in `tests/`, and the complete sample lesson in `examples/`. SQLite lesson JSON is authoritative; email and Obsidian notes are derived outputs. Keep generation and review separate from rendering and delivery so ready lessons remain usable without LLM access.

## Build, Test, and Development Commands

Use Python 3.11 or later and the standard library. Run `python3 -m unittest discover -s tests -v` for tests. Run `python3 -m n1_daily preview` or `python3 -m n1_daily dry-run` to produce email and Markdown previews without delivery. See `README.md` for setup and other commands.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and modules, and `PascalCase` classes. Prefer small functions and explicit state transitions. No formatter or linter is required.

## Tone & Judgement

Prioritize readability. Write natural prose in English, Japanese, and Cantonese; use traditional Chinese characters for Cantonese. English should flow like a native speaker's prose. Apply technical terms precisely and consistently. Avoid confusing, meandering sentences and unnecessary “this is X, not Y” constructions.

## Testing Guidelines

Test-driven development is the default. Write meaningful `unittest` cases in `tests/test_*.py` before implementing behavior. Cover content checks, incomplete reviews, correction limits across restarts, independent channel retries, uncertain Gmail outcomes, publication accounting, and edited-note preservation. Use temporary directories and fake services. Document checks requiring actual Apple Mail and Obsidian clients; browser previews cannot establish client compatibility.

## Commit & Pull Request Guidelines

Use imperative commit subjects, such as `Add vocabulary lookup`; no Git convention is established. Pull requests should describe behavior and validation, link issues, and identify outstanding client checks.

## Security & Configuration

Use explicit `--config` and `--secrets` JSON file arguments with no environment fallback. Keep private files and SQLite outside iCloud and the vault. Validate secret-file ownership and permissions, load credentials only for service requests, and exclude values from diagnostics and artifacts. Never send email, write into the real vault, or enable scheduling until explicitly requested. Keep previews temporary. Follow the specification's implementation stages; finish approved work with a locally runnable prototype and concise setup instructions.
