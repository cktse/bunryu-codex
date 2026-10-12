# Daily JLPT N1 — specification

**Status: approved on 2026-10-12.** The user explicitly approved the replacement specification, including C1–C8, and requested a fresh implementation with the previous code archived. This document is the implementation baseline. The [previous implementation](docs/history/implementation-2026-10-12/ARCHIVE.md) is preserved for reference. Approval covers implementation and offline tests; live services and delivery retain their explicit activation boundaries.

The [historical snapshot](docs/history/n1-daily-email-design-2026-10-12.md) preserves the complete preceding specification, its uncommitted edits, approval statements, and implementation evidence. Its sections 1–11 were the approved implementation baseline; its sections 12–13 were pending proposals. References to old section numbers below refer to that snapshot. The [reconciliation report](docs/history/spec-reconciliation.md) is archived alongside it as the historical 2026-10-09 audit. Neither historical file is an ongoing specification.

## 1. Purpose and scope

Produce one original JLPT N1 practice lesson for each date in Asia/Hong_Kong. Each lesson contains six questions with four options and one correct answer, covers kanji, vocabulary, and grammar, and aims to take 10–15 minutes. Use natural Japanese explanations and optional English or Cantonese supplements; Cantonese uses traditional Chinese characters.

The same saved lesson supplies a Gmail email and an Obsidian note. Answers start hidden and reveal independently, with selective hiragana readings inside answers. Downloaded outputs work offline. Publication establishes availability, not evidence of study or correctness.

Use Python 3.11 or later and the standard library. SQLite lesson JSON is authoritative; email, Markdown, and previews are derived outputs. One Mac, one writer, and one recipient are supported. Keep generation/review separate from rendering/delivery so a ready lesson remains usable without LLM access.

No learner accounts, scores, adaptive curriculum, catalog, automatic reserve-pool management, or daily human approval service are included. Manual generation for a selected future date remains supported. Automatic replacement of ready or published content, resend tooling beyond unknown-outcome resolution, and migration between vaults remain outside this revision.

Approximately 08:00 is the desired delivery time. The external scheduler controls execution and retry timing under C1; the application has no clock windows or cutoff closure.

## 2. Workflow

1. Create and save the date's assignment once.
2. Request a draft, perform mechanical checks, then request its content review.
3. If content fails, allow one corrected draft and repeat the checks.
4. Save the passing lesson and its matching review before marking it ready.
5. On a separate publication request, render the saved lesson and attempt only explicitly activated channels.
6. Record each channel independently. First confirmed publication advances teaching history and review exposure once.

A service or setup failure preserves progress for a later invocation. A content failure after the correction exhausts that date's budget. Delivery failures do not regenerate content.

The replacement specification is approved for implementation and tests. Future behavior changes require specification review before implementation. Live provider requests, email, real-vault writes, and scheduling activation require their respective explicit requests. Running the CLI with activation flags is an operator action; the application has no additional approval service.

## 3. Commands

Common arguments are explicit `--config`, `--secrets`, and `--db` paths. Commands load secrets only for an actual service request.

| Command | Input and behavior |
| --- | --- |
| `preview` | Validate input and write lesson.html, lesson.eml, lesson.txt, lesson.md, and lesson.json locally. Accept `--output` and `--ruby-mode`. No LLM, delivery, readiness, or exposure changes. |
| `validate` | Perform the same input checks without writing previews or changing readiness. |
| `generate` | Require `--config`. Generate or resume any selected `--date YYYY-MM-DD`, at any time; default to today's Hong Kong date. Reuse ready lessons and preserve assignment, saved phase, feedback, and correction budget. No publication. |
| `publish` | Require `--config` and at least one of `--activate-email` / `--activate-vault`. Deliver today's saved ready lesson; do not generate. Explicit `--date` must be today. There are no application timing windows under C1. |
| `daily` | Require `--config` and at least one activation flag. Generate/resume today, then publish its ready lesson through activated channels. Skip completed work and never scan older dates for delivery. There are no application timing windows under C1. |
| `status` | Read the selected date, defaulting to today, and report saved generation/delivery states and reasons. C2 additionally exposes saved phase and actionable diagnostics. No secrets, writes, schema creation, or send recovery. |
| `resolve-email` | Follow the one-active-run rule below when resolving a selected unknown/interrupted send after `--mailbox-checked`. Choose exactly one of `--retry` or `--accepted-id ID --accepted-at TIME_WITH_OFFSET`. Record the decision; do not send mail. C8 defines validation/accounting edge cases. |
| `schedule` | Require `--config`, `--secrets`, and `--output`. Export an inactive launchd plist with absolute paths, both activation flags, project working directory, RunAtLoad, and a 60-second template interval. Never install/load it or read secrets. The operator edits timing/channel arguments before installation. |

