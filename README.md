# Daily JLPT N1

A local Mac application for six original N1 practice questions per day, with email and Obsidian versions of the same lesson. [The specification](n1-daily-email-design.md) defines the product requirements. The prototype runs locally; live delivery and scheduling require explicit activation.

A side research goal is to experiment with Codex and GPT-6.1-Sol at high reasoning effort, building on prior experience with Claude Code and Opus 4.8, 5.0, and 5.5. The comparison will examine their development capabilities and how naturally they write prose in English, Japanese, and Cantonese using traditional Chinese characters.

## Run the prototype

Use Python 3.11 or later. No third-party packages, installation step, or build is required. Run commands from this repository's directory.

`preview` renders the bundled sample unless you select another input. It creates local preview files without network access, secret-file loading, email delivery, vault writes, or publication accounting. Use it to inspect the email and Obsidian versions of a lesson.

| Command | What it does |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | Runs the automated test suite using temporary directories and fake services. |
| `python3 -m n1_daily preview --output /tmp/n1-daily-preview` | Checks the bundled sample's structure and renders both email and Obsidian versions into the chosen directory. |
| `python3 -m n1_daily validate --lesson examples/sample-lesson.json` | Runs mechanical content checks on the sample without creating preview files. It does not assess linguistic correctness through an LLM. |

`preview` produces these files in its output directory:

| File | Purpose |
| --- | --- |
| `lesson.html` | HTML email preview; open it in Safari or another browser. |
| `lesson.eml` | MIME email containing HTML and plain-text alternatives; open it locally in Apple Mail. |
| `lesson.txt` | Plain-text email alternative, with all questions before the answer key. |
| `lesson.md` | Obsidian lesson with collapsed answer callouts. |
| `lesson.json` | Copy of the lesson content used to create the previews. |

Use `--lesson /path/to/lesson.json` to select a JSON input and run its mechanical checks. Use `--date YYYY-MM-DD` to select an existing **ready** lesson from SQLite. For example, after a lesson has been generated and saved:

```sh
python3 -m n1_daily preview --config /path/to/config.json --date 2026-10-09 --output /tmp/n1-saved-lesson
```

The selected date must already have a ready record; `preview` does not generate missing content. If both `--date` and `--lesson` are supplied, `--date` takes precedence. Neither private file is needed for the bundled sample; saved content needs its database location through `--config`, `--db`, or the default path.

`--output` overrides the configured `preview_path`. If neither is supplied, files go into `n1-daily-preview` under the operating system's temporary directory. Output directories inside a configured vault, an identifiable Obsidian vault, iCloud, or a launchd job directory are rejected.

The authored sample includes all six question formats assigned to the first day, Japanese explanations, selective answer ruby, and English/Cantonese supplements. It has passed mechanical checks. It has not undergone a live model review. Test review verdicts are synthetic fixtures used to exercise the gate.

## Configuration and saved lessons

Copy `config.example.json` and `secrets.example.json` into a private directory outside iCloud and the Obsidian vault, naming them `config.json` and `secrets.json`. Fill the needed settings and credentials, then restrict the secret file to your user with `chmod 600 /path/to/secrets.json` (or `400` for read-only use). The loader checks ownership and permissions before reading secrets. Both private filenames are ignored by Git.

