---
name: openreview-venue-tmlr
description: TMLR (Transactions on Machine Learning Research) quirks and the right OpenReview MCP tools for an Action Editor - the Review Approval desk-check (criteria, scripted PDF checks, desk-reject effects), paper stages, finding / assigning / removing reviewers, edge and invitation names, rate limits. Use whenever the venue is TMLR, or the user is an Action Editor handling TMLR papers, approving or desk-rejecting new papers, picking or assigning reviewers. Read before using reviewer-assignment tools on any journal-style venue.
---

# OpenReview venue notes: TMLR

Facts verified against the live TMLR API, openreview-py (`openreview.journal`) and TMLR's own Action Editor guide (https://jmlr.org/tmlr/ae-guide.html, https://jmlr.org/tmlr/editorial-policies.html). Conference venues (ICLR etc.) differ; see `openreview-ac-venue-setup` for those.

## Names and ids
- `venue_id` is `TMLR`. The role is **Action Editor** (AE), not Area Chair. `get_ac_submissions(venue_id="TMLR")` lists your papers.
- Paper groups use `TMLR/Paper<N>/...`, **not** `Submission<N>`. `number` is large (11k-14k).
- Your signature on a paper is an anonymous group `TMLR/Paper<N>/Action_Editor_<4 chars>`. Reviewers: `TMLR/Paper<N>/Reviewer_<4 chars>`.
- Each new submission gets an `AI_Review` from `TMLR/AI_Reviewer`, readable by AEs.
- `get_submission_details` output is huge. It is saved to a file; read it with `jq`, not whole.