`dry-run` is removed, without an alias. Add no commands or flags in this revision.

For preview/validation, `--date` and `--lesson` are mutually exclusive:

| Input | Checks and limits |
| --- | --- |
| Neither selector | Bundled sample and its known first-day assignment. No production database access. Report that production history and LLM review were not checked. |
| `--date` | Require an existing ready record. Recheck mechanical content against its saved assignment and relevant earlier published history, excluding itself. Do not recompute the assignment. |
| `--lesson PATH` | Check standalone structure, supported theme/format mix, local uniqueness, and review-reference shape. With explicit `--config` or `--db`, also require that date's saved assignment and check relevant published history. Without that context, report that assignment/history checks were not performed. |

Saved previews, validation, and status open SQLite read-only and leave permissions and active sends unchanged. Missing records or required context are input errors. Validation does not assert linguistic correctness; a standalone file's ready marker or embedded review does not confer production readiness.

Exit codes: 0 for success or routine waiting/ready/completed outcomes; 1 for failed content checks, recorded generation failure, publication blocked by validation, or incomplete requested delivery; 2 for input/configuration/storage errors or overlapping runs. Failure output must explain the reason; C2 requires printing recorded diagnostics directly as well as through status.

## 4. Configuration

### Configuration and secrets

Read flat JSON objects through explicit file arguments; no automatic file discovery, dotenv, or application environment fallback. Expand `~`; resolve relative data paths against the configuration file's directory and relative CLI paths against the working directory. CLI overrides take precedence.

| Config field | Meaning and default |
| --- | --- |
| `db_path` | SQLite path; default `~/Library/Application Support/N1Daily/lessons.sqlite3`. `--db` overrides it. |
| `preview_path` | Preview directory; default n1-daily-preview under the operating system's temporary directory. `--output` overrides it. |
| `vault_path` | Existing Obsidian iCloud vault, required only for activated unfinished vault delivery. Default unset. C7 defines destination validation. |
| `sender`, `recipient` | One email address each, without a display name or list. Required for unfinished Gmail delivery. Unset previews use preview@example.test / reader@example.test. |
| `timezone` | Only Asia/Hong_Kong; default Asia/Hong_Kong. |
| `timeout_seconds` | Positive finite JSON number, at most 300; default 30. Network-operation timeout for LLM/OAuth/Gmail. It is not a whole-lesson completion guarantee. |
| `ruby_mode` | html or parentheses, default html. Selects Obsidian answer annotations; email retains HTML ruby. `--ruby-mode` overrides it for preview/publication. |
| `llm_endpoint` | HTTPS host and path, optional valid port, no userinfo/query/fragment. Required for generation/review; default unset. Authentication goes in the request header. |
| `generation_model`, `review_model` | Nonempty provider model names, required for generation/review; default unset. |
| `generation_reasoning_effort`, `review_reasoning_effort` | Independently default medium. Accept low, medium, high, xhigh, or max. The configured provider/model must support the selected effort. |
| `google_client_id` | Google OAuth application ID, required for unfinished Gmail delivery; default unset. |
| `generate_time`, `publish_time`, `cutoff_time` | Legacy HH:MM settings accepted for compatibility only. Ignore their values and apply no ordering checks. Omit them from the example configuration. |

Secrets fields are `llm_api_key`, `google_client_secret`, and `google_refresh_token`, each a nonempty string when its service needs it. Missing/empty unused service settings are permitted. The secret file must be a regular file owned by the running user with mode 0600 or 0400; verify the opened file before reading its contents.

Reject malformed JSON, duplicate/unknown keys, nulls, wrong types, non-finite numbers, and credentials in config.json. Invalid top-level configuration and unsafe SQLite paths fail before opening SQLite. Missing service settings or unusable secrets during an otherwise valid run become recorded service-specific failures; they do not block the other channel.

Keep config, secrets, SQLite, and its auxiliary files outside iCloud and any configured or identifiable Obsidian vault. Keep private files out of version control. Preview/schedule exports must stay outside iCloud, vaults, and launchd job directories. Previews default to temporary storage. Load credentials only for necessary service requests; never export them to the environment, child processes, artifacts, or diagnostics. Redact credentials from useful diagnostics rather than suppressing provider explanations (C2).

Keep config.example.json / secrets.example.json and concise setup instructions. There is no automatic protocol, model, effort, timeout, or endpoint fallback.

