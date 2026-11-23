# Meta-review refine checklist

Input: the AC's own draft (`metareviews/<num>_draft.md`), the paper's dossier JSON, the dump and the venue profile.

Output: `metareviews/<num>_refine.md`. Never rewrite the draft wholesale; the AC's voice stays.

## 1. Sentence audit (main table)

Split the draft into sentences. For each sentence, give one tag:

| tag | meaning | evidence column |
|---|---|---|
| supported | a source says this | one or two verbatim Qs (who + text) |
| partly | a source says something narrower or weaker | the Q, plus a one-line note on what differs (at most 12 words) |
| unsupported | no source found | "-" |
| contradicted | a source says the opposite | the Q |
| AC judgment | the AC's own evaluative statement (fine to keep) | "-" |

All Qs pass `verify_quotes.py` before the table is shown.

## 2. Coverage

- Every Critical concern in the dossier, and every Major concern that is still Unresolved or Disputed, is mentioned or deliberately set aside. List the missing ones by concern ID, label and their Qs. Do not write the missing sentence for the AC.
- Where the discussion changed scores or positions, the draft reflects it (for example "after the rebuttal, X raised to 3").
- If the venue asks for it, the draft explains why the decision goes against a majority or a strong outlier review.

## 3. Consistency

- The recommendation agrees with the text: no "clear contribution" next to a reject without a stated reason, and the reverse.
- Score numbers and reviewer IDs in the draft match the dashboard.
- Statements about the revision ("the authors added X") match a `resolution_check` of `verified in revision`. Statements that a reviewer was wrong match a `validity` of `verified false`.

## 4. Venue requirements (from the venue profile)

- Required form fields, length limits, and anything the guidelines ask meta-reviews to contain or avoid.
- Policies such as AI-use disclosure for meta-reviews, if the venue has one.

## 5. Anonymity and tone

- No reviewer real names, no hints at identities, no author names. Refer to reviewers by their anonymous ID, or as "reviewers".
- No dismissive or personal language about authors or reviewers. Flag sharp phrases, with a neutral alternative of at most 10 words.

## 6. Suggested edits

- A short list of `- "old phrase" -> "new phrase"`. Each edit changes at most a phrase inside one existing sentence of the AC's, and links to the finding above that motivates it.
- No new sentences, no unified diff, no rewritten paragraphs. Coverage gaps are listed in section 2 (concern ID, label, Qs) for the AC to write.
- Only fix what the audit found. No style rewrites unless the AC asks for them.
