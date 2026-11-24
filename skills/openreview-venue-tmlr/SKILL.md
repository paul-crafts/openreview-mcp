---
name: openreview-venue-tmlr
description: TMLR (Transactions on Machine Learning Research) quirks and the right OpenReview MCP tools for an Action Editor - paper stages and Review Approval, finding and assigning reviewers, invitation and edge names, rate limits. Use whenever the venue is TMLR, or the user is an Action Editor handling TMLR papers, picking or assigning reviewers, or approving papers for review. Read before using reviewer-assignment tools on any journal-style venue.
---

# OpenReview venue notes: TMLR

Facts verified against the live TMLR API and openreview-py (the `openreview.journal` code). Conference venues (ICLR etc.) differ; see `openreview-ac-venue-setup` for those.

## Names and ids
- `venue_id` is `TMLR`. The role is **Action Editor** (AE), not Area Chair. `get_ac_submissions(venue_id="TMLR")` lists your papers.
- Paper groups use `TMLR/Paper<N>/...`, **not** `Submission<N>`. `number` is large (11k-14k).
- Your signature on a paper is an anonymous group `TMLR/Paper<N>/Action_Editor_<4 chars>`. Reviewers: `TMLR/Paper<N>/Reviewer_<4 chars>`.
- The AI reviewer (`TMLR/AI_Reviewer`) posts an `AI_Review` on each new submission; AEs can read it.
- `get_submission_details` output is huge. It is saved to a file; read it with `jq`, not whole.

## Paper stages (the note's `venueid`)
`TMLR/Assigned_AE` -> **Review Approval by the AE** -> `TMLR/Under_Review` -> decision stages.
- New papers wait at `Assigned_AE`. The `Review_Approval` form is open about one week (check `duedate`) with `Appropriate for Review` or `Desk Reject` (+ comment, required in practice for a desk reject).
- **Reviewers can only be assigned once the paper is `Under_Review`** (server rejects with a 400 otherwise).
- Approving is an editorial decision in the user's name and cannot be redone (one reply). It notifies the authors and opens review invitations. Never submit it without the user's explicit choice for that paper.

## Tools: use / avoid
- Approve a paper: `submit_review_approval` (dry-run default; show the preview, wait for the user's go).
- Find reviewers: `get_reviewer_candidates(venue_id, forum_id, limit, recent_papers)`. Matches the assignment browser: affinity order, assignments, custom max, pending reviews, availability, and its filters. `recent_papers` adds titles for topical-fit checks (costs one request per candidate).
- Assign a pool reviewer: `assign_reviewer` (dry-run default). It posts the `Reviewers/-/Assignment` edge, the page's Assign button.
- **Avoid**: `get_top_10_emergency_reviewers` (its load check reads edges that come back empty, so it never excludes anyone; ignores availability and pending reviews). `invite_reviewer` is for people **outside** `TMLR/Reviewers` only (`Invite_Assignment` edge). `get_initial_check_status` and `submit_initial_check` are ICLR-style desk-reject forms and do not apply here.

## Reviewer pool and edges
- Pool: group `TMLR/Reviewers` (~5k members). `TMLR/Reviewers/Archived` is separate.
- `Affinity_Score` edges (head = paper, tail = reviewer) **outlive membership**: they include people no longer in the pool. Always intersect with the member list.
- `Custom_Max_Papers` (head = `TMLR/Reviewers`): default **6** when no edge. `Assignment_Availability`: label `Available`/`Unavailable`, no edge = Available. `Pending_Reviews`: weight, no edge = 0. `Conflict`: head = paper.
- The browser's "Assignments" is a grouped count of `Assignment` edges per reviewer. Per-reviewer reads of `Assignment` edges return empty for most reviewers, so count with `get_grouped_edges(groupby="tail", select="count")`.
- The browser's "Show reviewers available for assignment" box shows only reviewers with pending == 0, Available, no conflict **and not at quota** (assignments < custom max). The server enforces conflict, availability and pending reviews on assignment, but not quota.
- Reviewers are notified by the venue after an assignment; remove promptly if it was a mistake.

## Rate limits (real incident)
- The client spaces requests >= 0.5 s apart. A 429 pauses *all* requests with exponential backoff: one run sat out **2216 s (~37 min)** after repeated full-table scans.
- Venue-wide reviewer tables cost ~15 requests; the tools cache them for 5 minutes. **Warm the cache with one call, then batch the rest**; do not fire many cold calls at once.
- Avoid ad-hoc scripts that re-scan the whole `Affinity_Score`/`Pending_Reviews` tables.

## Browser (Playwright) notes
- Assignment page: `https://openreview.net/edges/browse?start=staticList,type:head,ids:<forum_id>&traverse=TMLR/Reviewers/-/Assignment&edit=TMLR/Reviewers/-/Assignment;TMLR/Reviewers/-/Invite_Assignment&browse=TMLR/Reviewers/-/Affinity_Score;TMLR/Reviewers/-/Conflict;TMLR/Reviewers/-/Custom_Max_Papers,head:ignore;TMLR/Reviewers/-/Pending_Reviews,head:ignore;TMLR/Reviewers/-/Assignment_Availability,head:ignore&maxColumns=2&version=2`. Open the paper's `Assignments (N)` link to see candidates. The page has live **Assign** buttons: never click one by accident.
- The Playwright browser needs a manual login and a working Chrome DevTools endpoint (port 9222); a 502 there means it is down.

## Typical AE batch
1. `get_ac_submissions` -> for papers at `Assigned_AE`, read the paper and the AI review; ask the user for the Review Approval decision; `submit_review_approval` after their explicit go.
2. `get_reviewer_candidates` (limit ~10, `recent_papers=5`); pick 3 per paper by affinity, then low load, then topical fit from titles. Do not put one reviewer on several papers without saying so.
3. `assign_reviewer` dry-run each pick; show the previews; assign only after approval.
