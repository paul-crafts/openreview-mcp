---
name: openreview-ac-dossier
description: Build a per-paper Area Chair dossier from an OpenReview forum dump and the revised PDF - every strength and weakness per reviewer as verbatim quotes, equivalent concerns grouped across reviewers, severity (Critical/Major/Minor), what the rebuttal did for each concern and whether the reviewer accepted it, verification of each concern's validity and of each claimed resolution against the paper text, review-quality flags, and the cruxes that decide accept vs reject. Use when the user asks to summarize or analyze the reviews of a paper, list strengths/weaknesses, check whether a rebuttal addressed concerns, verify reviewer claims, find disagreements, or decide where a paper stands during the discussion phase. For meta-review TL;DRs use openreview-ac-metareview.
---

# OpenReview AC: per-paper dossier

Prerequisites, in the workspace: `venue_profile.md`, a fresh `forum_dump_<date>/`, `ac_discussion/pdf_text/` and `ac_discussion/00_dashboard.md`. If any is missing, run `openreview-ac-venue-setup` or `openreview-ac-sync` first.

`S` = `<this skill's base directory>/scripts` and `R` = `<base>/references`. Expand both to absolute paths in every command and every subagent prompt. The scripts import `../openreview-ac-sync/scripts/dumplib.py`, so `openreview-ac-sync` must be installed alongside this skill under the same name.

## The verbatim rule

Every piece of reviewer, author, PC or PDF text is a quote object, an exact copy checked by `S/verify_quotes.py`. Your own words are limited to labels (at most 5 words), enum fields, and `would_flip` (at most 8 words). No paraphrase and no summaries; drop anything you cannot quote. The schema is in `R/dossier_schema.md`.

## 1. Triage (with the user if unclear)

Use the dashboard to set the depth per paper:
- **full**: contested ratings, borderline means, active disputes, confidential notes, or conditional promises.
- **light**: unanimous and clear-cut, or no rebuttal at all. Only Critical and Major concerns, at most 6, with validity and resolution checks for the ones that drive the decision.

Skip excluded papers (withdrawn or desk rejected).

## 2. Build the dossiers

- **One or two papers**: follow `R/agent_brief.md` yourself.
- **Three or more**: spawn one `general-purpose` subagent per paper, all in one message so they run in parallel (in waves of about 10 for large batches).
  - Each prompt is the full text of `R/agent_brief.md` (or its absolute path) plus the filled-in values: PAPER, REF, WORKSPACE, DUMP_DIR, PDF_TEXT, SCRIPTS (absolute `S`), SCHEMA (absolute `R/dossier_schema.md`), DEPTH, VENUE_PROFILE, and DASHBOARD (where that paper's dashboard section is).
  - Agents write only `ac_discussion/<num>_dossier.json` and `.md`.

Useful commands (the brief has them too):
```
python3 -I S/print_forum.py <dump>/<ref>.json [--only-signer Reviewer_xxxx]
python3 -I S/verify_quotes.py --dump-dir <dump> --pdf-text-dir <ws>/ac_discussion/pdf_text <ws>/ac_discussion/<num>_dossier.json
python3 -I S/check_dossier.py <ws>/ac_discussion/<num>_dossier.json
python3 -I S/render_dossier.py <ws>/ac_discussion/<num>_dossier.json
```

## 3. Check every dossier yourself (don't trust agent reports)

1. Re-run `verify_quotes.py` on all the dossier JSON files at once (it must print `0 failed`), then `S/check_dossier.py` on them.
   - The check flags verdicts without the right kind of evidence: validity resting only on revision-era text, "n/a" where a fix was claimed, Critical/Major concerns left unchecked.
2. Spot-check the scores table against the dashboard.
3. Spot-check each Critical concern's `validity` and `resolution_check`: read the PDF excerpt in context in `pdf_text/<num>_*.txt`, and confirm the verdict follows from it.
4. Look for a lean that disagrees with the scores without a crux that explains it.

## 4. Batch calibration (update the AC-notes part of `00_dashboard.md`)

- Order the papers by lean, then by how much they hinge on a single open crux.
- Flag mismatches between rating and text. For example, a reviewer whose follow-up says the concerns were addressed but whose rating did not move. Refer to the dossier concern ID; any quote you paste is copied from the verified dossier JSON.
- List the decisions only the AC can make: confidential author requests, suspected AI-generated reviews, page limit, ethics.

## 5. Present to the user

- One paper at a time, borderline first.
- Show the rendered `.md` path and a 5-line digest: the lean, the cruxes, unresolved Critical and Major concern IDs, and which reviewers to ask what.
- Then offer `openreview-ac-messages` to act on the open questions.

## Updating after a new sync

Re-run steps 2 and 3 only for papers whose diff shows new reviewer comments, score changes or new author notes. Keep the concern IDs stable, and update `followups`, `status` and `decision`.
