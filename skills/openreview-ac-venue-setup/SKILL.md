---
name: openreview-ac-venue-setup
description: Set up an Area Chair workspace for an OpenReview venue by collecting that conference's current guidelines (AC, reviewer, author pages), timeline, rating scale, review and meta-review forms, and messaging rules into venue_profile.md. Use when starting AC work on a new conference or phase, when the user asks "what are the rules this year", or when another openreview-ac-* skill finds no venue_profile.md. Run once per venue and again when the phase changes.
---

# OpenReview AC: venue setup

Procedures differ by conference and year: rating scales, discussion phases, who can see what, meta-review fields, LLM policies. Every other `openreview-ac-*` skill reads `venue_profile.md` instead of assuming ICLR, NeurIPS or ICML conventions. This skill writes that file.

**Workspace**: the folder the user runs Claude from (for example `~/git/Conferences/ICLR2027`), unless they name another one. `venue_profile.md` goes at its root.

**Requires** the `openreview` MCP server (`mcp__openreview__*` tools). If they are unavailable, stop and tell the user to register it.

## 1. Ask the user (one AskUserQuestion round, plus free text)

- The venue id (for example `ICLR.cc/2027/Conference`). If they don't know it, use `search_venues`.
- Guideline sources: AC guidelines, reviewer guidelines, author guidelines (URLs, local files, or pasted text). **Always ask; never rely on memory of past years.**
- The current phase as they understand it (for example "reviewer-AC discussion"), and their next deadline.
- Their own preferences for messages (tone, sign-off), if not already in memory.

## 2. Collect the sources

- **URLs**: try `WebFetch`.
  - If it fails with a proxy or network error, say in one sentence that the site is blocked by the network or sandbox proxy.
  - Ask the user to paste the text, or save the page into the workspace (for example `guidelines/ac.md`).
  - Under a sandboxed client with a domain allowlist, the user can allowlist the domain themselves. Never edit allowlists or sandbox settings yourself.
- Check for local files too: `papers/`, `guidelines/`, any `*Guidelines*.md`.
- **From OpenReview** (read-only):
  - `get_venue_deadlines`;
  - `get_venue_invitation_types` (which phases or invitations exist: Official_Comment, Meta_Review, Rebuttal_Acknowledgement, and so on);
  - `get_ac_submissions`, or the dump manifest;
  - `get_forum_message_options(venue_id, <one paper>)` for the comment invitation, the open and close dates, the allowed readers (are Authors still allowed?), the max comment length, and the AC signature.
- **The review form, from real data.** Call `dump_ac_batch_submissions(venue_id, output_dir=<workspace>/forum_dump_<YYYY-MM-DD>)` directly (do not go through the sync skill, which needs this profile). Then read one dump JSON and list the `content` keys of a reply whose invitation ends in a review-like suffix. That gives:
  - the review invitation suffix;
  - the rating field and its observed values;
  - the confidence field and the sub-score fields;
  - the free-text fields.

  Do not guess field names.

## 3. Write `venue_profile.md`

Use this structure. Copy guideline statements **verbatim** with their source (URL or file and section). Your own words are limited to the short headings and field values.

```markdown
# Venue profile: <venue id>   (updated <date>)

## Sources
- AC guidelines: <url or file> (fetched/pasted <date>)
- ...

## Timeline   (verbatim lines + source)
## Current phase
- phase: <name>; open until <date> (from get_forum_message_options expiry or guidelines)
- authors can still reply: yes/no; author-visible readers allowed: yes/no

## Rating scale
- field `rating`: values seen <list>; meaning (verbatim from guidelines or form): ...
- field `confidence`: ...
- sub-scores: `soundness`, `presentation`, ...
- boundary between reject side and accept side: <number>  (used by dashboard.py --boundary)

## Review form fields
- invitation suffix: `Official_Review`; free-text fields: ...

## AC duties in this phase   (verbatim)
## Meta-review   (form fields, length limits, required content, deadline; verbatim)
## Policies   (LLM use by reviewers and ACs, confidentiality, page limit, late results; verbatim)
- page limit for revisions (main-text pages): <int or UNKNOWN>   (used by pdf_extract.py --page-limit)
- anonymity rules for authors (links, code, model hubs, comments) and what triggers desk rejection: <verbatim or UNKNOWN>   (for red_flags.py leads)
- scope of rebuttal revisions (how much new work counts): <verbatim or UNKNOWN>   (for the "out of the original scope" question)

## Messaging
- forum comment invitation: <...>; max comment length: <int>; title max: <int>
- email: invitation `<venue>/Submission<N>/-/Message`, parent group `<venue>/Submission<N>/Reviewers`
- AC signature per paper: <list or "from get_forum_message_options">; saved as ac_discussion/messages/signatures.json at send time
- sign-off: <text, default "Your Area Chair">
- user's message rules: <from memory or the user>

## Excluded papers   (never message; sync refreshes this list, and messages re-checks it live before sending)
- <num>: <venueid> / <Withdrawal or Desk_Rejection note>
```

## 4. Close

- Show the user any gaps (for example "meta-review form fields unknown: paste the form when it opens").
- Suggest the next step: the `openreview-ac-sync` skill.
- Gaps stay marked as `UNKNOWN` in the profile; never fill them from memory of another venue.
