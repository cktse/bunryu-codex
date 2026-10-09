# Daily JLPT N1 — MVP Specification

Status: proposed. This document defines the first working version and supersedes the earlier, broader design.

**Configuration revision: approved.** The JSON configuration requirements below replace environment configuration. Implementation proceeds under this approved revision; live delivery and scheduling still require explicit activation.

## Development process

Follow specification-driven development. Every system change starts with an update to this document and a review pause. Implementation and its tests begin only after the user explicitly approves the proposed specification. After approval, use test-driven development and continue until the approved behavior runs locally. Record straightforward implementation choices and concise setup instructions in `README.md`.

Implement a complete sample lesson and the email/Obsidian renderers first, then SQLite persistence, LLM generation and review, Gmail delivery, and daily scheduling. Keep generation separate from delivery. Activation of email, real-vault writes, and scheduling requires a separate explicit request. Daily lesson readiness continues to use the automated checks in section 5.

## 1. The product

Deliver one Japanese practice lesson each day at approximately 08:00 Asia/Hong_Kong. The same lesson is available in Apple Mail and as a dated Obsidian note synchronized through iCloud Drive. Target devices are macOS and iPhone.

A lesson takes 10–15 minutes and contains six original JLPT N1-style questions. Each question has four options and one correct answer. Every lesson covers kanji readings, vocabulary, and grammar. Explanations are primarily Japanese, with short English or Cantonese supplements when useful.

In email, a button-styled control immediately below each question reveals that question’s answer and explanation. In Obsidian, a collapsed callout provides the same behavior. All answer content is included in the lesson, so revealing an answer works offline after download. The reader sends no information to a backend service.

Difficult kanji in answers receive selective hiragana ruby. Questions receive no added pronunciation hints. Label the exercises as original practice, rather than actual past exam questions.

## 2. MVP scope and components

Build one application running on a designated Mac, with four responsibilities:

| Component | What it does | Output |
| --- | --- | --- |
| Daily runner | Chooses the date, theme, question mix, and review material; starts generation or reuses today’s saved lesson | A daily assignment |
| Content generator | Calls the LLM to write a lesson and review its correctness; runs the checks in section 5 | A ready lesson or a recorded failure |
| Lesson store | Keeps lesson JSON, the review result, and separate email and Markdown outcomes | The authoritative lesson history |
| Delivery component | Renders a ready lesson, sends it through Gmail, and writes it into the Obsidian vault | Email and a dated Markdown note |

Use SQLite for the lesson store and Apple’s native iCloud synchronization for the vault. Keep the database and credentials outside iCloud. A scheduled invocation on the Mac runs the daily process. The Mac must be available for publication at the requested time.

**The generation boundary is the saved, ready lesson.** LLM generation and content review are non-deterministic. Rendering and delivery use that saved content without any model call. An LLM outage therefore does not prevent sending a lesson already marked ready or recreating its Markdown file.

The MVP has one recipient and one writer. It excludes learner accounts, score collection, adaptive difficulty, a complete curriculum catalog, prerequisite management, a reserve lesson pool, and a separate approval service. There is no daily human approval step. “Ready” has the specific meaning defined in section 5.

### Source of truth

Store lesson content as JSON in SQLite and generate Markdown from it. JSON makes the question, options, correct answer, explanations, and readings explicit and gives both channels the same input. Markdown remains the portable reading archive. Personal notes belong in separate files.

Markdown authority would make direct editing convenient, but would require the application to reconstruct question structure and answers from edited notes. That additional parsing and synchronization is outside this MVP.

### Configuration and secrets

Load nonsecret settings from `config.json` and credentials from `secrets.json`, using explicit file arguments. Both files contain flat JSON objects. The following table defines every JSON field, private-file argument, and configuration override; examples are illustrative and contain no working credentials.