## 5. Assignment and content contract

Assignment slots 1–3 are kanji_reading / kanji, contextual_vocabulary / vocabulary, and grammar_selection / grammar. Slots 4–6 are three consecutive entries from the circular list paraphrase, usage, sentence_ordering, reading_comprehension. Paraphrase, usage, and reading comprehension use vocabulary targets; ordering uses grammar. With no publication, start at paraphrase.

Themes are 仕事, 社会, 文化, 科学, 環境, 旅行, 趣味. Theme and format position advance once per first publication, not per generation request or successful channel. Assignments are fixed when created; pre-generated dates do not reserve unpublished targets or predict later publications. C5 defines intervening-history handling.

Use at most two review slots. Choose oldest due compatible items, then original question ID, then original lesson ID. Use each original item once in an assignment; retain postponed items. A review preserves the original subject/target and tests it in a new context. New targets differ from known published subject/target pairs. Review delays are 1, 3, 7, and 14 days: initial publication starts the first delay; each review publication starts the next. Complete the sequence after four reviews.

Canonical content is NFC plain Unicode, without embedded HTML. Renderers escape literal HTML/Markdown syntax instead of treating lesson text as markup. No added phonetic hints appear in questions; reading choices in a kanji-reading question are part of the exercise. Readings and explanations belong in answers.

| Lesson content | Required structure |
| --- | --- |
| Identity | `date` in YYYY-MM-DD; `lesson_id` exactly n1- followed by that date; assigned Japanese `theme`; integer `estimated_minutes` from 10 to 15. |
| Questions | Six objects in display order, with IDs q1–q6 and assigned `type`, `subject`, and `review_of`. |
| Target/reference | Nonempty `target`; `review_of` is null for a new item or an object containing nonempty `lesson_id` and `question_id` for the original published question. |
| Exercise | Nonempty `prompt_ja`; `options` is exactly A–D mapped to distinct nonempty strings; `correct_option_id` is A–D. |
| Answers | Nonempty `answer_ja` and `explanation_ja`; `distractors_ja` contains exactly the three incorrect-option IDs with nonempty explanations. The tested kanji reading appears in the answer. |
| Supplements | `supplement_en` / `supplement_yue` may be omitted or empty strings. Present nonempty values must be normalized plain-text strings. |
| Reading | `passage_ja`, required and nonempty for reading_comprehension. |
| Ordering | `prefix_ja` / `suffix_ja` are strings and may be empty; `correct_order` is a permutation of A–D; `starred_position` is an integer 1–4 and its fragment matches the answer ID. `reconstructed_sentence_ja` equals prefix + ordered fragments + suffix, without inserted text. Correct order/reconstruction appear only in the answer. |
| Ruby | `ruby` is a list, possibly empty. Each annotation has field, start, end, text, and reading. |

Ruby uses half-open Unicode code-point ranges [start, end) in the final NFC text. In この判断は恣意的です。, start 5 / end 8 selects 恣意的. Require 0 ≤ start < end ≤ field length, exact selected text, nonoverlapping ranges per field, and hiragana readings containing at least one hiragana character; the long-vowel mark is allowed.

Allowed ruby fields are exactly answer_ja, explanation_ja, distractors_ja.A–D for existing incorrect options, and reconstructed_sentence_ja on ordering questions. Annotate useful difficult words once per answer panel, leave common words unannotated, keep okurigana outside ruby, and never add Japanese readings to Cantonese.

Application-owned metadata includes status, phase, attempt_count, failure_reasons, correction_feedback, review_result, first_published_at, generation_model, review_model, and prompt_version. Provider-supplied status or metadata cannot establish readiness or publication. C3 completes parsing/field checks; C4 binds saved content to its review.

## 6. Generation and readiness

Supply the assignment, requirements, output contract, previous seven relevant published lessons, all earlier published subject/target pairs, and original material for review items explicitly. Requests assume no provider memory. Failed/unpublished drafts remain diagnostic material and do not establish teaching history.

Use the Responses contract with the configured endpoint/model: input messages, text.format type json_object, reasoning.effort, and store false. Drafts/corrections use generation effort; reviews use review effort. Each call contains complete context; no previous_response_id, remote conversation, streaming, tools, background execution, or replayed reasoning. Store false disables response-state storage, not a broader retention guarantee.

Accept final content only from a completed response. Traverse typed output; skip reasoning/commentary, concatenate output_text from completed assistant messages whose phase is absent or final_answer, and parse one unfenced JSON object. Do not assume the first output item is the answer. C3 requires strict JSON parsing.

