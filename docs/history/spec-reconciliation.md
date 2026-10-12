# Implementation and Specification Reconciliation

Historical scope: the one-off 2026-10-09 audit and its section 9 corrections, including removal of `dry-run`. This file preserves findings and evidence from that period. It is archived alongside the [previous specification](n1-daily-email-design-2026-10-12.md). Current requirements, later revisions, and their validation evidence live in [the main specification](../../n1-daily-email-design.md); this report is not an ongoing specification or implementation log.

Audit date: 2026-10-09. Baseline: commit `3d32620` plus the working directory. This report compares all application modules, the sample, configuration templates, README, contributor guidance, and tests with `n1-daily-email-design.md`. It records findings and recommendations; it does not approve requirements or authorize implementation.

At the initial audit, the specification proposed removing `dry-run` and retaining `preview`; the application, tests, and command documentation still exposed both commands pending review. No production code or tests were changed during that audit. No email was sent, real-vault lesson files written, or schedule enabled.

**Approved follow-up: preview only.** The user subsequently approved removal of `dry-run`. It was removed from the CLI and help, README examples, and contributor commands, with 80 tests passing at that stage. The duplicate-command item is resolved.

## Approved reconciliation follow-up

The user approved specification section 9. Its offline implementation is complete, with **115 tests passing**, including 35 new regression tests in [test_reconciliation.py](../../tests/test_reconciliation.py). Initial failing runs reproduced the audit defects before their fixes. The remaining tables and findings below preserve the original audit snapshot; they do not describe unresolved defects after this follow-up.

| Finding | Implemented behavior | Regression evidence |
| --- | --- | --- |
| F1 | Published material supplies teaching history; malformed entries produce date/field diagnostics, usable questions are retained, and failed drafts remain stored. | Cross-date generation with malformed failed drafts; preserved raw drafts; partial published history; no reservation of unpublished targets. |
| F2 | Truncated LLM reads are retryable; OAuth failures and malformed access tokens are definite; incomplete Gmail send responses are unknown. | Draft/review recovery with unchanged budget and no duplicate generation; safe diagnostics; OAuth and send response classification. |
| F3 | Requested channel setup is independent; a missing vault records a Markdown failure while email succeeds. Completed channels need no service settings. | Repair the vault and complete its files without resending email or loading deleted credentials; inverse email setup failures remain covered. |
| F4 | Setup failures are persisted and notified while preserving phase/draft. Initial setup failure leaves attempt count 0; unchanged daily failures do not repeat notifications. | Missing settings, unusable secrets/permissions, review-phase failures, and recovery after repair. |
| F5 | Ruby paths match exact existing answer fields; suffixes and non-ordering reconstructed fields are rejected. | Exact field-path failures with otherwise matching offsets; existing answer-only rendering and hiragana/bounds/overlap tests. |
| F6 | Failed reviews require reason, offending text, and change. Incomplete reports are retained and rejected with field-specific correction feedback. | Missing/empty/mistyped fields, saved feedback, and exhausted correction limit across retries. |
| F7 | Saved lessons are rechecked using their persisted assignment and prior published history. Standalone inputs support later themes/formats and report their limits. | Corrupted ready content, historical duplicates, standalone review-reference shapes, input conflicts/duplicate JSON keys, and missing/corrupt context. |
| F8 | Endpoints require HTTPS host/path with no userinfo, query, or fragment. Rejected values are absent from errors. | Query parameters and encoded names, empty userinfo, fragments, invalid ports, HTTP, and missing path. |

Related scope gaps are resolved under section 9: generation is restricted to today and the configured window; read-only commands do not create or modify SQLite or recover active sends; publication uses observed completion time; cutoff closes missed/partial delivery once; review ties use question ID then lesson ID; unknown-email resolution retains saved bytes. Tests cover window endpoints/overlap, completed-channel reuse, publication across midnight, cutoff between channels/index work, notification deduplication, review cap/postponement, and both mailbox decisions. Existing review tests cover all four delays, delayed exposure, and completion of the sequence.