| File or interface | Field or argument | Meaning and valid values | Required when | Default if omitted | Example |
| --- | --- | --- | --- | --- | --- |
| CLI | `--config` | Path to the settings JSON file; keep it outside iCloud and the vault. Scheduled invocations use an absolute path. | `generate`, `publish`, `daily`, and `schedule`; optional for local previews, validation, status, and email-outcome resolution | No file is discovered or loaded; commands that permit omission use the defaults below | `--config /private/path/config.json` |
| CLI | `--secrets` | Path to the credentials JSON file, outside iCloud and the vault. It must be owned by the running user with permissions `0600` or `0400`. Scheduled invocations use an absolute path. | Before an LLM or Gmail request; a schedule using these services must include the path | No secret file is loaded; service requests requiring credentials cannot proceed | `--secrets /private/path/secrets.json` |
| CLI | `--db` | SQLite path overriding `db_path`; outside iCloud and the vault | Optional | Uses `db_path` or its default below | `--db /private/path/lessons.sqlite3` |
| CLI | `--output` | Preview directory for `preview`/`dry-run`, or destination file for an inactive `schedule` export; outside iCloud, the vault, and launchd job directories | `schedule`; optional for previews | Previews use `preview_path`; schedule export has no default destination | `--output /tmp/n1-daily-preview` or `--output /tmp/n1-daily.plist` |
| CLI | `--ruby-mode` | String `html` or `parentheses` overriding Obsidian answer annotation settings for this invocation | Optional | Uses `ruby_mode` or its default below | `--ruby-mode parentheses` |
| `config.json` | `db_path` | String path to the authoritative SQLite lesson history; outside iCloud and the vault | Optional | `~/Library/Application Support/N1Daily/lessons.sqlite3` | `"~/Library/Application Support/N1Daily/lessons.sqlite3"` |
| `config.json` | `preview_path` | String path for local HTML, MIME email, plain text, Markdown, and JSON previews; outside iCloud and the vault | Optional | `n1-daily-preview` under the operating system's temporary directory | `"/tmp/n1-daily-preview"` |
| `config.json` | `vault_path` | String path to the Obsidian vault created with the iCloud option; used only after explicit vault activation | Vault delivery | Unset; vault delivery cannot proceed | `"~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Japanese"` |
| `config.json` | `sender` | String containing one email address for the configured sending Google account; no display name or address list | Gmail delivery | Unset; local MIME previews use `preview@example.test` | `"sender@example.com"` |
| `config.json` | `recipient` | String containing the single lesson recipient's email address; no display name or address list | Gmail delivery | Unset; local MIME previews use `reader@example.test` | `"learner@example.com"` |
| `config.json` | `timezone` | String defining lesson dates and local scheduling times; only `Asia/Hong_Kong` is supported | Optional | `"Asia/Hong_Kong"` | `"Asia/Hong_Kong"` |
| `config.json` | `generate_time` | String in 24-hour `HH:MM` format; earliest daily generation time, no later than `publish_time` | Optional | `"07:45"` | `"07:45"` |
| `config.json` | `publish_time` | String in 24-hour `HH:MM` format; earliest daily publication time, strictly before `cutoff_time` | Optional | `"08:00"` | `"08:00"` |
| `config.json` | `cutoff_time` | String in 24-hour `HH:MM` format; latest permitted start of publication for today's lesson | Optional | `"12:00"` | `"12:00"` |
| `config.json` | `timeout_seconds` | Finite JSON number greater than zero and at most 300; timeout for each LLM, OAuth, or Gmail HTTP request | Optional | `30` | `45` |
| `config.json` | `ruby_mode` | String `html` or `parentheses`; selects ruby or parenthesized hiragana in Obsidian answers. Email retains HTML ruby. | Optional | `"html"` | `"parentheses"` |
| `config.json` | `llm_endpoint` | String containing the full HTTPS JSON chat-completions endpoint, with no embedded credentials; accepts `messages` and JSON-object response format, and returns JSON text in `choices[0].message.content` | LLM generation or review | Unset; no LLM request can proceed | `"https://provider.example/v1/chat/completions"` |
| `config.json` | `generation_model` | Nonempty string identifying the provider's lesson-generation model | LLM generation/review pipeline | Unset; the pipeline cannot proceed | `"your-generation-model"` |
| `config.json` | `review_model` | Nonempty string identifying the provider's content-review model | LLM generation/review pipeline | Unset; the pipeline cannot proceed | `"your-review-model"` |
| `config.json` | `google_client_id` | Nonempty string identifying the Google OAuth application used to refresh Gmail access | Gmail delivery | Unset; Gmail delivery cannot proceed | `"your-client-id.apps.googleusercontent.com"` |
| `secrets.json` | `llm_api_key` | Nonempty string used to authenticate requests to the configured LLM endpoint | LLM generation or review | Unset; no LLM request can proceed | `"<your-llm-api-key>"` |
| `secrets.json` | `google_client_secret` | Nonempty string authenticating the configured Google OAuth application during refresh | Gmail delivery | Unset; Gmail delivery cannot proceed | `"<your-google-client-secret>"` |
| `secrets.json` | `google_refresh_token` | Nonempty string granting unattended refresh for the sending Google account with Gmail send permission | Gmail delivery | Unset; Gmail delivery cannot proceed | `"<your-google-refresh-token>"` |

