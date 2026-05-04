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

### Reviewer - "The Feedback Provider"
1. **Assignments**: `get_reviewer_assignments(venue_id)` to see what you need to work on.
2. **Deadlines**: `get_venue_deadlines(venue_id)` to prioritize tasks.
3. **Staying Current**: `get_discussion_updates(venue_id)` to check for new author rebuttals or AC comments in your forums.

### Author - "The Paper Owner"
1. **Tracking**: `get_my_submissions(venue_id)` to see the status of your papers.
2. **Feedback**: `get_submission_feedback(submission_id)` to read reviews and comments as they become public.

## 💡 Pro Tips

- **Venue IDs**: OpenReview Venue IDs usually look like `ICLR.cc/2025/Conference`. If a user says "ICLR 2025", use `search_venues("ICLR")` first to find the exact ID.
- **Anonymity**: Be aware that many venues are double-blind. Signatures like `~Reviewer1` or `(Anonymized)` are normal. Don't try to "de-anonymize" participants.
- **Data Limits**: `get_discussion_updates` takes a `limit` parameter. Default is 10. Use higher values if the venue is very active.