The forward specification baseline is explicitly approved. README documents the supported CLI semantics and exit codes, endpoint restrictions, setup, and implementation choices. AGENTS now describes the observed `feat:`/`fix:` commit examples. Scheduling still exports the same inactive two-channel artifact; no selection or installation commands were added. Published-content correction tooling remains deferred.

Static reading found the sample's English and traditional-script Cantonese supplements readable. The Japanese ordering explanation now states the comparison in context. The sample's N1 difficulty and estimated duration remain unverified by the user; synthetic verdicts and static reading do not establish linguistic acceptance. Actual provider behavior, received messages, iPhone clients, offline controls, enlarged-text legibility, iCloud synchronization, launchd execution, and Mac notification display remain pending separately authorized acceptance work. No live services, real-vault lesson writes, or scheduling were activated.

## Evidence and limits

- `python3 -m unittest discover -s tests -v`: **79 tests passed**. Tests use temporary databases/vaults and fake services.
- `python3 -m n1_daily preview --output <temporary-directory>` produced `lesson.html`, `lesson.eml`, `lesson.txt`, `lesson.md`, and `lesson.json`. The audit directory was removed afterward.
- Temporary, offline reproductions confirmed the defects below. HTTP failures were injected; no provider credentials were used.
- The user previously confirmed sample rendering in Safari, Obsidian, and a local `.eml` opened in macOS Apple Mail. Received messages, iPhone behavior, offline controls, enlarged text, and iCloud synchronization remain unverified.
- Git contains one initial MVP commit. It cannot establish the sequence of specification reviews, approvals, test failures, and implementation. Passing tests do not establish that development followed TDD.

## Requirements coverage

“Covered” means code and offline evidence support the behavior. It does not imply live-service or client acceptance. Findings F1–F8 below qualify the relevant rows.