Mechanical checks establish completeness, assigned types/subjects/reviews, new-target and wording uniqueness, published review references, correct ordering reconstruction, optional-field types, and ruby structure. Duplicate wording compares visible exercise content—including passage, ordering context, and options—after whitespace normalization. It excludes explanations and reconstructed answers.

Malformed published structures produce date/field diagnostics and retain usable material without crashing later generation. Invalid authoritative storage produces a storage error; do not silently invent missing history. Partial usable history cannot establish that unavailable material contains no duplicates.

One LLM review sees the proposed answers and assesses all four options; it is not blind or independent solving. Retain six review entries matching q1–q6, four boolean fits verdicts with nonempty reasons, and exactly these seven criterion keys:

| Criterion | Required assessment |
| --- | --- |
| `unique_answer` | Exactly one defensible option matches the key; sufficient context. |
| `explanation_accuracy` | Correct explanation and valid contextual reasons each distractor fails. |
| `japanese_quality` | Grammatical, idiomatic Japanese appropriate to register. |
| `learning_target` | The stated N1 target is tested; review context differs meaningfully. |
| `format_correctness` | Correct contextual readings, natural ordering/starred fragment, passage-supported reading answer. |
| `ruby_quality` | Correct, useful, selective readings; tested kanji reading included. |
| `supplement_accuracy` | Accurate English/Cantonese, preserving relevant meaning/grammar/pronunciation distinctions. |

Every criterion has pass/fail/inapplicable status and a nonempty plain-text reason. Every failure also has nonempty offending_text and change. Inapplicable is allowed only for supplement_accuracy when both supplements are absent/empty. Missing detail or any uncertainty affecting the answer fails the review. A bare approval/score is insufficient.

Readiness requires all mechanical checks and all applicable review criteria to pass for the same saved content. A content failure permits one corrected draft; a second failure is terminal. Attempt count is 0 before the first actual draft request, 1 for the initial draft, and 2 once the correction is authorized. It counts draft versions, not network retries. Ordinary successful generation uses two calls; a correction can use four, while service recovery can require additional requests.

HTTP/provider execution failures, interrupted reads, timeouts, and service setup failures preserve phase, draft, feedback, and correction budget. They are resumable after repair or a later invocation; authentication/quota failures are not assumed to resolve by waiting. Incomplete/refused/malformed model output remains a content-response failure within the correction limit. C2 retains its diagnostic details; C4 specifies durable checkpoints.

A ready lesson is reused without another model call. Generation never sends mail, writes vault files, or records publication. Terminal failures—including historical closed missed days—remain closed; this revision does not reopen them.

The saved phases are generating, checking, reviewing, ready, and failed. Status failed with a generating/checking/reviewing phase is resumable; phase failed is terminal. C4 requires saving enough result/feedback at each checkpoint to resume that phase without losing a completed decision or spending another correction. A crash before a response is committed may require another service request; no exactly-once model-call guarantee is made.

## 7. Publication

Before first publication, C4–C5 require checking saved content/review integrity and relevant intervening teaching history. A blocker records specific reasons and exits 1 without sending, writing, regenerating, or replacing the lesson. Readiness is a saved content-quality result, not permission to bypass publication checks.

Attempt activated email and vault channels independently, including independent setup validation. Disabled/completed channels need no service settings or secrets. A successful channel is retained when the other fails. An index failure can be retried without repeating email or rewriting a successful lesson.

Retain generation state separately from email pending/sent/failed/unknown, Markdown pending/written/failed, and index pending/written/failed, with destinations, attempt times, completion times, and last errors. An internal sending marker supports crash recovery. Only a command that has acquired exclusive access under the rule below may recover abandoned sending markers; readers never perform recovery.

### One active run

Allow one active `generate`, `publish`, `daily`, or `resolve-email` command per SQLite database. This prevents an overlapping scheduled/manual invocation from sending the same email, spending the same correction budget, or treating an active send as an abandoned one. SQLite transactions protect individual updates; API calls and file publication span multiple updates and need this run-level guard.

Use one operating-system file lock beside the resolved database path (`<database-path>.lock`), acquired before opening writable storage or recovering sends and held until the command exits. If another run holds it, immediately report that a run is already active and exit 2, before any service request or state change. The operating system releases the lock when the process exits or crashes; the lock file may remain and requires no manual cleanup. File existence alone must never indicate that a run is active. The guard applies across selected dates for the same database; different databases are independent.

Preview, validation, status, and schedule export need no run lock. They can run alongside generation/publication because they do not modify lesson state or recover sends. This clarifies the existing approved overlap-prevention requirement; it adds no command, setting, or locking service.

