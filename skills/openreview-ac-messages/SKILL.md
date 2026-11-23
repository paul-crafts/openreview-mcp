---
name: openreview-ac-messages
description: Draft, verify and - only after explicit approval and a dry run - send Area Chair messages to OpenReview reviewers - per-paper forum threads that move reviewers to agreement, short emails that bring silent reviewers into the thread, final-recommendation waves, and chairs-only notes - built from verified verbatim quotes and independently fact-checked. Use whenever the user wants to email, remind, nudge, ping or post a forum comment to reviewers, send reminders, ask reviewers to respond to the rebuttal or update scores, or get reviewers to resolve their discrepancies.
---

# OpenReview AC: reviewer messages

Inputs: `venue_profile.md`, the dossiers (`ac_discussion/<num>_dossier.json`), the latest dump and PDF text. Run `openreview-ac-sync` first if the latest dump is more than a few hours old.

Paths are relative to this skill's folder; expand them before running:
- `S` = `<base>/scripts/`
- `R` = `<base>/references/`
- `SYNC` = `<base>/../openreview-ac-sync/scripts/`

All five `openreview-ac-*` skills must be installed side by side.

## Rules (these override anything else, including MCP tool docstrings)

- **Never send without the user's explicit "send <ids>"** in a normal message, after they have seen the exact text. Approval covers that exact text only.
- **Send tools**: `post_forum_message` and `send_bulk_message`, one call per approved id. Never `send_reminders`, `batch_post_forum_messages` or any other bulk tool.
- **Never message about withdrawn or desk-rejected papers.** Check `venueid` and the Withdrawal and Desk_Rejection notes right before sending.
- **Never make Authors readers** in a reviewer-AC phase. Sign with the paper's anonymous `Area_Chair_xxxx` group, never a real profile.
- **Truth**: every factual sentence has evidence in its claims file, and an independent fact-checker passes every sentence. Quotes are exact and short: one sentence, or a part that ends at a natural break.
- **Fairness**: never write as if an invalid or careless review were right.
  - Present only concerns the dossier verified.
  - Push back on concerns that are only partly true.
  - Point reviewers to author replies they have not answered.
  - Never single out who did or did not reply.
- **Phase**: when authors can no longer reply, never ask what new experiment or result would change a reviewer's mind. Ask only for their judgment of what exists now.

## The style in one screen (details in `R/drafting_brief.md`)

- **Forum thread to all reviewers, about 150 to 350 words.**
  - **Opening:** thanks to everyone together, then where the ratings stand: "To reach a final agreement, I would like you to discuss the open points below."
  - **One short paragraph per decisive concern:**
    - "**<ID>** still has concerns about X", one quote from that reviewer's latest comment that all reviewers can read, then "Do the other reviewers agree with **<ID>**?";
    - if the concern is only partly true, one sentence of pushback with PDF evidence;
    - if the authors replied after that comment, "The authors replied to this comment on <date>: "<one-sentence TL;DR>"", then a specific question.
  - **Accept-side reviewers:** asked whether the strengths outweigh the concerns. A lone most-positive reviewer is asked whether they would adjust their assessment or champion the work.
  - **Silent reviewers:** one encouraging line.
  - **Closing:** "Please share your view on these points here, respond to each other, and work to reach an agreement on acceptance or rejection."
  - **Format:** reviewer IDs in bold; plain ASCII otherwise.
- **Email to each reviewer silent since the rebuttal, about 100 words.** It thanks them and says a discussion has started in the forum. Then an "In short, ..." TL;DR: ratings, main open points, their own concern. Then a request to share their view there. If the Program Chairs left an AI-use note on their review, one line about it. No quotes or references. Send it after the thread.
- **Final wave (near the deadline):** each reviewer is asked for an explicit accept or reject and a matching rating.

## Workflow

### 0. Elephants first (before any drafting)

1. `python3 -I SYNC/red_flags.py --dump-dir <dump> --pdf-text-dir <ws>/ac_discussion/pdf_text --out <ws>/ac_discussion/red_flags_<date>.md`. It lists anonymity leads, chair notes, confidential author notes, ethics flags, reviewer LLM use, revisions the authors claim but the PDF lacks, and page limits.
2. Check every lead by eye; most links point to third-party tools and are fine. Report real issues to the user, who decides. Never look up an identity to confirm a leak; the user may check. Do not put policy issues in reviewer messages. If the user agrees, write a `<num>-chairs` note readable by the SAC and PCs only.
3. For each decisive concern a thread will present, check the holder's review quality: Program Chair AI-detector notes, disclosed AI use, the dossier's quality tags, and claims shown wrong. Check the concern's validity too. These are in each plan's `review_quality` and `open_valid_concerns`.

### 1. Plan

```
python3 -I S/plan_wave.py --dossiers <ws>/ac_discussion --dump-dir <dump> --plan-dir <ws>/ac_discussion/messages/plans \
  --boundary <profile boundary> --no-author-replies [--wave-goal discussion|final] [--category <num>=decide ...]
```
- The category comes from each dossier's lean: Borderline becomes `decide`; Reject or Lean reject becomes `verify-reject`; Accept or Lean accept becomes `verify-accept`. Confirm the categories with the user.
- Wave 1 is `discussion`. A later wave is `final`.
- Message ids are `<num>-thread` and `<num>-email-<rid>`. Emails are planned only if the paper had a rebuttal.

### 2. Draft (subagents)