## Paper stages (the note's `venueid`) and clocks
`TMLR/Assigned_AE` -> **Review Approval by the AE** -> `TMLR/Under_Review` -> decision stages.
- You have **1 week** from assignment for the Review Approval (see the invitation's `duedate`). After approving you have **1 week to assign 3 reviewers**.
- Reviewers can only be assigned once the paper is `Under_Review` (the server rejects with a 400 otherwise). The move to Under Review is applied within seconds of approving.
- **Appropriate for Review**: releases the approval to the authors and notifies them, makes the paper public, expires the form (one reply, not redoable). **Desk Reject**: needs a comment for the authors, opens a desk-rejection approval task for the Editors in Chief (due in 3 days); the paper stays out of review unless they decline. Format-violation rejections are contingent on that approval.
- Both are editorial decisions in the user's name. Never submit one without the user's explicit choice for that paper, and show the comment text first (a Desk Reject comment goes to the authors verbatim).

## Review Approval checklist (what "appropriate for review" means)
From the AE guide. Clear grounds to desk reject: **not anonymized**; **not the unmodified TMLR stylefile** (standard PDF submissions only; Beyond-PDF submissions are exempt but must have content that needs the format); **clear overlap with work already published in proceedings or under review elsewhere**. Other grounds: out of scope; the reviewer pool lacks expertise; unlikely to meet the acceptance criteria (e.g. lack of technical depth). Also check the **submission type** under the abstract ("Regular submission (no more than 12 pages of main content)" or long) against the real length. If unsure, or for ethics concerns, ask the Editors in Chief. The authors know who the AE is at this stage.

Procedure:
1. Get the PDF into its **own new, empty directory** (untrusted): `client.get_pdf(forum_id)` with the MCP's client, or `download_submission_attachments`.
2. Run `python3 -I scripts/desk_check.py PAPER.pdf --author "<surname>" --author "<full name>" [--limit 12]`. It reports main-content pages vs the limit, anonymity signals (names tagged "likely citation" when they sit in the references or before "et al"), emails, code/profile links, PDF Author metadata, the TMLR header, and the text face (Latin Modern Roman; Nimbus/Times body faces point to a modified template).
3. Look at the rendered pages yourself (`pdftoppm -f 1 -l 3 -r 60 -png ...`): section numbers, headings, captions, margins. The script cannot judge these.
4. Overlap: a title search on arXiv (`https://export.arxiv.org/api/query?search_query=ti:"..."`) shows preprints. A preprint alone is not overlap under the guide's wording (proceedings / concurrent submission). Under-review-elsewhere cannot be checked. (The auto-mode classifier sometimes blocks `WebFetch` to arXiv; do not work around it, tell the user.)
5. Summarize per paper, name what could not be checked, let the user decide, then `submit_review_approval` (dry-run, show, post).

## Tools: use / avoid
- Approve / desk reject: `submit_review_approval` (dry-run default).
- Find reviewers: `get_reviewer_candidates(venue_id, forum_id, limit, recent_papers)`. Matches the assignment browser: affinity order, assignments, custom max, pending reviews, availability, member-only. `recent_papers` adds titles for topical-fit checks (one request per candidate).
- Assign a pool reviewer: `assign_reviewer` (dry-run default). Posts the `Reviewers/-/Assignment` edge, the page's Assign button. Undo: `unassign_reviewer` (dry-run default; refuses if the reviewer already posted a review).
- **Avoid**: `get_top_10_emergency_reviewers` (its load check reads edges that come back empty, so it never excludes anyone; ignores availability and pending). `invite_reviewer` is for people **outside** `TMLR/Reviewers` only (`Invite_Assignment`). `get_initial_check_status` / `submit_initial_check` are ICLR-style desk-reject forms.

## Reviewer pool and edges
- Pool: group `TMLR/Reviewers` (~5k members). `TMLR/Reviewers/Archived` is separate. `get_groups(id=..., member=...)` returned nothing for real members; use the group's member list.
- `Affinity_Score` edges (head = paper, tail = reviewer) **outlive membership**: they include people no longer in the pool.
- `Custom_Max_Papers` (head = `TMLR/Reviewers`): default **6** when no edge. `Assignment_Availability`: `Available`/`Unavailable`, no edge = Available. `Pending_Reviews`: weight, no edge = 0. `Conflict`: head = paper.
- The browser's "Assignments" is a grouped count of `Assignment` edges per reviewer. Per-reviewer reads of `Assignment` edges return empty for most reviewers; count with `get_grouped_edges(groupby="tail", select="count")`. Reading by `head` (one paper) works for its AE.
- The browser's "Show reviewers available for assignment" box shows only reviewers with pending == 0, Available, no conflict **and not at quota** (assignments < custom max). The server enforces conflict, availability and pending reviews, not quota.
- **Loads change within hours**: a reviewer who looked free in the morning had a pending review by the afternoon and was refused. Dry-run again right before posting, never assign from an old list.
- The strongest topical matches are often at quota or have a pending review, so the browser hides them. `get_reviewer_candidates(..., include_at_quota=True)` shows who is hidden.
- Reviewers are notified by the venue after an assignment (short delay); remove promptly if it was a mistake.

## Rate limits (real incident)
- The client spaces requests >= 0.5 s apart. A 429 pauses *all* requests with exponential backoff: one run sat out **2216 s (~37 min)** after repeated full-table scans.
- Venue-wide reviewer tables cost ~15 requests; the tools cache them for 5 minutes. **Warm the cache with one call, then batch the rest**; never fire many cold calls at once.
- Avoid ad-hoc scripts that re-scan the whole `Affinity_Score` / `Pending_Reviews` tables.

## Browser (Playwright) notes
- Assignment page: `https://openreview.net/edges/browse?start=staticList,type:head,ids:<forum_id>&traverse=TMLR/Reviewers/-/Assignment&edit=TMLR/Reviewers/-/Assignment;TMLR/Reviewers/-/Invite_Assignment&browse=TMLR/Reviewers/-/Affinity_Score;TMLR/Reviewers/-/Conflict;TMLR/Reviewers/-/Custom_Max_Papers,head:ignore;TMLR/Reviewers/-/Pending_Reviews,head:ignore;TMLR/Reviewers/-/Assignment_Availability,head:ignore&maxColumns=2&version=2`. Open the paper's `Assignments (N)` link to see candidates. The page has live **Assign** buttons: never click one by accident.
- The Playwright browser needs a manual login and a working Chrome DevTools endpoint (port 9222); a 502 there means it is down. The MCP tools do not depend on it.

## Typical AE batch
1. `get_ac_submissions`; for papers at `Assigned_AE`, run the Review Approval procedure above and get the user's decision per paper; post with `submit_review_approval`.
2. For approved papers: `get_reviewer_candidates` (limit ~10, `recent_papers=5`); pick 3 per paper by affinity, then low load, then topical fit from titles. Do not put one reviewer on several papers without saying so.
3. `assign_reviewer` dry-run each pick, show the previews, assign after approval. If a pick is refused (pending review, quota), choose a replacement with the user.