### Email

Refresh Gmail OAuth access using the configured client and offline refresh token with gmail.send permission. Send RFC-compliant multipart email with HTML and plain-text alternatives. C6 specifies required message headers and frozen message bytes/destinations.

HTML has six independent checkbox/label reveal controls: initially hidden, click to reveal, click again to hide only that answer. Render legible Japanese/Cantonese and answer ruby. Plain text places all questions before the answer key. Keep answers and reading hints out of the subject and preview text. Actual received-email compatibility is an acceptance requirement; browser/local MIME rendering is insufficient.

Persist outgoing bytes and send intent before contacting Gmail. A usable Gmail acceptance response/message ID establishes API acceptance, not guaranteed receipt or study. Definite rejection and failures before Gmail contact are resumable. A send timeout, lost/truncated/unreadable response, ambiguous server error, or crash after send intent leaves outcome unknown. Stop automatic resend until mailbox resolution. C2 retains useful diagnostics without changing this classification.

### Obsidian

Use the existing iCloud vault chosen by the operator. Write Lessons/YYYY/MM/YYYY-MM-DD.md, maintain Index.md with dated links/themes, and link Notes/YYYY-MM-DD.md. Never create or alter personal notes.

Render native collapsed answer callouts in Reading view. Inline ruby is the default; parenthesized hiragana inside answers is the documented fallback. Both channels use the same lesson/answer content. Always identify the correct option in the revealed answer; C3 closes the current answer-label gap.

Write complete files with atomic replacement and persisted checksums. Preserve untracked existing files and unexpected edits, including Index.md; report the path. Persist pending checksums to recover a completed write interrupted before status recording. C7 defines root/path consistency and confines the index to lessons written in that vault.

Local lesson-write completion establishes Markdown publication; index success and eventual iCloud transfer are separate outcomes. Downloaded notes and callouts work offline.

### Dates and accounting

Record each channel's actual attempt and observed completion time in Asia/Hong_Kong. First confirmed channel success records publication and applies teaching/review exposure in the same SQLite transaction, once per lesson. A failed attempt or index-only retry creates no exposure. Under C8, first_published_at remains the timestamp of the event that first established confirmed publication in SQLite. If later mailbox resolution confirms an earlier email acceptance, retain that actual time separately as email completion; do not rewrite already-applied exposure anchors or advance an already-consumed review stage. Unknown delivery cannot establish exposure until confirmed.

C1 requires capturing today's date once per invocation, removing clock windows/cutoff closures, and leaving retries to the scheduler. If generation finishes after midnight, save the selected lesson but do not start delivering yesterday's lesson. An already-started channel may finish after midnight; preserve its actual result, but start no additional old-date channel. Index bookkeeping for a note written in that invocation may finish. Return 1 for unfinished activated delivery; do not invent a missed/partial closure or switch to a second date mid-run.

Unknown outcomes for any recorded date may still be resolved as metadata. No automatic backlog delivery is authorized.

## 8. Diagnostics and operating guidance

C2 replaces the blanket bans on provider error text/body content in old sections 9.1 F2 and 10.2. Those bans were assistant-authored design decisions, not user-supplied requirements. Credential redaction is the requirement; suppression of useful error explanations is not.

Retain service/operation context, HTTP status, provider message/type/code/parameter/reason fields, request ID, and Retry-After when supplied. Cover LLM HTTP/execution errors, refusal/incomplete reasons, OAuth refresh errors, and Gmail errors. Recognized transport failures identify timeout, connection/TLS/DNS failure, or interrupted read when known, without inventing a cause.

For unrecognized/malformed/non-JSON error responses, retain a readable bounded excerpt. Missing/unreadable bodies retain status/headers and explain the extraction failure. Extraction must not replace the original failure or change send certainty. Preserve useful provider wording, with credential redaction, terminal-control sanitization, and explicitly marked truncation. Use documented finite body/diagnostic limits; handle a credential split at a truncation boundary. Never record authorization values, access/refresh tokens, configured secret values, outgoing request bodies, or reasoning traces.

Persist diagnostics in failure_reasons / channel error fields. Print an actionable recorded reason on command failure; status exposes saved phase, attempts, channel states, and reasons without service access. Notifications may remain concise and point to status; unchanged repeated daily failures do not repeatedly notify. No new log file or diagnostic command is needed.

Manual runs write normal command output to the terminal. The optional launchd template uses /tmp/n1-daily-launchd.log and /tmp/n1-daily-launchd-error.log; these temporary files are not the authoritative failure record. Previously discarded error details cannot be recovered retroactively.