| Specification requirement | Implementation and evidence | Assessment |
| --- | --- | --- |
| Development process: approved scope before implementation | AGENTS now requires traceability and review. The specification's overall status remains “proposed”; only the configuration revision is explicitly marked approved. | Approval baseline needs clarification; current code cannot prove earlier process compliance. |
| §1: six original N1 questions, four options, required subjects, 10–15 minutes | Sample and `content.check_lesson`; renderer labels original practice. | Structure covered; actual N1 difficulty, study duration, and linguistic quality need assessment. |
| §1, §6: independent hidden answers, answer-only ruby, offline reading | `render.py` produces checkbox panels, collapsed callouts, ruby, and plain-text answer key. Structural renderer tests pass. | Local rendering confirmed by user; actual interactive/offline client acceptance pending. |
| §2: local Mac app, SQLite authority, separate generation and delivery | `store.py`, `workflow.py`, renderers, and CLI adapters separate saved content from services. | Covered; saved ready lessons work without LLM credentials in tests. |
| §2: excluded product scope | No learner accounts, scores, adaptation, catalog, approval service, or automatic published-content corrections. | Mostly aligned; unrestricted manual generation dates create a scope question. |
| §2 configuration: fields, defaults, overrides, validation | `config.py`, example JSON files, and 24 configuration/CLI tests. | Broadly covered; credential-bearing endpoint query validation has a gap (F8). |
| §2 configuration: credentials isolated, deferred loading, no environment fallback | Lazy `SecretSource`, ownership/mode checks, safe diagnostics, explicit arguments; isolation tests pass. | Covered for tested paths; no actual credential setup exercised. |
| §3.1: Hong Kong dates, themes and format rotation advance on publication | Assignment derives rotation from first-publication count; transaction avoids double advancement. | Covered by rotation and duplicate-run tests. |
| §3.1: oldest compatible reviews, cap two, delays 1/3/7/14, completion | `Store.assignment`, `review_items`, and `channel_success`; interval/cap tests pass. | Basic behavior covered; exhaustive subject-compatibility and tie-order cases are not tested. |
| §3.2: explicit history, targets, original reviews, requirements in requests | `Store.context` and `JsonModel` provide context, sample shape, requirements, and seven review criteria. | Covered for normal history; malformed retained drafts can block later dates (F1). |
| §3.2: initial draft plus one correction, persistent limit, normal two calls/max four | Persisted generation phases, attempt count, feedback, saved review; correction/restart tests. | Covered for tested failures; truncated HTTP responses escape handling (F2). Transport retries can add requests, as allowed by outage recovery. |
| §3.3: checks and matching review saved before delivery | `generate` marks ready after mechanical checks and `check_review`; `deliver` requires ready. | Covered for ordinary cases; check completeness has gaps (F5/F6). |
| §3.3: independent channel attempts and retries | Per-channel states; failed email can coexist with successful Markdown; retries skip completed work. | Covered in delivery tests; missing vault configuration aborts both channels before delivery (F3). |
| §3.3: first publication and review exposure once on either channel success | `Store.channel_success` updates delivery and exposure in one transaction. | Covered; recorded timestamp is the start of delivery, not observed success time. |
| §4.1: lesson identity, metadata, attempts, failures, review | Lesson JSON plus SQLite columns and generation metadata. | Covered on normal paths; setup errors may leave a draft with no failure reasons (F4). |
| §4.1: question fields, passage and ordering structure | Mechanical checks, sample, model instructions, renderers. | Covered for required fields; optional false-valued supplements and some extra fields are not strictly validated. |
| §4.2: normalized code-point ruby, allowed fields, bounds, overlap, hiragana | Mechanical checks and renderer tests. | Mostly covered; invalid distractor field suffixes pass and disappear in rendering (F5). |
| §4.3: complete reasoned options/criteria and concrete failure details | `check_review` requires six entries, four verdicts, seven criteria, and exactly one fitting option. | Readiness blocking covered; required failure text/change fields are not enforced (F6). |
| §4.4: saved MIME, Gmail ID, delivery statuses, timestamps/errors, checksum | SQLite delivery columns, generated-file records, explicit unknown resolution. | Covered on tested paths; internal `sending` and `index_status` are supporting implementation states. |
| §5.1: completeness, assignment, references, format data, duplicates | `check_lesson` plus negative content tests. | Broadly covered; CLI validation of saved ready records skips all these checks (F7). |
| §5.2: linguistic review, uncertainty fails, inapplicable only for absent optional features | Explicit model criteria and structured response validation; synthetic verdict tests. | Gate logic covered; real reviewer performance and language quality unverified. |
| §6.1: Gmail OAuth refresh, multipart MIME, acceptance and uncertainty handling | `services.py`, persisted send intent/bytes, recovery and HTTP classification tests. | Implemented and mocked; real OAuth and received-email behavior unverified. A truncated OAuth response is conservatively marked unknown rather than definite. |
| §6.2: dated lesson, Index, Notes link, collapsed callouts, ruby fallback | Markdown renderer and vault delivery; temporary-file tests. | Covered locally; no real-vault delivery or iCloud test performed. |
| §6.2: complete atomic files, preserve edits, independent index retry | `files.py` fsync/replace/checksums; edit/conflict/crash/index tests. | Covered for tested failure cases; actual iCloud interruption behavior pending. |
| §6.3: 07:45/08:00/noon, one active run, no backlog, retries/notifications | `daily`, per-database file lock, minute-polling inactive launchd export, notification helper. | Daily timing/locking covered offline; manual generation limits and actual launchd/notification behavior need review/verification. |
| §7: demonstrations before daily use | Negative checks, retry scenarios, and configuration isolation tested offline. | Partial acceptance only: real model review, received mail, iPhone, offline, and iCloud demonstrations are pending. |
| Proposed preview-only revision | Existing aliases share the same rendering handler and options. | Not implemented, deliberately awaiting approval. |

## Confirmed implementation gaps

### F1 — High: malformed saved drafts can block future generation

Requirement: §3.2 records failed content and allows subsequent daily work; §5.1 identifies malformed structure.

[Store.context](../../n1_daily/store.py) (lines 140–144) iterates `old.get("questions", [])` without verifying it is a list. Failed drafts are included in `history`. A retained draft containing `{"questions": null}` causes a later date to raise `TypeError: 'NoneType' object is not iterable` before contacting the model. Other malformed historical entries can also disrupt duplicate checks.

Confirmed offline: save that failed draft for 2026-10-09, then generate 2026-10-10. The exception escapes. Existing malformed-history tests exercise `check_lesson` directly, bypassing `Store.context`.