Pass paths explicitly with `--config` and `--secrets`. The application reads JSON directly and passes credentials to service clients in memory. Application environment variables and dotenv files are no longer supported. Private files are never discovered automatically. The [configuration table](n1-daily-email-design.md#configuration-and-secrets) defines each field, its requirements, default, and an example.

Set `llm_endpoint`, `generation_model`, and `review_model` in `config.json`, and `llm_api_key` in `secrets.json`. The endpoint must accept `messages` and `response_format: {"type":"json_object"}`, and return JSON text in `choices[0].message.content`. Providers with another protocol need a separate adapter. All service requests use a finite configurable timeout.

```sh
python3 -m n1_daily generate --config /path/to/config.json --secrets /path/to/secrets.json
python3 -m n1_daily status --config /path/to/config.json
python3 -m n1_daily preview --config /path/to/config.json --date 2026-10-09
```

`generate` saves content and its matching structured review before readiness. `preview --date` renders a saved ready lesson without consulting the LLM. SQLite defaults to `~/Library/Application Support/N1Daily/lessons.sqlite3`; `db_path` or `--db` can select another location outside iCloud and the vault. Previewing the sample does not create a database.

`--config` is required for `generate`, `publish`, `daily`, and `schedule`. Sample previews work without either private file. Secrets are loaded only when generation, review, or an unfinished Gmail send requires them. Saved ready lessons and completed channels remain usable without LLM credentials; vault delivery needs no secret file. Missing or invalid email credentials produce a definite channel failure, allowing vault delivery to continue.

Relative data paths are resolved against the configuration file's directory; `~` expands to the user's home directory. CLI overrides take precedence and resolve relative paths against the working directory. The example omits `preview_path` to use the operating system's temporary directory. Malformed JSON, duplicate/unknown keys, incorrect types, and misplaced credentials fail with field-specific diagnostics that omit values.

## Implementation choices

- Python's standard library supplies SQLite, Unicode processing, MIME email, HTTP, tests, and macOS file locking. Modules separate content checks, rendering, persistence, generation, and delivery.
- The first three slots cover kanji, vocabulary, and grammar. Paraphrase, usage, and reading-comprehension slots use vocabulary targets; ordering uses grammar targets. Themes and format rotation advance once on first publication. Due reviews fill compatible slots, with a daily cap of two.
- Ruby ranges use NFC text and half-open Unicode code-point offsets. `distractors_ja.B` identifies a distractor field. Markdown uses inline ruby by default; `"ruby_mode": "parentheses"` or `--ruby-mode parentheses` provides the answer-only hiragana fallback.
- A persisted generation phase resumes an interrupted request or review. One corrected draft is allowed; exhausted quality failures remain failed across restarts. Transport failures remain retryable in the publication window. Complete drafts and verdicts are saved before delivery.
- Each channel retains its own result. Email bytes and a `sending` marker are committed before Gmail contact. Interrupted sends become `unknown`; definite rejection is retryable. Saved bytes are reused. SQLite transactions record first publication and review exposure together.
- Markdown files and the index use atomic replacement with persisted checksums. Unexpected edits are preserved and reported. A pending checksum permits recovery after a completed write interrupted before status recording. Personal notes are never created or modified.

## Delivery and scheduling after activation

Gmail configuration requires one sender, one recipient, OAuth client credentials, and an offline refresh token with `https://www.googleapis.com/auth/gmail.send` permission. Follow Google's [sending guide](https://developers.google.com/workspace/gmail/api/guides/sending) and [offline OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server). The adapter refreshes access before sending base64url-encoded multipart MIME through the [Gmail send endpoint](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).

Set `vault_path` to the vault created with Obsidian's iCloud option. Set `sender`, `recipient`, and `google_client_id` in `config.json`, and `google_client_secret` and `google_refresh_token` in `secrets.json` for Gmail delivery. Activated publication writes `Lessons/YYYY/MM/YYYY-MM-DD.md` and `Index.md`, with a link to `Notes/YYYY-MM-DD.md` for your own notes.

After an explicit activation request, `publish --activate-email --activate-vault` sends today's saved ready lesson during the publication window; either flag can activate one channel. `daily` accepts the same flags and generates only when needed. It uses Asia/Hong_Kong, defaults to generation from 07:45, publication from 08:00, and a noon cutoff. It skips completed channels and never sends a backlog.

Export a reviewable, inactive [launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) job:

```sh
python3 -m n1_daily schedule --config /absolute/path/config.json --secrets /absolute/path/secrets.json --output /tmp/n1-daily.plist
```

The export polls every minute and invokes the daily runner with both activation flags and absolute JSON file paths. It records the secret-file path without reading its contents or embedding credentials or application environment variables. It does not install or load a job. Installing the artifact enables live delivery and must wait for explicit activation. The designated Mac must be available; late runs publish only until noon. Failures appear in status and trigger a Mac notification.

For `unknown` email outcomes, inspect the sending mailbox. `resolve-email --date YYYY-MM-DD --mailbox-checked --retry` permits a later retry; alternatively use `--accepted-id ID --accepted-at TIME_WITH_OFFSET` to record confirmed acceptance. Neither resolution command sends mail. Published content is preserved; automatic corrections and replacement emails are excluded.

## Validation before daily use

Tests cover malformed content and ruby, ambiguous/incomplete reviews, correction limits, outages, independent channel retries, crash recovery, review dates, duplicate runs, timing, and edited-file conflicts. Configuration tests cover defaults, overrides, permissions, ownership, secret isolation, and credential-free reuse of saved lessons. Temporary directories and fake services keep tests offline.

The user ran `python3 -m n1_daily preview --output /tmp/n1-daily-preview` and confirmed correct example-lesson rendering in these local previews:

| Output | Client | Result |
| --- | --- | --- |
| `lesson.html` | Safari | Rendering confirmed |
| `lesson.md` | Obsidian | Rendering confirmed |
| `lesson.eml` | Apple Mail on macOS, opened as a local file | Rendering confirmed |

Received Gmail messages, iPhone clients, offline answer controls, enlarged-text legibility, and iCloud synchronization still require verification.

The following remain actual-client acceptance checks:

- Receive an activated test email in Apple Mail on Mac and iPhone. Confirm six initially hidden answers, independent reversible controls, legible Japanese/Cantonese/ruby at enlarged sizes, and answer-free subject/preview. Mail clients may change HTML and CSS; a browser preview establishes only browser behavior.
- Open an activated note in Obsidian Reading view on both devices. Confirm native [collapsed callouts](https://obsidian.md/help/callouts), ruby or its documented fallback, note links, and iCloud index/archive synchronization.
- Download both outputs, go offline, and confirm every answer still reveals. Manually assess sample difficulty and linguistic accuracy, then verify the configured LLM and Gmail credentials in an authorized integration test.

Keep the existing chat practice delivery until these checks pass and cutover is explicitly agreed.