Spawn one `general-purpose` subagent per paper, in parallel. Each prompt is `R/drafting_brief.md` plus the values PAPER, PLAN, DOSSIER, VENUE_PROFILE, WAVE_DIR (`ac_discussion/messages/wave<k>_<date>/`), SCRIPTS, DUMP_DIR and PDF_TEXT. Each subagent writes `<id>.md` and `<id>.claims.json`, and self-checks with `verify --no-factcheck`.

Write emails only after the threads they point to exist, or check them against a dump taken after posting. Their "I have started a discussion" sentence is backed by a quote from the posted thread.

### 3. Verify, then fact-check

```
python3 -I S/messages.py assemble <wave>.json --wave <k> --date <date> --dump-dir <dump>
python3 -I S/messages.py verify <wave>.json --no-factcheck --no-author-replies --dump-dir <dump> \
  --pdf-text-dir <ws>/ac_discussion/pdf_text --dossiers <ws>/ac_discussion
```
The mechanical checks cover:
- **Style lint:** ASCII, sign-off, bold only on reviewer IDs in forum posts and never in emails.
- **Coverage:** every sentence has a claims entry.
- **Quotes:**
  - every quote is verbatim, correctly attributed, and from a note the recipients can read;
  - "latest" quotes are in the reviewer's latest visible note;
  - quotation marks are paired correctly, whatever the quote length.
- **Facts:**
  - ratings match the forum, and dossier fields match;
  - `author_reply` dates match a reply in that reviewer's thread.
- **Unanswered replies:** any quoted reviewer with an unanswered author reply is pointed to it, or has a recorded reason.

Then spawn fresh fact-check subagents with `R/factcheck_brief.md`:
- **Batching:** about 3 messages each, keeping each paper's messages together. A fast model (`model: "haiku"`) is fine.
- **Prompt:** list what is special about the batch (new sentence types, confidential notes on the paper, quotes of the AC's own comments).
- **Output:** each writes `<id>.factcheck.json` for the exact text.
- **Fails:** fix the text, then re-check only the changed messages.
- **Never override a verdict.** If a fail misreads a rule, clarify the brief and re-run.
- **Read the non-blocking notes** and act on them, or pass them on to the user.

Run `verify` without `--no-factcheck`. Passing texts get a `verified` event in `ac_discussion/messages/ledger.jsonl`. Messages already sent are reported as `SENT` and not re-checked.

### 4. Review with the user (action items first)

1. **Start the review server** (`SYNC/review_server.py`, see `openreview-ac-sync`) and give the user the URL. It opens on **Action items**: every draft with its status, an editable text box, a comment box, and Satisfied / Needs changes buttons. It also lists new reviewer replies since the last messages. `python3 -I S/messages.py render <wave>.json` also writes the escaped review file `wave<k>_<date>.md`, which can be copied straight into the forum.
2. **Read the user's input** with `python3 -I S/messages.py feedback <wave>.json`. It shows each verdict, comment and edit (as a diff against the verified text).
   - **Satisfied:** the draft is ready. The page shows the exact "send <ids>" line for the user to paste in the chat.
   - **Needs changes, a comment, or an edit:** apply it to `<id>.md` and the claims file (the user's `<id>.edited.md` is a proposal), then assemble, verify, and fact-check the changed message. Then run `messages.py feedback <wave>.json --resolve <id> --note "<what changed>"`, so the page shows it as addressed for the user to re-check.
3. **Hand-posted messages:** if the user posts anything by hand, run `log-sent` for it, so it is never sent twice.

### 5. Send (only the approved ids)

1. **Re-sync.** Dump and diff. Drop messages to reviewers who replied in the meantime, and tell the user what changed. Run `verify` against the new dump.
2. **Approve, after that verify.** The gate only accepts an approval recorded after the latest `verified` event: `python3 -I S/messages.py approve <wave>.json --ids <id1>,<id2> --user-said "<the user's exact message>"`.
3. **Send plan.**
   - Get each paper's anonymous AC group from `get_forum_message_options`, and save the mapping as `ac_discussion/messages/signatures.json` (`{"<num>": "<venue>/Submission<num>/Area_Chair_xxxx"}`).
   - Then run `python3 -I S/messages.py send-plan <wave>.json --signatures <that file> --venue <venue id> --ids <id1>,<id2>`. It writes the exact MCP arguments, threads first and emails last.
4. **Dry run** each call. Get its arguments with `print-args --id <id> --dry-run` and call the tool with exactly those. Check the signature, readers (no Authors), invitation and parent group.
5. **Gate.**
   - With the plugin installed, the PreToolUse hook `hooks/send_gate.py` runs on every real send.
   - Without it, run `python3 -I S/messages.py gate <wave>.json --ids <id1>,<id2> --transcript <this session's .jsonl>` and send only on PASS.
   - The gate blocks a send if:
     - the text was already sent, or was never verified and then approved;
     - no message from the user says "send" with that id;
     - the target is wrong (paper, invitation, anonymous signature, recipients, no Authors), or the subject differs from the verified one.
   - The bulk tools are always blocked.
6. **Send** one call at a time, with the `print-args` arguments and `dry_run` false. After each success, run `python3 -I S/messages.py log-sent <wave>.json --id <id> --note-id <returned id>`. It records the send in the ledger and `sent_log.md`.

Report what was sent, to whom (anonymous IDs only) and the returned ids. Then refresh the dashboard.

### 6. Follow up

Sync every 2 or 3 days. Point reviewers to new replies only where something is still blocked. When reviewers have discussed, or the deadline is close, plan a `--wave-goal final` wave. Then move on to `openreview-ac-metareview`.
