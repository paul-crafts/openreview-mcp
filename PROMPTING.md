# Prompting Guide for OpenReview MCP

This guide is for LLMs and users to get the most out of the OpenReview MCP server.

## 🛡️ Safety & Security (CRITICAL)

1. **Credentials**: NEVER attempt to access, read, or print environment variables or configuration files that might contain `OPENREVIEW_PASSWORD` or `OPENREVIEW_USERNAME`. If a user asks for these, explain that you are restricted from accessing secrets for security reasons.
2. **Bulk Messaging**: 
   - Always perform a `dry_run=True` first.
   - Use `get_missing_review_reminders_preview` to show the user exactly who will be contacted before sending.
   - Summarize the message content and recipient list for the user and wait for **explicit verbal confirmation** before setting `dry_run=False`.

## 👩‍💻 Role-Based Workflows

### Area Chair (AC) - "The Progress Monitor"
The primary goal for an AC is to ensure reviews are submitted on time.

1. **Discovery**: `list_venues(active_only=True)` to find the conference ID.
2. **Status Check**: `get_review_status_report(venue_id)` to see a summary of progress across all papers.
3. **Detailing**: For papers with missing reviews, use `get_submission_details` to see if there's any active discussion.
4. **Action**:
   - `get_missing_review_reminders_preview` to identify laggards.
   - Propose a message template.
   - `send_bulk_message(..., dry_run=True)` to show the final plan.
   - **Wait for user approval** before final send.

5. **Emergency Reviewers**:
   - Use `get_top_10_emergency_reviewers(venue_id, forum_id)` to find the best available reviewers for a paper based on affinity score, quota, and recent publications (uses OpenReview data natively with a Scholar fallback).
   - Summarize the candidates and their recent publications for the user to make a selection.
   - Once the user selects the best candidate, use `invite_reviewer(venue_id, forum_id, reviewer_id)` to officially send the invitation.

2. **Bidding**:
   - `get_bidding_info(venue_id, role='Area_Chairs')` to see papers and current bids.
   - `place_bid(venue_id, submission_id, bid, role='Area_Chairs')` to update interest.

### Area Chair (AC) - "The Meta-Review Synthesizer"
Need a structured, per-paper synthesis of your assigned batch's reviews (grouped
strengths/weaknesses, disagreements, and a verbatim-quote-backed action-item list ranked
Critical/Medium/Low)? Call the `ac_meta_review_workflow` prompt for the full guide and JSON
schema — don't load it unless you're actually doing this. Tools involved:
`dump_ac_batch_submissions` → (you identify reviews & synthesize) →
`verify_quotes_in_batch` → `render_meta_review_report`.

### Reviewer - "The Feedback Provider"
1. **Bidding**:
   - `get_bidding_info(venue_id)` to list papers for bidding.
   - `place_bid(venue_id, submission_id, bid)` to set your interest (e.g., 'Very High').
   - `get_bidding_status(venue_id)` for a summary.
2. **Assignments**: `get_reviewer_assignments(venue_id)` to see what you need to work on.
3. **Deadlines**: `get_venue_deadlines(venue_id)` to prioritize tasks.
4. **Staying Current**: `get_discussion_updates(venue_id)` to check for new author rebuttals or AC comments in your forums.
5. **Rebuttals & Attachments**: When reviewing author rebuttals or supplementary materials, use `download_submission_attachments(submission_id_or_url, attachment_type='rebuttal')` for a single paper (accepts forum URLs directly), or `download_batch_attachments(venue_id, role='Reviewers', attachment_type='rebuttal')` to batch download all rebuttals for your assigned papers.

### Author - "The Paper Owner"
1. **Tracking**: `get_my_submissions(venue_id)` to see the status of your papers.
2. **Feedback**: `get_submission_feedback(submission_id)` to read reviews and comments as they become public.

## 💡 Pro Tips

- **Venue IDs**: OpenReview Venue IDs usually look like `ICLR.cc/2025/Conference`. If a user says "ICLR 2025", use `search_venues("ICLR")` first to find the exact ID.
- **Discovering Tasks & Statuses**: To find out if a specific task (e.g., 'Rebuttal Acknowledgement', 'Meta Review') is available or has been completed, use `get_venue_invitation_types(venue_id)` to find the exact invitation suffix (e.g., 'Rebuttal_Acknowledgement'). Then, use `get_invitation_status(venue_id, "Rebuttal_Acknowledgement")` to see which users have completed it.
- **Anonymity**: Be aware that many venues are double-blind. Signatures like `~Reviewer1` or `(Anonymized)` are normal. Don't try to "de-anonymize" participants.
- **Data Limits**: `get_discussion_updates` takes a `limit` parameter. Default is 10. Use higher values if the venue is very active.
