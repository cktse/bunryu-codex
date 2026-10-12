# Daily JLPT N1

A Python 3.11+ standard-library application that generates six-question N1 lessons, reviews them, and saves them in SQLite. Email and Obsidian notes are derived from the same saved lesson. Generation, review, and delivery have separate retry states.

The [approved specification](n1-daily-email-design.md) defines the behavior. The previous [implementation](docs/history/implementation-2026-10-12/ARCHIVE.md), [specification](docs/history/n1-daily-email-design-2026-10-12.md), and [reconciliation](docs/history/spec-reconciliation.md) are archived for reference.

## Try the local preview

From this repository:

```sh
python3 -m n1_daily preview
python3 -m n1_daily validate
python3 -m unittest discover -s tests -v
```

Preview writes `lesson.html`, `lesson.eml`, `lesson.txt`, `lesson.md`, and `lesson.json` under the operating system's temporary directory (`n1-daily-preview`). Use `--output /tmp/n1-preview` to choose another directory. It uses the bundled sample when no selector is supplied, even with `--config`; it never opens a production database in that case.

HTML has six independent answer controls. Markdown uses collapsed Obsidian answer callouts. `preview --ruby-mode parentheses` provides the answer-reading fallback. Plain text puts every question before the answer key. Preview/validation check structure, not linguistic correctness or client compatibility.

## Configure

```sh
mkdir -p ~/.config/n1-daily
cp config.example.json ~/.config/n1-daily/config.json
cp secrets.example.json ~/.config/n1-daily/secrets.json
chmod 600 ~/.config/n1-daily/secrets.json
```

Edit these files yourself. Keep configuration, secrets, and SQLite outside iCloud and Obsidian vaults. The secret file must be owned by your user and have mode `0600` or `0400`; the application checks the opened file before reading it. No environment variables, dotenv files, or automatic configuration discovery are used.