Recommended correction: safely extract usable historical questions while retaining the failed draft and its diagnostics. Add a cross-date workflow regression after approval.

### F2 — High: truncated HTTP responses escape outage handling

Requirement: §3.2 persists model failure reasons and permits a later retry; §6.3 reports unsuccessful work.

[JsonModel._call](../../n1_daily/llm.py) catches HTTP errors, `OSError`, and `ValueError`. `http.client.IncompleteRead` is outside those categories and also escapes `generate` and the CLI handler.

Confirmed offline: inject `IncompleteRead` during generation. The exception escapes, leaving status `draft` and failure reasons `[]`. Existing timeout/outage tests do not cover this case. During OAuth refresh the same exception reaches the generic send handler and becomes `unknown`, although Gmail has not been contacted; this unnecessarily blocks safe automatic retry.

Recommended correction: distinguish incomplete LLM responses as retryable outages, pre-send OAuth failures as definite failures, and incomplete Gmail send responses as uncertain outcomes. Preserve the correction budget and report failures.

### F3 — High: a missing vault setting blocks independent email delivery

Requirement: §3.3 attempts channels independently and preserves successful delivery.

[channels](../../n1_daily/cli.py) (lines 102–112) raises for missing `vault_path` before `deliver` runs. With both activation flags, a vault configuration failure prevents any email attempt. The inverse email-configuration failure is handled independently and already tested.

Confirmed offline with temporary configuration and a mocked sender: `daily` returns 2 for missing `vault_path`; the Gmail client is never created.

Recommended correction: record the vault failure per channel and still attempt configured email. Verify both directions with fakes after approval.

### F4 — Medium: service setup errors bypass failure recording and notification

Requirement: §4.1 retains specific failure reasons; §6.3 reports unsuccessful generation.

`generate` creates a draft and increments its attempt count before lazy model configuration is validated. A `ConfigError` then reaches the CLI's generic handler without updating the record or issuing a Mac notification. This differs from the persisted, notified outage path.

Confirmed offline with valid public LLM settings and no secret-file argument: the CLI returns 2, the record remains `draft` with attempt count 1 and failure reasons `[]`, and the notification helper is never called. Existing bad-secret tests assert that network access is blocked, but do not inspect failure state or notification. Recommended correction: define and test how setup failures are recorded and retried without consuming a content correction.

### F5 — Medium: invalid ruby field paths pass mechanical validation

Requirement: §4.2 and §5.1 restrict annotations to existing allowed answer fields.

[answer_field](../../n1_daily/content.py) (lines 175–183) uses only the second dot-separated component of a distractor path. An annotation for `distractors_ja.B.extra` passes `check_lesson` when its offsets match distractor B. The renderer requires an exact field match, so the annotation is omitted from the output.

Confirmed offline: `check_lesson` returned `[]` for that invalid field path. Recommended correction: enforce exact allowed field names and test rejection plus rendered annotation preservation.

### F6 — Medium: failed reviews need not supply the required correction details

Requirement: §4.3 retains offending text, failure reason, and required change for every failure.

[check_review](../../n1_daily/content.py) (lines 209–218) checks the reason but permits missing `offending_text` and `change`. It rejects the draft, yet accepts incomplete failure detail into the stored review and correction feedback.

Confirmed offline: a failed criterion with only a reason yields blank `offending text:` and `change:`. Recommended correction: reject incomplete failure reports explicitly and supply actionable feedback within the existing correction limit.

### F7 — Medium: saved-lesson validation reports checks that it never runs

Requirement: §5.1 defines mechanical checks; the README says `validate` runs them.

[preview_lesson](../../n1_daily/cli.py) returns a saved record immediately when status is ready. `validate --date` then prints “Mechanical content checks passed” without examining its content.

Confirmed offline: remove option D from a temporary saved ready lesson; `validate --date` returns 0 and prints success. File-based validation also assumes the first day's work theme/formats and empty history, so it rejects later themes and cannot establish history-dependent checks.

Recommended correction: specify validation input/context semantics before changing this CLI behavior. A saved validation should use its persisted assignment and prior history, excluding the lesson itself from duplicate detection.

