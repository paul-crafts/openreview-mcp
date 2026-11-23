# Dossier JSON schema

One file per paper: `ac_discussion/<num>_dossier.json`. It is rendered with
`scripts/render_dossier.py` and checked with `scripts/verify_quotes.py`.

## The quote object (Q)

Every piece of reviewer, author, PC or PDF text is a Q. Nothing else in the file
may contain text taken from a source.

```jsonc
{"who": "Rv2b", "text": "<exact substring of a note signed by Reviewer_Rv2b>"}
{"who": "Authors", "text": "<exact substring of an author note>"}
{"who": "PC", "text": "..."}          // Program_Chairs; "AC" = the paper's Area_Chair_*
{"who": "pdf", "pdf": "1234_revised", "page": "5", "text": "<excerpt from pdf_text/1234_revised.txt>"}
```

Copying rules:
- Copy from the dump JSON's decoded strings, keeping markdown (`**`, `$...$`), punctuation and capitalization. Do not fix typos.
- One sentence or clause per Q is ideal. Long passages are allowed, but no ellipses inside a Q: use two Qs instead.
- For PDF excerpts, copy from `pdf_text/<num>_revised.txt` (or `_original.txt`). Whitespace differences and line-break hyphens are tolerated. `page` is 1-based, counting form feeds.
- If you cannot find the exact source text, drop the point. Never approximate.

## Top level

```jsonc
{
  "ref": "paper_1234_A_Paper_Title_Truncated_To_Fifty_Characters",  // dump filename stem (required)
  "number": 1234,
  "title": "A Paper Title: ...",
  "dump_dir": "forum_dump_2026-11-20",
  "depth": "full",                     // or "light"

  "scores": [                          // numbers/dates/flags only; columns are free-form
    {"reviewer": "Rv2b", "rating": "2 -> 3", "confidence": 2, "engaged": "yes", "PC flag": "-"}
  ],

  "decision": {
    "lean": "Borderline",              // Accept | Lean accept | Borderline | Lean reject | Reject
    "cruxes": [
      {"label": "Theorem exactness", "for": [Q], "against": [Q], "would_flip": "Rv3c confirms fix"}
    ]
  },

  "concerns": [
    {
      "id": "C1",
      "label": "Theorem exactness",    // <= 5 words, your words
      "severity": "Critical",          // Critical | Major | Minor
      "raised_by": ["Rv2b", "Rv1a", "Rv3c"],
      "reviewer_quotes": [Q],           // one Q per raising reviewer, at least
      "author_response": [Q],           // who = "Authors"; empty list if none
      "response_type": "revision made", // new experiment | clarification | revision made | camera-ready promise | rebuttal of the point | none
      "followups": [Q],                 // the reviewer's own post-rebuttal words; empty = "no reply"
      "status": "Partially",           // Resolved | Partially | Unresolved | Disputed | Not addressed
      "status_source": "reviewer",     // "reviewer" when a follow-up states it, else "AC inferred"
      "validity": {                     // is the reviewer's claim about the (original) paper true?
        "verdict": "verified true",     // verified true | partly true | verified false | could not verify | not checked
        "evidence": [Q]                 // PDF text, or an author concession in the forum
      },
      "resolution_check": {             // did the AC verify the fix the authors claim?
        "verdict": "verified in revision", // verified in revision | rebuttal only | not in revision | promise only | not verified | n/a
        "evidence": [Q]                 // revised-PDF text showing the fix, or the forum text it lives in
      }
    }
  ],

  "strengths": [{"label": "Strong OOD gains", "quotes": [Q, Q]}],   // one Q per agreeing reviewer

  "disagreements": [
    {"label": "Value of imitation baselines",
     "sides": [{"position": "Sufficient", "quotes": [Q]}, {"position": "Insufficient", "quotes": [Q]}]}
  ],

  "quality": [
    // tag: PC AI-use flag | AI documentation | wrong claim | non-engagement | conditional promise | low confidence | other
    {"reviewer": "Rv3c", "tag": "PC AI-use flag", "quotes": [Q], "met": "n/a"},
    {"reviewer": "Rv2b", "tag": "conditional promise", "quotes": [Q, Q], "met": "yes"}   // promise Q + evidence Q
  ],

  "confidential": [{"label": "Request for reconsideration", "quotes": [Q]}],

  "open_questions": [                   // inputs for the messages skill
    {"to": "Rv1a", "label": "Is revised Thm 2.1 enough?", "quotes": [Q]}
  ]
}
```

## Severity rubric

- **Critical**: if true, a central claim is unsupported or the method is unsound. The paper cannot be accepted until this is resolved.
- **Major**: materially weakens the evidence or the contribution (missing key baseline, weak statistics, limited scope). Fixing it needs new work.
- **Minor**: presentation, clarity, citations, small additions that fit a camera-ready.

## Validity rules (the reviewer's claim, judged against the paper)

- **verified true**: the paper text (or an explicit author concession) shows the claim holds.
- **partly true**: part of the claim holds, part does not, or it is overstated. The evidence shows both parts.
- **verified false**: the paper text shows the claim is wrong, for example a reviewer misread something that is stated. Quote the passage.
- **could not verify**: you looked, but no text settles it. This includes pure judgments such as novelty or significance, unless the authors concede the point.
- **not checked**: not examined. This is allowed only for Minor concerns.
- **Evidence comes from the original submission** (`<num>_original.txt`) or from an explicit author concession. Never from text or results that exist only in the revision or the rebuttal: those show the *resolution*, not whether the original claim was valid. Judged against the original, a concern that the revision later fixed is usually `verified true`. A revised-PDF quote is acceptable only when the same passage is unchanged in the original; in that case cite the original.

## Resolution-check rules (the claimed fix, verified by the AC)

- **verified in revision**: the revised PDF contains the fix. Quote it from `<num>_revised.txt`, with the page.
- **rebuttal only**: the results or explanation exist only in the forum, not in the PDF. Quote the forum text.
- **not in revision**: the authors say the PDF was changed, but the revised PDF does not contain the change. If the authors only gave the results in the forum and never claimed a PDF change, use `rebuttal only` instead.
- **promise only**: the fix is promised for the camera-ready.
- **not verified**: there is a claimed fix you did not check. This is allowed only for Minor concerns.
- **n/a**: nothing is claimed. If the authors made any revision or ran a new experiment for the concern, check it, even when the concern stays Unresolved. A fix can be present (`verified in revision`) and still fail to convince.

## Status rules

- Use the reviewer's own follow-up when there is one (`status_source: "reviewer"`).
- Otherwise judge from the rebuttal and the revision, and mark `"AC inferred"`.
- A response of type `camera-ready promise` can be at most `Partially`.
- `Disputed`: the authors argue that the reviewer is wrong, and the reviewer has not conceded.

## Meta-review TL;DR (same file, or `metareviews/<num>_tldr.json`)

```jsonc
{"ref": "...", "number": 1234, "title": "...",
 "tldr": {"lean": "Borderline",
          "lines": [{"label": "Main strength", "quotes": [Q]},
                    {"label": "Unresolved: theory", "quotes": [Q, Q]}]}}
```

Limit it to about 10 `lines`. Labels are at most 5 words; everything else is a Q.