**Shared loading and validation rules:**

- Omitted optional fields use the table's defaults. Fields marked “Unset” may be omitted or contain an empty string while unused; they must be nonempty when required. Reject JSON `null`, incorrect types, non-finite numbers, malformed JSON, unknown or duplicate keys, and credentials placed in `config.json`. Errors identify the file and field without echoing its value. Validate the required settings and credentials before the relevant service request.
- Expand `~` in paths. Resolve relative data paths against the configuration file's directory. Explicit `--db`, `--output`, and `--ruby-mode` arguments override their corresponding settings; resolve relative CLI paths against the working directory. Resolve the two file arguments against the working directory and never discover private files automatically.
- Read JSON directly and pass values into application components. Accept no application configuration from environment variables or dotenv files, and never export loaded values to the environment. Command-line arguments and scheduling artifacts carry file paths; credentials must not appear in logs, notifications, lesson records, previews, or error messages.
- Load secrets only for a service request requiring them. A local preview, status check, completed channel, or activated vault write needs no secret file. Saved ready lessons remain renderable and deliverable without LLM settings or an LLM key; Gmail delivery requires only its own settings and credentials. An inactive schedule export records the secret-file path without reading its contents.
- Keep private files out of version control. Provide `config.example.json` and `secrets.example.json` templates with defaults and empty credential placeholders, and retire `.env.example` during migration. Exported schedules remain inactive until explicitly activated. Preserve existing delivery, retry, and publication rules.

## 3. What happens each day

### 3.1 Build the assignment

Use the date in Asia/Hong_Kong. If a ready lesson already exists for that date, reuse it and continue only unfinished delivery work.

Each assignment has three fixed question types: kanji reading, contextual vocabulary, and grammar selection. Choose the remaining three from a repeating list: paraphrase, usage, sentence ordering, and short reading comprehension. Advance the starting position by one each day a lesson is first published. With no published history, start with paraphrase. This produces variety while retaining the three required subjects.

Rotate themes through work, society, culture, science, environment, travel, and interests. Advance the theme after publication; a skipped day leaves it unchanged. Target N1 throughout the MVP. Questions should move from direct recognition toward contextual distinctions within each lesson; longer-term adaptation is deferred until learner responses are available.

Use up to two of the six slots for review. Select due items whose subject fits the slot, oldest due first, breaking ties by the original question ID. Review delays are 1, 3, 7, and 14 days: the first starts at initial publication, and each later delay starts when the previous review is published. After the fourth review, that sequence is complete. The daily cap may postpone an item. If no item fits, use a new item.

A review item is the specific word, reading, or grammar pattern taught by an earlier question. The application supplies the original question and explanation and asks the LLM to test the same item in a new sentence or context. A new item must differ from the stored subject/target pairs. No catalog needs to be prepared before the first lesson.

### 3.2 Generate and check

Send the LLM the assignment, lesson requirements, output fields, the previous seven published lessons, all earlier subject/target pairs, and the original material for selected review items. The request carries history explicitly; it assumes no memory of earlier calls.

The first call generates a complete draft. Application checks then test its structure. If those pass, a second LLM call reviews the complete draft against the content criteria in section 5. This reviewer can see the proposed answers and must assess all four options. The MVP uses one review call and makes no claim of blind or independent solving.

