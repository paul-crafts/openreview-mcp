# AC Meta-Review Synthesis Workflow

This is the detailed guide for synthesizing a structured, per-paper meta-review report
from an Area Chair's assigned batch of submissions — grouped Strengths/Weaknesses,
Disagreements, Isolated points, and a verbatim-quote-backed Action Items list ranked
Critical/Medium/Low. Only load this document when you're actually doing this task; for
routine AC progress-monitoring, see `PROMPTING.md` / the `openreview_instructions` prompt
instead.

## The pipeline

```
dump_ac_batch_submissions
        │
        ▼
(you, the LLM, read the raw dumps and synthesize — no tool call)
        │
        ▼
verify_quotes_in_batch
        │
        ▼
render_meta_review_report
```

## Step 1: Dump

Call `dump_ac_batch_submissions(venue_id)`. It writes one raw JSON file per non-withdrawn
assigned submission and returns a compact manifest — **never review content**. Note each
paper's `ref` value (the filename stem); you'll need it in every later step.

## Step 2 (you, the LLM, do this — no tool call): identify reviews and synthesize

Each `<ref>.json` file has the shape `{"submission": {...}, "replies": [...]}`. `replies`
contains **every** note in the forum — reviews, author comments, AC comments,
withdrawal/decision notices — **nothing has been filtered or classified for you**. This is
deliberate: which invitation suffix means "official review," which content field is a
rating vs. free text, and how reviewer signatures are formatted are all
venue-customizable conventions that differ across conferences and tracks. A fixed
heuristic baked into the dump tool would silently break on the next venue with a
different review-form template — so that judgment call is yours, made against the real
data in front of you each time.

- **Identify official review notes yourself**, by reading each reply's `invitations`
  list. Reviews are usually the notes whose invitation path ends in something like
  `.../Official_Review`, but conventions vary (a round suffix, a differently-named review
  type, a separate meta-review invitation for ACs, etc.) — use the dump manifest's
  `reply_invitation_tally` (a simple count per distinct invitation suffix) as a quick hint
  of what's actually present in this venue, then confirm by inspecting the real invitation
  strings on the notes themselves. If nothing in the tally looks review-like, investigate
  before assuming there are no reviews — don't silently produce an empty synthesis.
- **Get a short reviewer label** from each review's `signatures` field — often something
  like `.../Reviewer_xuxJ` (take the trailing identifier, `xuxJ`) or already a bare
  anonymous handle like `~Reviewer1`. Never attempt to resolve this to a real identity —
  many venues are double-blind and this is a hard rule, not just for this workflow.
- **Read each review's `content` dict as-is.** Field names vary by venue (a rating might
  be called `rating`, `overall_score`, `recommendation`, or something else entirely; some
  venues have no single numeric rating at all; free-text fields might be `summary`,
  `strengths_and_weaknesses`, `review`, or a breakdown like `soundness`/`presentation`/
  `contribution`). Use your judgment on what's a rating/confidence/free-text field based
  on what's actually there — don't assume a fixed schema, and don't skip a paper's review
  just because its fields don't match a pattern you saw on a previous paper in the same
  batch.
- **Produce the synthesis**, grouped like this for each paper:
  - **Strengths** — points multiple reviewers agree on, grouped together with a quote
    from each agreeing reviewer.
  - **Weaknesses** — same grouping, with a **Minor** sub-group for small/nitpick issues.
  - **Disagreements / Conflicts** — points where reviewers gave opposing assessments;
    include a quote from each side.
  - **Isolated points** — points raised by only one reviewer, grouped by reviewer.
  - **Action Items for Authors** — categorized by this rubric:
    - **Critical**: absolutely needed for the paper to be accepted (solving these does
      not by itself guarantee acceptance).
    - **Medium**: assuming the critical issues are solved, needed to move the paper from
      the borderline region into the acceptance region.
    - **Low**: low priority; not a game changer.

  **Every bullet in every group needs a verbatim quote attributed to a reviewer ID.**
  Never paraphrase a claim without also having the exact substring you paraphrased from —
  the next step checks this. If you can't find a supporting quote, either find the right
  one or drop the claim; never approximate one.

### Synthesis JSON schema

Assemble one object with this shape (pass it to `render_meta_review_report` in Step 4):