README must explain setup/permissions, explicit file arguments, generation/status/preview commands, Responses migration/effort settings, exit codes, safe retries, unknown-email resolution, edited-file conflicts, and the scheduler template's activation consequences. Recommend pacing according to provider diagnostics; do not imply every 429 is temporary or that an accepted message necessarily reached the recipient. No implicit scheduler installation or automatic configuration repair.

## 9. Acceptance

Write meaningful unittest regressions before behavior changes. Use temporary databases/vaults, fake services, and fake clocks; run python3 -m unittest discover -s tests -v. Passing fixtures establish application behavior, not linguistic correctness or client compatibility.

Required offline coverage: strict JSON and field validation; incomplete/ambiguous reviews; durable draft/review pairing and crash checkpoints; one correction across restarts; service diagnostic preservation/redaction; independent setup/channel retries; no network use for ready-content generation reuse/previews; publication integrity and intervening-history checks; unknown-email resolution; exactly-once exposure; frozen destinations/bytes; file/index edit preservation; date rollover and no backlog; read-only commands and overlapping-run prevention; configuration defaults/types/permissions and absence of environment fallback.

Before daily use, assess the sample's N1 difficulty, natural Japanese/English/traditional-script Cantonese, readings, options, and realistic duration. Exercise real provider generation/review with deliberate defects. After separately authorized activation, test received Gmail in Mac/iPhone Apple Mail, Obsidian Reading view on both devices, enlarged text, independent reversible controls, ruby/fallback, offline reading, personal-note links, index/archive synchronization, launchd/sleep-wake execution, and Mac notifications. Keep existing chat practice delivery until acceptance and cutover are agreed. If a client cannot support the specified interaction, report the failure and review a replacement; do not silently change the product.

## 10. Approved corrections and scope

The user explicitly approved the replacement specification, including all eight corrections below, on 2026-10-12. These corrections were authored by the assistant to repair the identified gaps. Everything else consolidates the preceding approved baseline. Approval adds no command, configuration key, delivery activation, automatic content replacement, or scheduling installation.

| Change | Problem and approved correction |
| --- | --- |
| C1 — Scheduling | Carry forward old proposed section 12: external scheduling owns execution/retries; remove application clock windows and cutoff closure. Accept/ignore legacy HH:MM fields without ordering checks; remove them from the example config. Specify midnight behavior that still enforces today's publication/no backlog. |
| C2 — Diagnostics | Replace old sections 9.1 F2 / 10.2 suppression rules. Preserve useful LLM/OAuth/Gmail and model-response details with credential redaction; show actionable failures directly and in read-only status, including saved phase. Keep existing retry/correction/send-certainty rules. |
| C3 — Content parsing | Reject duplicate JSON keys and non-finite numbers in provider envelopes/final objects as well as files. Validate every renderer-consumed field; reject incompatible format-specific fields and invalid types instead of accepting content that later fails rendering. Compare targets with NFC/whitespace normalization and prevent duplicate targets/review references inside a lesson. Clearly display the correct option in every answer. |
| C4 — Durable readiness | Persist each completed draft/check result/review and its matching phase atomically. Reuse a saved review only for its bound content, assignment, and review context; persist a content fingerprint/binding, with application-owned metadata excluded. Save failure feedback with the correction transition. An interrupted corrected request retains attempt count 2 and its feedback; a saved completed review does not require another call on restart. Recheck content/review integrity before an unfinished delivery; a ready marker alone is insufficient. |
| C5 — Saved assignments/history | Fix assignment eligibility for past/future generation: review originals must be from an earlier lesson date, actually published, and compatible/due for the selected date. Save the generation history/context and selected review stages when assigning; do not change the assignment/context on restart. Before first publication, check current earlier-date published targets/wording and selected review stages. Block collisions/stale review selections without replacing ready content or resetting its budget. Frozen themes/formats reflect assignment creation, not a forecast of future rotation. |
| C6 — Email stability | Persist RFC-compliant bytes, including Date and Message-ID, and their sender/recipient before contact. Reuse them exactly on retry. If an unfinished invocation's configured sender/recipient conflicts with saved bytes, report the mismatch before service contact; never silently retarget or rebuild the message. |
| C7 — Vault stability | Require the configured vault root to exist as a directory; never create a new root because a configured path is missing. Bind the resolved root/destination before the first file write. A setup failure before binding permits configuration repair. Reject destination changes for bound unfinished lesson/index work and paths escaping the root; leave email independent. Index only successfully written lessons belonging to that same root. Preserve all untracked/edited files; no automatic vault migration. |
| C8 — Mailbox/accounting | Require an existing database/send record; do not create an empty database for resolution. Reject accepted-at without accepted-id, blank IDs, unconfirmed decisions, and non-aware/impossible acceptance times (future or before the recorded send attempt). Reject argument errors before mutating/recovering records. Both decisions remain metadata-only. Record actual confirmed acceptance even when another channel has published; keep exposure idempotent. Late confirmation must not increment an already-consumed review stage or shift established review schedules/publication anchors. Retain per-channel completion time separately from attempt time. |