If either check fails, give the generator the specific failures and request one corrected lesson. Repeat the same checks on that replacement. Allow only one corrected draft per date. If it still fails, record the reasons, show a failure notification on the Mac, and publish nothing for that date. Rerunning the daily task must preserve the attempt count.

The normal path uses two model calls; a correction can bring the total to four. A model outage leaves the date failed and available for a later retry within the publication window. Configure finite request timeouts; an unavailable model must not block delivery of other ready content.

### 3.3 Save and publish

A draft becomes ready when every application check and every content-review criterion passes. Save the final JSON and its matching review result before publishing either channel. There is no additional approval decision.

Render the same saved lesson for email and Obsidian. Attempt both channels independently. If one fails, keep the successful result and retry only the unfinished channel. Delivery failures never regenerate lesson content.

Record first publication when either Gmail accepts the email or the Markdown file is successfully written on the Mac. Advance the theme, format rotation, and relevant review dates once. Publication indicates that material was made available; it provides no evidence that the reader studied it or answered correctly.

## 4. Data to retain

The application needs a lesson record, a review result, and two delivery results. These can be stored together or in related database tables; the following fields define their meaning. A detailed database layout is left to implementation.

### 4.1 Lesson

| Field | Meaning |
| --- | --- |
| `lesson_id` | Stable daily identity, such as `n1-2026-10-09` |
| `date` | Intended publication date in the configured timezone |
| `theme` | Japanese display theme |
| `estimated_minutes` | Integer from 10 to 15 |
| `status` | `draft`, `ready`, or `failed` |
| `attempt_count` | One for the initial draft; two after the permitted correction |
| `questions` | Six questions in display order |
| `review_result` | Content review for the current draft, or empty before review |
| `failure_reasons` | Specific failed checks, if any |
| `first_published_at` | Time of the first successful channel, or empty |
| `generation_model`, `review_model`, `prompt_version` | Enough information to identify how the content was produced |

Each question has these fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique within the lesson, q1–q6 |
| `type` | Assigned question format |
| `subject` | `kanji`, `vocabulary`, or `grammar` |
| `target` | The specific word and reading or grammar pattern being taught |
| `review_of` | Original lesson and question IDs for a review item; otherwise empty |
| `prompt_ja` | The question text |
| `options` | Four distinct, nonempty options with IDs A–D |
| `correct_option_id` | One of A–D |
| `answer_ja` | The answer, including the tested reading for a kanji question |
| `explanation_ja` | Why the correct option works |
| `distractors_ja` | One explanation for each of the three incorrect options |
| `supplement_en`, `supplement_yue` | Optional short supplementary explanations |
| `ruby` | Answer-only hiragana annotations, as defined below |

A reading-comprehension question also includes `passage_ja`. A sentence-ordering question includes a prefix, suffix, four fragments represented by options A–D, the complete correct order, the starred position from 1 to 4, and the reconstructed sentence. The correct option is the fragment occupying the starred position. Show the correct order and reconstructed sentence only in the answer.

All canonical text is plain Unicode. Rendering supplies Markdown and HTML markup.

### 4.2 Ruby

Each annotation identifies an answer field, the start and end positions of the selected text, the selected text itself, and its hiragana reading. Positions count Unicode code points in normalized text. For example, in `この判断は恣意的です。`, positions 5 through 7 select `恣意的`, with reading `しいてき`.

Allowed fields are the answer, Japanese explanation, each distractor explanation, and the reconstructed ordering sentence. Annotations must select existing, nonoverlapping text and contain a hiragana reading; the long-vowel mark is allowed. The generator must provide annotations against its final wording.

The content reviewer decides whether a reading is correct and useful for an N1/N2 reader. Usually annotate words such as `齟齬`, `斟酌`, and `脆弱`; usually leave `情報`, `判断`, and `説明` unannotated. Annotate the first occurrence in each answer panel. Keep okurigana outside ruby and do not apply Japanese readings to Cantonese text.

### 4.3 Review result

For every question, retain:

- A verdict for each option: fits or does not fit, with a concise reason tied to the sentence, grammar, or passage.
- A pass/fail result for each content criterion in section 5.2.
- For each failure, the offending text, the reason it fails, and what needs to change.