### F8 — Medium: endpoint credential validation is incomplete

Requirement: the approved configuration table prohibits embedded credentials in `llm_endpoint`.

[Config.from_file](../../n1_daily/config.py) (lines 145–158) rejects URL userinfo and four credential query names, but accepts `?client_secret=SYNTHETIC_SECRET`.

Confirmed offline with a synthetic value. No actual credential leak was observed. Recommended correction: agree on permitted endpoint URL components and enforce that policy; do not assume a short query-name list establishes credential-free URLs.

## Scope and specification gaps requiring review

| Area | Current behavior | Reconciliation needed |
| --- | --- | --- |
| Approval status | Overall specification says “proposed”; configuration says approved. | Establish which baseline sections are approved. Do not infer approval from the MVP commit or this audit. |
| Duplicate command | `dry-run` duplicates `preview`. | The new proposed revision explicitly removes it, without a compatibility alias. |
| CLI contract | `validate`, `generate`, `publish`, `daily`, `status`, `resolve-email`, and `schedule` implement specified capabilities; configuration mentions several names. Inputs, conflicts, side effects, and exit codes are not fully specified. | Distinguish useful implementation entry points from additional product behavior and document only approved semantics. These command names alone do not establish unauthorized product features. |
| Manual generation | `generate --date` accepts past/future dates and ignores the daily window. | This can pre-generate future lessons despite the reserve-pool exclusion. Decide the authorized manual recovery scope before implementing restrictions or exceptions. |
| Preview inputs | `--date` silently overrides `--lesson`; arbitrary JSON is checked against the first-day assignment. | Define supported custom lessons and conflicting-input behavior. Preserve current options during the removal-only revision; do not silently approve their semantics. |
| Historical targets | Unpublished failed drafts contribute targets and duplicate wording. | Clarify whether “earlier” targets include failed material that was never taught. The specification uses both stored-history and prior-teaching language. |
| Publication timestamp | Success records the datetime supplied before network/file operations. | Clarify whether `first_published_at` must reflect actual acceptance/write completion; the current value can precede availability by the request duration. |
| Late partial delivery | After cutoff, `daily` records a missed window even if one channel already published. | Clarify status wording for partial delivery versus a day with no publication; existing successful exposure is preserved. |
| Schedule export | One inactive plist always enables both channels when installed, polls every minute, and uses fixed temporary log paths. | Document these choices and whether selecting scheduled channels is required. No installation or live scheduling has occurred. |
| Post-publication corrections | Spec requires explicit operator action; no correction command exists. | This is a deferred operational procedure, not authorization to add one. Define it before any such implementation. |
| Contributor history | AGENTS says no Git convention is established; one `feat: initial MVP commit` now exists. | Acknowledge the observed example without inventing an established convention. |

## Remaining acceptance work

1. Assess the complete sample for N1 difficulty, natural Japanese, English and traditional-script Cantonese, correct readings, defensible options, and realistic completion time.
2. Exercise real generation and review with deliberate ambiguous answers and incorrect explanations. Synthetic verdicts verify application handling, not a model's ability to detect these defects.
3. After explicit email activation, verify OAuth refresh and actual Gmail-received messages in macOS/iPhone Apple Mail: six initially hidden answers, reversible independent controls, answer-free preview, ruby, and enlarged text.
4. After explicit vault activation, verify Obsidian Reading view on both devices, the ruby fallback, Notes links, Index updates, and iCloud synchronization/archive access.
5. Download both outputs and test answer controls offline on both devices.
6. After explicit scheduling activation, verify launchd execution, overlapping invocations, sleep/wake recovery, configured windows, cutoff behavior, and visible Mac notifications.
7. Add meaningful regressions for F1–F8 only after the required specification review. Complete current offline coverage for review ordering/compatibility, unknown-email operator resolution, and service response edge cases.

## Review boundary

The user approved both removal of `dry-run` and specification section 9. Those offline implementation, test, README, and AGENTS updates are complete. The initial scope questions are resolved as recorded above; the original audit remains a historical record. Live activation and any further system changes still require their respective explicit requests and specification review.