```jsonc
{
  "venue_id": "NeurIPS.cc/2026/Conference",
  "venue_display_name": "NeurIPS 2026",          // optional, falls back to venue_id
  "generated_at": "2026-07-14T18:32:00Z",         // optional, tool fills in now() if absent
  "papers": [
    {
      "ref": "paper_17_Some_Paper_Title",          // MUST match dump manifest's `ref`
      "number": 17,
      "title": "Some Paper Title",
      "reviewers": [{"id": "xuxJ", "rating": 6}],  // optional; badges derived from quotes if omitted
      "strengths": [
        {
          "claim": "The proposed method achieves state-of-the-art results on 3 benchmarks.",
          "who": ["xuxJ", "aB3q"],                   // reviewer short-sigs agreeing
          "quotes": [
            {"reviewer": "xuxJ", "text": "results are consistently ahead of the strongest baseline"},
            {"reviewer": "aB3q", "text": "SOTA on all three datasets tested"}
          ],
          "note": null                                // optional free-text annotation
        }
      ],
      "weaknesses": [ /* same shape as strengths */ ],
      "minor": [ /* same shape — the "Minor" subgroup under weaknesses */ ],
      "disagreements": [
        {
          "claim": "Reviewers disagree on whether the ablation in Table 3 is sufficient.",
          "who": ["xuxJ", "aB3q"],
          "quotes": [
            {"reviewer": "xuxJ", "text": "the ablation study is thorough and convincing"},
            {"reviewer": "aB3q", "text": "the ablation in Table 3 omits the key baseline"}
          ],
          "note": null
        }
      ],
      "isolatedPoints": [
        {
          "reviewer": "R9mZ",                         // isolated points are grouped BY reviewer
          "claim": "Requests clarification on the training schedule in Section 4.2.",
          "quotes": [{"reviewer": "R9mZ", "text": "it is unclear how the learning rate schedule was tuned"}],
          "note": null
        }
      ],
      "actionItems": {
        "critical": [
          {
            "claim": "Add the missing baseline comparison requested by two reviewers.",
            "who": ["xuxJ", "aB3q"],
            "quotes": [{"reviewer": "xuxJ", "text": "without this baseline the claim of SOTA is unsupported"}],
            "note": "References Table 3."   // free text; table/fig/prop refs go here
          }
        ],
        "medium": [ /* same shape */ ],
        "low": [ /* same shape */ ]
      }
    }
  ],
  "excludedWithdrawn": [
    {"id": "defUVW456", "number": 22, "title": "Withdrawn Paper Title"}
  ],
  "verificationLog": {                                 // fill this in from Step 3's result
    "total_checked": 47,
    "passed": 47,
    "failed": 0,
    "failures": []
  }
}
```

Every leaf item (`strengths`/`weaknesses`/`minor`/`disagreements`/`isolatedPoints`/
`actionItems.*`) shares one shape: `claim` (str), `who` (list) or `reviewer` (single str,
isolated points only), `quotes` (list of `{reviewer, text}`), optional `note`. Table/
figure/proposition references belong in the free-text `note` field, not a separate
structured field.

## Step 3: Verify

Call `verify_quotes_in_batch(quotes, output_dir)` with **every** `{ref, quote}` pair you
used in Step 2 — one entry per quote across every group and every paper — plus any
external citation you named in an Action Item's `note` (a table/figure/proposition
number, a paper title) if that exact text also appears verbatim in the dump. Fix any
reported failures by re-checking the source file and correcting the quote (or dropping the
claim) before proceeding — a synthesis with unverified quotes should not be rendered. Copy
the tool's result directly into the synthesis JSON's `verificationLog` field.

## Step 4: Render

Once `all_passed` is `true`, call `render_meta_review_report(synthesis, output_dir)`. This
writes both a `.md` file and a self-contained `.html` file (sidebar nav with scroll-spy,
color-coded strength/weakness/disagreement/action-item sections, collapsible verification
log, and copy/view/download-as-markdown buttons) to `output_dir`. Share the `.html` path
with the user for review in a browser.

## Reminder

Every claim in every section needs a verbatim, attributed quote. If you can't find a
supporting quote for a claim, either find the right one or drop the claim — never invent
or approximate one.