Settings are documented in [specification §4](n1-daily-email-design.md#4-configuration). Defaults include Hong Kong dates, a 30-second network-operation timeout, HTML ruby, and `~/Library/Application Support/N1Daily/lessons.sqlite3`. Relative data paths resolve beside the config file; explicit CLI paths resolve from the current directory. `--db` overrides `db_path`.

For generation, set `llm_endpoint`, `generation_model`, and `review_model`, plus `llm_api_key` in secrets. The endpoint must implement the **Responses** contract, using HTTPS with a host/path and no credentials, query, or fragment. A Chat Completions endpoint is incompatible. Choose model names and reasoning efforts supported by your provider; there is no automatic fallback. Generation and review independently default to `medium` and accept `low`, `medium`, `high`, `xhigh`, or `max`.

Requests use `input`, `text.format: {"type":"json_object"}`, `reasoning.effort`, and `store: false`. Each request includes its own context. The timeout applies to network operations, not total lesson-generation time. See the official [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses).

For Gmail, set `sender`, `recipient`, and `google_client_id`. Obtain an offline refresh token for your Google OAuth client with `https://www.googleapis.com/auth/gmail.send` permission, then put `google_client_secret` and `google_refresh_token` in secrets. The application refreshes access tokens when sending; it does not implement the initial authorization flow. See [Google offline authorization](https://developers.google.com/identity/protocols/oauth2/web-server#offline).

For Obsidian, set `vault_path` to an **existing** vault directory. The application writes `Lessons/YYYY/MM/YYYY-MM-DD.md` and `Index.md`, and links to `Notes/YYYY-MM-DD.md`. It never creates or edits personal notes. Vault configuration may be repaired after a setup failure, but a bound lesson/index destination cannot be changed automatically.

Unused service settings may remain empty. Invalid configuration is rejected before opening SQLite; missing service credentials are recorded when that service is needed.

## Generate and inspect

```sh
python3 -m n1_daily generate \
  --config ~/.config/n1-daily/config.json \
  --secrets ~/.config/n1-daily/secrets.json

python3 -m n1_daily status \
  --config ~/.config/n1-daily/config.json \
  --date 2026-10-12

python3 -m n1_daily preview \
  --config ~/.config/n1-daily/config.json \
  --date 2026-10-12
```

`generate --date YYYY-MM-DD` also supports past or future dates, at any hour. An assignment and its history are saved once. One correction is permitted for failed content; HTTP/setup failures retain the current phase and correction budget. Ready lessons are reused without an LLM request. Generation never delivers anything.

Failures print their recorded reasons directly. `status` shows phase, draft count, channel states, timestamps, and errors. A failed status with phase `generating`, `checking`, or `reviewing` can resume after repair by running the same command. Phase `failed` is terminal. Provider messages, HTTP status, error codes, request IDs, and Retry-After are retained with credential redaction. An HTTP 429 may mean exhausted quota; follow its details rather than assuming waiting will fix it.

The authoritative diagnostics are in SQLite. Manual output appears in your terminal; there is no separate application log. Details discarded by the old implementation cannot be recovered retroactively.

`preview/validate --date` read an existing ready lesson and its saved assignment. `--lesson PATH` checks a standalone JSON file; adding explicit `--config` or `--db` also requires that date's saved assignment/history. `--lesson` and `--date` are mutually exclusive. Read-only commands do not migrate databases, change permissions, recover active sends, or read secrets. No `dry-run` alias exists.

## Deliver explicitly

After reviewing the saved lesson and configuring the intended destinations:

```sh
python3 -m n1_daily publish \
  --config ~/.config/n1-daily/config.json \
  --secrets ~/.config/n1-daily/secrets.json \
  --activate-email --activate-vault
```

Use either activation flag alone to request one channel. `publish` requires today's ready lesson and does not generate. `daily` generates/resumes today and then publishes through its activated channels. Both enforce today's Hong Kong date and never scan old dates for delivery. There are no application clock windows or cutoff closures. Legacy `generate_time`, `publish_time`, and `cutoff_time` values are accepted as valid `HH:MM` strings and ignored.

Email and vault work are independent. Re-running skips completed channels, and an index retry does not resend email or rewrite a completed note. Before unfinished delivery, the application rechecks saved content/review integrity. Before first publication it also checks intervening teaching history. A stale pre-generated lesson can be blocked; automatic replacement is not supported.

The first email attempt saves complete MIME bytes, Date, Message-ID, sender, and recipient. Retries use those exact bytes; a changed address is rejected before service contact. Gmail acceptance establishes API acceptance, not guaranteed receipt.

A lost or ambiguous send response leaves email `unknown`, stopping automatic resend. Check the sending mailbox, then record one decision:

```sh
python3 -m n1_daily resolve-email \
  --config ~/.config/n1-daily/config.json --date 2026-10-12 \
  --mailbox-checked --retry

# Alternatively, record confirmed acceptance with its actual offset-bearing time:
python3 -m n1_daily resolve-email \
  --config ~/.config/n1-daily/config.json --date 2026-10-12 \
  --mailbox-checked --accepted-id ACTUAL_GMAIL_ID \
  --accepted-at 2026-10-12T08:01:00+08:00
```

These commands only change metadata. They require an existing send record; accepted time must be between the send attempt and now. A later publication command performs an authorized retry, still subject to today's-date rule. Confirmation after another channel succeeded records the email time without shifting the existing publication/review schedule.

Untracked files and unexpected edits, including `Index.md`, are preserved. An error identifies the conflicting path. Review and back up your changes before resolving a conflict manually; no force-overwrite command is provided.

## Optional scheduling

```sh
python3 -m n1_daily schedule \
  --config ~/.config/n1-daily/config.json \
  --secrets ~/.config/n1-daily/secrets.json \
  --output /tmp/n1-daily.plist
```

This only exports an inactive template. **Edit its timing and channel arguments before installation:** it contains `RunAtLoad`, a 60-second interval, and both activation flags. Loading that template unchanged would immediately permit generation and both deliveries, with repeated retries. External scheduling controls the desired approximately 08:00 execution and retry pacing. No scheduler is installed or loaded by this command.

The template sends stdout/stderr to `/tmp/n1-daily-launchd.log` and `/tmp/n1-daily-launchd-error.log`. These temporary logs supplement the saved SQLite reasons. Unchanged repeated daily failures do not repeatedly notify on macOS.

## Implementation and limits

- `content.py` and `render.py` handle checks and presentation; `llm.py`, `gmail.py`, and `http.py` handle services and diagnostics. `store.py` owns SQLite transactions, `workflow.py` owns phase/channel decisions, and `files.py` owns atomic vault writes. CLI/configuration are separate.
- Completed draft/check/review decisions are committed with their next phase. A SHA-256 binding covers content, assignment, frozen history, selected review stages, and review. It detects subsequent edits, not malicious database rewriting. A crash before a response is saved can require another request.
- Schema migration is additive and retains existing lessons, attempts, phases, and channel state. Legacy ready lessons need valid mechanical checks and a complete saved review before receiving an integrity baseline. Reconstructed context is marked in `status`. These baselines cannot prove content or context before migration; no LLM request is needed for migration.
- A nonblocking operating-system lock at `<resolved-db-path>.lock` guards writable commands across dates. SQLite transactions alone do not cover service calls. Overlap exits 2; process exit releases the lock automatically, even if its file remains. Readers need no lock.
- Error bodies are read up to 64 KiB plus one truncation-detection byte; saved diagnostics are capped at 4,096 characters with marked truncation. Successful JSON envelopes have a 2 MiB limit. Credential redaction precedes diagnostic truncation and handles a known credential split at the body boundary. Outgoing authorization values, request bodies, and reasoning traces are not logged.
- Exit codes: **0** success/reused/completed work; **1** failed generation/content or incomplete requested delivery; **2** invalid input/configuration/storage or an overlapping run.

Offline tests cover application behavior using temporary storage, fake services, and fake clocks. Live provider quality, received Gmail in Mac/iPhone Apple Mail, Obsidian on both devices, offline/enlarged-text interactions, iCloud synchronization, launchd/sleep-wake behavior, and actual notifications still require separate acceptance checks. Keep existing practice delivery until acceptance and cutover are agreed.