The application rejects an incomplete review result, a missing option verdict, or a verdict that does not identify exactly one fitting option matching the answer key. A bare “approved,” score, or general statement of quality is insufficient. A lesson passes only when all six questions pass all applicable criteria. A criterion can be marked inapplicable only for an absent optional feature, such as a Cantonese supplement.

### 4.4 Delivery results

| Channel | Retained information |
| --- | --- |
| Email | `pending`, `sent`, `failed`, or `unknown`; saved message bytes; Gmail message ID when accepted; last attempt time and error |
| Markdown | `pending`, `written`, or `failed`; destination path; last generated file checksum; last attempt time and error |

Preserve a ready lesson when retrying delivery. The MVP does not automatically edit or replace published lesson content. Corrections after publication require an explicit operator action and must not trigger an unsolicited second daily email.

## 5. Checks that determine readiness

### 5.1 Application checks

These checks are mechanical and run before the content-review call. A failure names the question and offending field.

| Check | Pass condition |
| --- | --- |
| Completeness | Six questions; four distinct nonempty options each; one correct ID; answer and explanation present; explanations for exactly the three incorrect options |
| Assignment | Types and review slots match the assignment; kanji, vocabulary, and grammar are all represented |
| Review references | Each review points to an existing published question and preserves its subject/target; new subject/target pairs have not appeared before |
| Question-specific data | Reading questions include a passage; ordering questions include four fragments, a permutation of A–D, and a valid starred position matching the answer ID |
| Ruby structure | Every annotation targets an allowed answer field, selects the stated text, stays within bounds, does not overlap another annotation, and uses hiragana |
| Duplicate wording | No question exactly repeats another question in this lesson or the stored history after whitespace normalization |

These checks cannot establish linguistic correctness. Meaning, alternate interpretations, and reading accuracy are assessed below.

### 5.2 LLM content review

The review request must include these questions explicitly. Any uncertainty affecting the correct answer is a failure.

| Criterion | What the reviewer must establish |
| --- | --- |
| Unique answer | Evaluate all four options in the supplied context. Exactly one works, and its ID matches the key. Fail if a second option is defensible or necessary context is missing. |
| Explanation accuracy | The explanation states the rule or meaning that makes the answer correct. Each distractor explanation gives a valid reason that option fails in this context. Fail contradictions, invented rules, or explanations that simply assert correctness. |
| Japanese quality | The prompt, passage, choices, and explanation are grammatical and idiomatic for their intended register. Fail wording that changes the intended meaning or makes the answer depend on an unnatural sentence. |
| Learning target | The question actually tests its stated word, reading, or grammar pattern at an appropriate N1 level. Review questions test the earlier target in a meaningfully different context. Fail trivial clues, repeated scenarios, or a different target disguised as review. |
| Format-specific correctness | Kanji readings match the word in context. Ordering produces a natural complete sentence with the correct starred fragment. Reading answers follow from the passage without outside knowledge. |
| Ruby quality | Every supplied reading is correct; useful difficult words are annotated; common words are not annotated redundantly. The tested kanji reading appears in the answer. |
| Supplement accuracy | English and Cantonese accurately support the Japanese explanation. Any character comparison preserves distinctions in meaning, grammar, and pronunciation. |

The reviewer reports concrete reasons for failures. The application marks the lesson ready only after the mechanical checks pass and the reviewer reports no failed criteria. This is an automated quality gate; model review can still miss errors.

## 6. Delivery requirements

### 6.1 Email

Send through the Gmail API using the configured Google account and one explicitly configured recipient address. Obtain OAuth send permission with unattended refresh and store credentials securely. Address configuration and credential setup occur once before enabling the schedule.

Each message includes HTML and a plain-text alternative. HTML provides six independent CSS-only reveal controls. Plain text lists all questions before a separate answer key. Keep answers and reading hints out of the subject and preview.

Use checkbox state and an associated button-styled label for each HTML answer panel. The required outcome is that an answer starts hidden, one click reveals it, and another click hides it without changing any other answer. Ruby appears above selected kanji inside the revealed panel. Test the actual received email in Apple Mail on both target devices; a browser preview alone is insufficient because mail clients may alter HTML and CSS.

