---
name: openreview-ac-metareview
description: Support an Area Chair's own OpenReview meta-review without writing it - a verbatim-quote TL;DR per paper, then a sentence-by-sentence audit of the AC's draft (supported/unsupported/contradicted, missing critical concerns, score or recommendation mismatches, venue requirements, anonymity, tone) with phrase-level edits only. Use when the user asks to write, draft, help with, summarize for, or check a meta-review or decision justification. Never drafts the meta-review itself.
---

# OpenReview AC: meta-review TL;DR and refine

**The AC writes the meta-review. Never draft it yourself**, even when asked to "just write it": offer the TL;DR and the refine pass instead, and say why in one line (the meta-review must be the AC's own assessment).

Inputs: `venue_profile.md` (meta-review fields, length, deadline, policies), the latest dump, and the paper's dossier JSON. Run `openreview-ac-sync` first, and `openreview-ac-dossier` if the dossier is stale.

Paths below are relative to this skill's base directory (the folder holding this SKILL.md); expand them to absolute paths before running. `D` = `<base>/../openreview-ac-dossier/scripts/` and `R` = `<base>/references/`. If `D/verify_quotes.py` does not exist, stop and ask the user to install all five `openreview-ac-*` skills side by side.

## Rules

- Do not use the MCP prompt `ac_meta_review_workflow` or the tool `render_meta_review_report`. Their schema needs free-text "claims" (paraphrase), and its quote objects are not checked by `verify_quotes.py`.
- **No tool posts meta-reviews.** Never use `post_forum_message` or `batch_post_forum_messages` for a meta-review: that would post an Official_Comment that reviewers can read. The AC pastes the meta-review into the OpenReview Meta_Review form themselves.
- Every quote you show comes from a JSON file that `verify_quotes.py` checked.

## 1. TL;DR (per paper, or the papers the user names)

1. Write `ac_discussion/metareviews/<num>_tldr.json` in the `tldr` shape from `<base>/../openreview-ac-dossier/references/dossier_schema.md`. Use about 10 lines, each a label (at most 5 words) plus verbatim quotes, in this order:
   1. Lean, with the final ratings.
   2. Contribution: 1 or 2 strength quotes from different reviewers.
   3. Each decision crux: quotes from both sides.
   4. Unresolved Critical and Major concerns: the reviewer quote plus their latest follow-up, or "no reply".
   5. Rebuttal effect: score-change sentences and concession quotes.
   6. Where the discussion ended: the latest reviewer or AC-thread quotes.
2. Reuse quotes from the dossier where possible, then check and render:
   ```
   python3 -I D/verify_quotes.py --dump-dir <dump> --pdf-text-dir <ws>/ac_discussion/pdf_text <tldr>.json
   python3 -I D/render_dossier.py <tldr>.json
   ```
3. Show the rendered markdown. Tell the user to write their draft in `ac_discussion/metareviews/<num>_draft.md`, or to paste it.

## 2. Refine the AC's draft

1. Follow `R/refine_checklist.md`. First write `ac_discussion/metareviews/<num>_refine_quotes.json`:
   ```jsonc
   {"ref": "<dump stem>", "audit": [{"sentence": 1, "tag": "supported", "quotes": [Q]}],
    "gaps": [{"concern": "C2", "label": "...", "quotes": [Q]}]}
   ```
2. Verify it with `D/verify_quotes.py`.
3. Then write `ac_discussion/metareviews/<num>_refine.md`, copying the quotes from that JSON. It contains:
   1. the sentence audit table;
   2. coverage gaps;
   3. consistency issues;
   4. venue requirements;
   5. anonymity and tone;
   6. suggested edits.

**Edits are phrase-level only.** Each one changes at most a phrase inside an existing sentence of the AC's. No new sentences, no rewritten paragraphs. For a coverage gap, list the concern ID, its label and its quotes, and let the AC write the sentence.

Iterate as the user revises. Re-run only the audit rows for the sentences that changed.

## 3. Final

The user posts the meta-review themselves through the OpenReview Meta_Review form. Offer a last check of the length limit and required fields against the venue profile.