C4 migration must preserve existing records and require no LLM request. For an old ready record without a binding, validate its current mechanical content and complete saved review before establishing a migration baseline. Reject missing/invalid reviews for publication. A newly computed baseline cannot prove that content was unchanged before migration; document that limit. Read-only commands never perform this migration.

For legacy assignments without a saved context/stage snapshot, C5 permits reconstructing a migration baseline from the saved assignment and available earlier-date published records. Identify it as reconstructed; it does not prove the original request context. Preserve content, phase, and correction budget, and still run current-history publication checks. Never recompute the assigned theme, formats, or review references during migration.

C5 deliberately preserves the existing prohibition on forced regeneration/replacing ready content. A stale pre-generated lesson can therefore be inspected but blocked from first publication. Adding a recovery/replacement workflow would be a separate product decision; this review does not silently authorize one.

Implement approved changes in focused steps with their relevant offline checks, update README implementation choices, and record measured evidence here. Do not treat approval of one correction as approval of unrelated proposals. An assistant-authored restriction must be attributed to the assistant, even if its containing revision was approved.

## 11. Evidence and references

### 11.1 Historical approval record

The historical snapshot retains the previous approval statements and detailed evidence. The user subsequently approved this replacement specification and all C1–C8 corrections on 2026-10-12, followed by a fresh implementation with the previous code archived.

### 11.2 Review evidence and references

Specification review covered the entire preceding specification, the historical reconciliation, all application modules, sample/config templates, README, and the relevant test cases. Code inspection found concrete gaps behind C2–C8: HTTPError bodies are closed unread; OAuth/Gmail discard detail; provider json.loads accepts duplicate/non-finite input; delivery trusts status ready without checking the saved review; assignments query reviews without excluding later lesson dates; completed reviews can be requested again after a crash because their decision/phase is not committed with the report; invalid-response feedback can be lost at a checking checkpoint; MIME lacks Date; vault writes can create a missing root and build an index from another destination's records. New regression results must be recorded after approval, not inferred from inspection.

Official references establish provider formats and client documentation, not approval of product choices:

- [OpenAI Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses), [reasoning](https://developers.openai.com/api/docs/guides/reasoning), [JSON output](https://developers.openai.com/api/docs/guides/structured-outputs), and [error codes](https://developers.openai.com/api/docs/guides/error-codes).
- [Gmail sending](https://developers.google.com/workspace/gmail/api/guides/sending), [send endpoint](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send), [error handling](https://developers.google.com/workspace/gmail/api/guides/handle-errors), and [Google offline OAuth](https://developers.google.com/identity/protocols/oauth2/web-server). Gmail documents JSON error details in addition to HTTP status, and distinguishes API responses from guaranteed delivery.
- [Obsidian callouts](https://obsidian.md/help/callouts) and [Apple launchd jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

### 11.3 Implementation evidence

The previous specification records 115 tests after the reconciliation corrections, 133 after Responses/effort support, and 142 after flexible manual generation. These are historical measurements, preserved in the snapshot; the specification-review stage ran no tests. The replacement implementation has its own evidence below.

The user previously confirmed local sample rendering in Safari, Obsidian, and a local .eml opened in macOS Apple Mail. Received messages, iPhone interaction, offline controls, enlarged text, real provider quality, synchronization, and live scheduling remain acceptance work. The specification-review stage changed no application code or tests. Neither that stage nor the replacement implementation work touched private configuration, production SQLite, real-vault files, delivery, or scheduler state.


### 11.4 Replacement implementation — 2026-10-12

Following explicit approval, the previous source/tests and their supporting README/templates/sample were archived under `docs/history/implementation-2026-10-12/`. Active application modules and tests were rebuilt against this specification. The authored sample remains shared input, not generated acceptance evidence.

Measured offline evidence: `python3 -m unittest discover -s tests -v` passes **57 tests** on Python 3.14.3. The suite covers strict JSON/renderer fields, complete option/criterion reviews, Responses payloads and typed output, HTTP error details/redaction and send certainty, draft/review commit interruption, correction/service retries, frozen assignments and review stages, intervening-history blockers, review intervals and idempotent exposure, independent delivery and index retry, frozen MIME/destinations, unknown-email resolution, checksum recovery/edit preservation, vault boundaries, midnight behavior, read-only commands, additive legacy migration, overlap prevention, and notification deduplication. Tests use fake services and temporary storage.

Command smoke checks passed: sample `preview --output /tmp/n1-daily-rewrite-preview` produced all five artifacts; default `validate` and standalone validation of the exported lesson JSON passed. `git diff --check` passed. These checks establish local application behavior only; no provider request, email, real-vault write, scheduler activation, or client acceptance was performed.

README documents the implementation choices, explicit setup, diagnostic limits, migration limits, retry/resolution commands, and scheduler activation consequences. The example config omits ignored legacy timing fields. Scope review found no added commands, flags, configuration keys, automatic content replacement, or activation beyond the approved revision. Live/client acceptance in §9 remains outstanding.

### 11.5 Connection investigation — 2026-10-12

After the operator reported a timeout and then `RemoteDisconnected` with `timeout_seconds: 300`, explicitly approved diagnostic requests established:

- Credential-free curl and Python requests reached OpenAI and received the expected HTTP 401.
- The configured key could retrieve `gpt-6.1-sol` model metadata (HTTP 200).
- Responses requests deliberately missing the required model field received HTTP 400 promptly, both with an empty body and with the actual lesson-sized body (10,207 bytes without the model field). These validation requests generated no tokens.
- One minimal generation using the configured model and medium reasoning completed in 2.88 seconds: 16 input tokens and 12 output tokens. Request ID: `req_e0ff11f3db2142bf9515f9d76bfbcf05`.
- An instrumented retry of the actual generation command disconnected after 60.36 seconds, before receiving HTTP response headers, despite the 300-second configured timeout. The saved record remained resumable at phase generating, attempt count 1. No lesson was delivered.

These observations establish a failure during the longer request, not a general authentication, model-access, or request-upload failure. A roughly 60-second server/intermediary limit is the leading hypothesis; the closing network hop and its policy have not been identified. The operator's earlier zero-usage observation does not establish where those requests stopped. No application implementation was changed during this investigation. The temporary diagnostic scripts contain no embedded credentials.

## 12. Pending proposal — streaming Responses transport

**Not approved.** This is an assistant-proposed transport correction following §11.5. Sections 1–11 remain the approved baseline until explicit approval. The prohibition on streaming in §6 was assistant-authored; the user did not independently request it.

Replace §6's prohibition on streaming with the following behavior:

- Draft and review requests explicitly set `stream: true` on the same configured Responses endpoint. Keep the existing model, reasoning effort, JSON-object output format, complete per-call context, and `store: false`. There is no streaming toggle, protocol fallback, background mode, or new command/configuration field.
- Read the SSE response incrementally. Accept lesson/review content only from the complete response envelope in `response.completed`, using the existing typed final-output and strict JSON checks. Partial text never becomes a saved draft or review. Ignore reasoning/commentary and intermediate output; do not print, persist, or replay reasoning traces.
- Treat a terminal `response.failed` or provider error event as a resumable service failure with redacted provider details. Treat a terminal `response.incomplete`, refusal, or malformed completed model output under the existing content-failure/correction rules. A disconnect, timeout, or stream ending without a terminal response is a resumable service failure, preserving the saved phase and budget. A malformed stream protocol is a resumable service failure; it cannot establish a completed content decision.
- Keep `timeout_seconds` as the existing network-operation timeout. Streaming makes response headers/events available before the entire lesson finishes; it is not a guarantee against every network or provider timeout. Retain bounded event/final-response parsing and existing credential redaction. Do not add automatic retries or alter publication behavior.
- For LLM transport failures, record the configured timeout, elapsed request time, whether HTTP response headers arrived, and a request ID when available, alongside the existing sanitized reason. This distinguishes client timeouts from earlier peer disconnects without exposing request bodies or authorization.

After approval, write offline regressions for chunked/multiline SSE parsing, completed/failed/incomplete/refused responses, malformed or unterminated streams, ignored reasoning, strict JSON, redaction, elapsed/header diagnostics, and restart/correction preservation. Then repeat generation for the existing selected date with the configured model and effort to assess whether streaming resolves the observed disconnect. Do not send email, write to the real vault, or activate scheduling. Record the live result honestly; a successful stream alone does not establish lesson quality or client acceptance.