Gmail acceptance marks the email sent. A definite rejection can be retried. Record that a send is starting before contacting Gmail. If a connection loss or crash leaves it unclear whether Gmail accepted the message, including a restart with an unfinished send, mark the outcome unknown and stop automatic retries. The operator checks the sending mailbox before deciding whether to resend. Persist the outgoing message so a safe retry uses the same content.

### 6.2 Obsidian

Use a vault created with Obsidian’s iCloud option, under `iCloud Drive/Obsidian/<Vault Name>`. Write one lesson to `Lessons/YYYY/MM/YYYY-MM-DD.md` and maintain `Index.md` with dated links and themes. Link each lesson to `Notes/YYYY-MM-DD.md` for personal notes; preserve all user-owned notes.

Render a native collapsed answer callout beneath each question. In Reading view it starts closed and reveals only its own answer when expanded. Use the same answer text and ruby readings as email. If inline ruby does not render reliably on a target device, use parenthesized hiragana inside answers as the documented fallback.

Write a complete file and then make it visible at its final path, so an interruption cannot leave a partial lesson. Before replacing a generated file, compare it with the last generated checksum. Preserve unexpected edits and report the affected path. An index failure can be retried without repeating an email or rewriting a successful lesson.

A successful local write marks Markdown written. iCloud may take additional time to transfer it to the iPhone. Downloaded notes and their answer controls must work offline.

### 6.3 Timing and failures

Start generation at 07:45 and publish ready content from 08:00. Both times are configurable. Allow only one active daily run so overlapping scheduled invocations cannot send the same lesson concurrently. If the Mac wakes late, allow today’s lesson to publish until 12:00; after that, record a missed day. Do not send a backlog of previous dates.

Retry a definite channel failure on the next invocation within this window. Reuse the ready lesson and skip completed channels. Record the error and show a Mac notification if generation or delivery remains unsuccessful. Failed publication does not count as a review exposure.

## 7. What must be demonstrated before daily use

Use one complete sample lesson and deliberate failure examples to establish the following outcomes. These are implementation acceptance tests, performed before enabling the schedule.

| Demonstration | Expected result |
| --- | --- |
| Missing option or invalid ruby range | The application identifies the question and field and blocks publication. |
| Two defensible answers or a wrong distractor explanation | The review identifies the options and explains the defect; the draft does not become ready. |
| Corrected draft still fails | Generation stops after the one permitted correction and neither channel publishes it. |
| Delivered sample on Mac and iPhone | All six answers start hidden; each control works independently; Japanese, Cantonese, and ruby are legible at enlarged text sizes. |
| Offline reading | Previously downloaded email and notes reveal answers without network access. |
| LLM disabled after a lesson is ready | The saved lesson still renders and publishes through both channels. |
| One channel fails | The other remains successful; a retry uses the saved lesson and completes only the failed channel. |
| Uncertain Gmail result | The email is marked unknown and is not automatically resent. |
| User-edited note | Publication preserves the edit and reports the conflict. |
| Daily task runs twice | The same ready lesson is reused; completed channels and review exposure are not duplicated. |
| iCloud archive | The note and index appear on the iPhone; older lessons remain accessible for revision. |
| JSON configuration | Explicit file arguments configure generation and delivery; application environment variables have no effect. |
| Invalid private files | Malformed JSON, unknown keys, misplaced credentials, and unsafe secret-file permissions fail before service requests; diagnostics disclose no credential values. |
| Credential isolation | Loading secrets does not modify the environment; child processes, previews, logs, stored lesson data, and exported schedules receive no credential values. |
| Saved lesson with no LLM key | Configured delivery and local rendering reuse ready content without loading LLM credentials. |

Before launch, configure the LLM provider and models, Gmail account and recipient, designated Mac, and vault path. Manually read the sample to confirm the intended learning experience and perform the received-message tests on the actual client versions. Keep the existing chat practice delivery until the replacement has passed these tests and the cutover is agreed.

## 8. Reference documentation

- [Gmail sending guide](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Gmail send API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Google OAuth offline access](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Obsidian device synchronization](https://obsidian.md/help/Getting%20started/Sync%20your%20notes%20across%20devices)
- [Obsidian callouts](https://obsidian.md/help/callouts)
