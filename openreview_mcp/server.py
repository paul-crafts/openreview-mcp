import os
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from openreview.api import OpenReviewClient
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Initialize FastMCP
mcp = FastMCP("OpenReview MCP")

# Global client cache
_client_instance: Optional[OpenReviewClient] = None


def get_client() -> OpenReviewClient:
    """Get the cached OpenReview API v2 client or create a new one."""
    global _client_instance

    if _client_instance is not None:
        return _client_instance

    username = os.environ.get("OPENREVIEW_USERNAME")
    password = os.environ.get("OPENREVIEW_PASSWORD")
    baseurl = os.environ.get("OPENREVIEW_BASEURL", "https://api2.openreview.net")

    if not username or not password:
        raise ValueError(
            "OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD environment variables must be set."
        )

    _client_instance = OpenReviewClient(
        baseurl=baseurl, username=username, password=password
    )
    return _client_instance


@mcp.tool()
def get_profile() -> Dict[str, Any]:
    """Get the current user's OpenReview profile information."""
    client = get_client()
    profile = client.profile
    return profile.to_json()


@mcp.tool()
def list_venues(active_only: bool = True) -> List[Dict[str, str]]:
    """
    List venues where the user has an active role.
    Set active_only=False to see historical venues.
    """
    client = get_client()
    my_id = client.profile.id
    groups = client.get_groups(member=my_id)

    import datetime

    current_year = datetime.datetime.now().year
    active_years = [str(current_year), str(current_year + 1), str(current_year - 1)]

    venues = []
    seen_venues = set()

    for group in groups:
        parts = group.id.split("/")
        if len(parts) >= 2:
            potential_venue = None
            role = parts[-1]
            if "Area_Chairs" in parts:
                potential_venue = "/".join(parts[: parts.index("Area_Chairs")])
            elif "Reviewers" in parts:
                potential_venue = "/".join(parts[: parts.index("Reviewers")])
            elif "Authors" in parts:
                potential_venue = "/".join(parts[: parts.index("Authors")])
            elif "Program_Chairs" in parts:
                potential_venue = "/".join(parts[: parts.index("Program_Chairs")])

            if potential_venue and potential_venue not in seen_venues:
                # Filter by year if active_only is True
                if active_only and not any(
                    year in potential_venue for year in active_years
                ):
                    continue

                venues.append({"id": potential_venue, "role": role})
                seen_venues.add(potential_venue)

    return venues


@mcp.tool()
def search_venues(query: str) -> List[Dict[str, str]]:
    """Search for a venue by name or ID (e.g., 'ICLR')."""
    # Get all venues and filter by query
    all_venues = list_venues(active_only=False)
    query = query.lower()
    return [v for v in all_venues if query in v["id"].lower()]


@mcp.tool()
def get_ac_submissions(venue_id: str) -> List[Dict[str, Any]]:
    """Get submissions assigned to the current user as an Area Chair."""
    client = get_client()
    my_id = client.profile.id

    # Get assignments
    assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )

    submission_ids = [edge.head for edge in assignments]
    if not submission_ids:
        return []

    # Fetch notes for these submissions
    submissions = [client.get_note(sid) for sid in submission_ids]

    return [
        {
            "id": s.id,
            "title": s.content.get("title", {}).get("value", "No Title"),
            "number": s.number,
            "forum": s.forum,
        }
        for s in submissions
    ]


@mcp.tool()
def get_review_status_report(venue_id: str) -> List[Dict[str, Any]]:
    """Get a report on review progress for AC-assigned submissions."""
    client = get_client()
    my_id = client.profile.id

    # 1. Get assigned submissions
    ac_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )
    submission_ids = [edge.head for edge in ac_assignments]
    if not submission_ids:
        return []

    # 2. Get all reviewer assignments for these submissions
    # We can filter by 'head' if the API supports it, or get all and filter locally
    reviewer_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Assignment"
    )
    # Filter for our submissions
    reviewer_assignments = [a for a in reviewer_assignments if a.head in submission_ids]

    # 3. Get all notes for these submissions to find reviews
    # We fetch all notes in forums to handle per-submission invitations
    all_notes = []
    for sub_id in submission_ids:
        all_notes.extend(client.get_all_notes(forum=sub_id))

    # Map reviewers to their reviews per submission
    report = []

    # Cache for de-anonymizing signatures
    signature_cache = {}

    def resolve_signature(client, signature):
        if signature.startswith("~") or "@" in signature:
            return signature
        if signature in signature_cache:
            return signature_cache[signature]

        try:
            group = client.get_group(signature)
            if group.members:
                resolved = group.members[0]
                signature_cache[signature] = resolved
                return resolved
        except Exception:
            pass
        return signature

    for sub_id in submission_ids:
        sub_reviewers = [a.tail for a in reviewer_assignments if a.head == sub_id]
        # Filter for reviews in this forum
        sub_reviews = [
            n
            for n in all_notes
            if n.forum == sub_id
            and any("Official_Review" in inv for inv in n.invitations)
        ]

        submitted_by = []
        for r in sub_reviews:
            if r.signatures:
                resolved = resolve_signature(client, r.signatures[0])
                submitted_by.append(resolved)

        missing = [rev for rev in sub_reviewers if rev not in submitted_by]

        report.append(
            {
                "submission_id": sub_id,
                "total_reviewers": len(sub_reviewers),
                "reviews_submitted": len(sub_reviews),
                "missing_reviewers": missing,
            }
        )

    return report


@mcp.tool()
def identify_missing_reviews(venue_id: str) -> List[Dict[str, Any]]:
    """Identify reviewers who haven't submitted their assigned reviews."""
    # This is a refinement of the status report
    report = get_review_status_report(venue_id)
    missing = []
    for item in report:
        for rev in item["missing_reviewers"]:
            missing.append({"submission_id": item["submission_id"], "reviewer": rev})
    return missing


@mcp.tool()
def get_missing_review_reminders_preview(venue_id: str) -> List[Dict[str, Any]]:
    """Identify which reviewers will receive a reminder (those who haven't submitted)."""
    client = get_client()
    report = get_review_status_report(venue_id)

    targets = []
    for item in report:
        if item["missing_reviewers"]:
            # Fetch submission title for better preview
            sub = client.get_note(item["submission_id"])
            title = sub.content.get("title", {}).get("value", "No Title")
            for rev in item["missing_reviewers"]:
                targets.append(
                    {
                        "reviewer": rev,
                        "submission_id": item["submission_id"],
                        "submission_title": title,
                    }
                )
    return targets


@mcp.tool()
def send_bulk_message(
    venue_id: str,
    recipients: List[str],
    subject: str,
    message: str,
    reply_to: Optional[str] = None,
    invitation: Optional[str] = None,
    signature: Optional[str] = None,
    parent_group: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Send a message to a list of recipients.
    Set dry_run=False to actually send the emails.
    """
    client = get_client()

    if dry_run:
        return {
            "status": "preview",
            "message": "This is a dry run. The following recipients would have received the email.",
            "recipients": recipients,
            "subject": subject,
            "reply_to": reply_to,
            "invitation": invitation,
            "signature": signature,
            "parent_group": parent_group,
            "body_preview": message[:100] + "..." if len(message) > 100 else message,
        }

    try:
        response = client.post_message(
            subject=subject,
            recipients=recipients,
            message=message,
            replyTo=reply_to,
            invitation=invitation,
            signature=signature,
            parentGroup=parent_group or venue_id,
        )
        return {"status": "success", "response": response}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_submission_details(submission_id: str) -> Dict[str, Any]:
    """Get full details of a submission, including reviews and comments."""
    client = get_client()
    submission = client.get_note(submission_id)
    # Get all responses (reviews, comments, etc.)
    replies = client.get_all_notes(forum=submission_id)

    return {
        "submission": submission.to_json(),
        "replies": [r.to_json() for r in replies],
    }


# --- Reviewer Tools ---


@mcp.tool()
def get_reviewer_assignments(venue_id: str) -> List[Dict[str, Any]]:
    """List submissions the user is assigned to review."""
    client = get_client()
    my_id = client.profile.id

    assignments = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Assignment", tail=my_id
    )

    submission_ids = [edge.head for edge in assignments]
    if not submission_ids:
        return []

    submissions = [client.get_note(sid) for sid in submission_ids]
    return [s.to_json() for s in submissions]


# --- Author Tools ---


@mcp.tool()
def get_my_submissions(venue_id: str) -> List[Dict[str, Any]]:
    """List all submissions where the user is an author."""
    client = get_client()
    my_id = client.profile.id

    # Try common author identification methods
    submissions = client.get_all_notes(
        invitation=f"{venue_id}/-/Submission", content={"authorids": my_id}
    )

    # If empty, try searching in authors list
    if not submissions:
        # Some venues might use a different filter
        pass

    return [s.to_json() for s in submissions]


@mcp.tool()
def search_submissions(venue_id: str, query: str) -> List[Dict[str, Any]]:
    """Search for submissions in a venue by title or abstract keywords."""
    client = get_client()
    # Simple search by fetching and filtering
    submissions = client.get_all_notes(invitation=f"{venue_id}/-/Submission")

    results = []
    query = query.lower()
    for s in submissions:
        title = s.content.get("title", {}).get("value", "").lower()
        abstract = s.content.get("abstract", {}).get("value", "").lower()
        if query in title or query in abstract:
            results.append(s.to_json())

    return results


@mcp.tool()
def get_venue_deadlines(venue_id: str) -> List[Dict[str, Any]]:
    """Get important deadlines for a venue."""
    client = get_client()
    invitations = client.get_all_invitations(prefix=f"{venue_id}/")

    deadlines = []
    for inv in invitations:
        if inv.duedate:
            # Convert timestamp to human readable
            import datetime

            dt = datetime.datetime.fromtimestamp(inv.duedate / 1000.0)
            deadlines.append(
                {
                    "invitation": inv.id,
                    "deadline": dt.isoformat(),
                    "name": inv.id.split("/")[-1],
                }
            )

    return deadlines


@mcp.tool()
def get_server_time() -> Dict[str, Any]:
    """
    Get the current time from the OpenReview server.
    Useful for synchronizing cron jobs or checking for clock drift.
    """
    client = get_client()
    # Use a simple, fast request
    response = client.session.get(client.baseurl + "/groups", params={"id": "public"})
    server_date_str = response.headers.get("Date")

    import datetime

    # Parse 'Mon, 04 May 2026 09:28:05 GMT'
    server_dt = datetime.datetime.strptime(server_date_str, "%a, %d %b %Y %H:%M:%S %Z")
    local_dt = datetime.datetime.now(datetime.timezone.utc)

    return {
        "server_time_gmt": server_date_str,
        "server_timestamp_ms": int(server_dt.timestamp() * 1000),
        "local_time_utc": local_dt.isoformat(),
        "drift_seconds": (
            server_dt.replace(tzinfo=datetime.timezone.utc) - local_dt
        ).total_seconds(),
    }


def _parse_since(since: Optional[str]) -> int:
    """Parse a time duration string (e.g. '1d', '2h', '30m') to a millisecond timestamp."""
    if not since:
        return 0
    import datetime
    import re

    now = datetime.datetime.now()
    match = re.match(r"(\d+)([dhmin]*)", since.lower())
    if not match:
        return 0

    value, unit = match.groups()
    value = int(value)
    if unit.startswith("d"):
        delta = datetime.timedelta(days=value)
    elif unit.startswith("h"):
        delta = datetime.timedelta(hours=value)
    elif unit.startswith("m"):
        delta = datetime.timedelta(minutes=value)
    else:
        delta = datetime.timedelta(days=value)

    return int((now - delta).timestamp() * 1000)


def _summarize_note(note: Any) -> Dict[str, Any]:
    """Create a token-savvy summary of a note."""
    summary_content = {}
    for k, v in note.content.items():
        val = v.get("value") if isinstance(v, dict) else v
        if isinstance(val, str) and len(val) > 500:
            val = val[:500] + "... [truncated]"
        summary_content[k] = val

    return {
        "id": note.id,
        "forum": note.forum,
        "replyto": note.replyto,
        "signatures": note.signatures,
        "readers": note.readers,
        "content": summary_content,
        "cdate": note.cdate,
    }


@mcp.tool()
def get_ac_updates(
    venue_id: str, since: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Get forum updates for papers where you are Area Chair.
    Filters for reviews, official comments, and rebuttals.
    'since' can be '1d', '2h', '30m' or a millisecond timestamp.
    """
    client = get_client()
    my_id = client.profile.id
    since_ts = _parse_since(since)

    # Get assigned submissions as AC
    assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )
    forums = [e.head for e in assignments]
    if not forums:
        return []

    updates = []
    for forum_id in forums:
        notes = client.get_all_notes(forum=forum_id, mintcdate=since_ts)
        for n in notes:
            # We want almost everything in the forum as AC
            updates.append(_summarize_note(n))

    updates.sort(key=lambda x: x["cdate"] if x["cdate"] else 0, reverse=True)
    return updates[:limit]


@mcp.tool()
def get_reviewer_updates(
    venue_id: str, since: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Get updates for papers you are reviewing.
    Filters for AC comments, other reviewer comments (if visible), and author rebuttals.
    """
    client = get_client()
    my_id = client.profile.id
    since_ts = _parse_since(since)

    # Get assigned submissions as Reviewer
    assignments = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Assignment", tail=my_id
    )
    forums = [e.head for e in assignments]
    if not forums:
        return []

    updates = []
    for forum_id in forums:
        notes = client.get_all_notes(forum=forum_id, mintcdate=since_ts)
        for n in notes:
            # Reviewers should see new comments/rebuttals
            invs = getattr(n, "invitations", [])
            if any(
                "Comment" in inv or "Rebuttal" in inv or "Decision" in inv
                for inv in invs
            ):
                updates.append(_summarize_note(n))

    updates.sort(key=lambda x: x["cdate"] if x["cdate"] else 0, reverse=True)
    return updates[:limit]


@mcp.tool()
def get_author_updates(
    venue_id: str, since: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Get updates for your own submissions.
    Filters for new reviews, AC comments, and official public comments.
    """
    client = get_client()
    my_id = client.profile.id
    since_ts = _parse_since(since)

    # Get my submissions
    submissions = client.get_all_notes(
        invitation=f"{venue_id}/-/Submission", content={"authorids": my_id}
    )
    forums = [s.forum for s in submissions]
    if not forums:
        return []

    updates = []
    for forum_id in forums:
        notes = client.get_all_notes(forum=forum_id, mintcdate=since_ts)
        for n in notes:
            # Authors care about reviews and comments
            invs = getattr(n, "invitations", [])
            if any(
                "Review" in inv or "Comment" in inv or "Decision" in inv for inv in invs
            ):
                updates.append(_summarize_note(n))

    updates.sort(key=lambda x: x["cdate"] if x["cdate"] else 0, reverse=True)
    return updates[:limit]


@mcp.tool()
def get_discussion_updates(venue_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get the most recent comments/rebuttals in assigned forums (for ACs/Reviewers)."""
    # This is now a simplified version of get_ac_updates/get_reviewer_updates combined
    client = get_client()
    my_id = client.profile.id

    # Get all assigned submissions (AC or Reviewer)
    ac_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )
    rev_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Assignment", tail=my_id
    )

    forums = list(set([e.head for e in ac_assignments + rev_assignments]))
    if not forums:
        return []

    # Get all notes in these forums, sorted by creation date
    all_notes = []
    for forum_id in forums:
        notes = client.get_all_notes(forum=forum_id)
        for n in notes:
            invs = getattr(n, "invitations", [])
            if any(
                "/-/" in inv and ("Comment" in inv or "Rebuttal" in inv) for inv in invs
            ):
                all_notes.append(n)

    # Sort by cdate descending
    all_notes.sort(key=lambda x: x.cdate if x.cdate else 0, reverse=True)

    return [_summarize_note(n) for n in all_notes[:limit]]


@mcp.tool()
def get_submission_feedback(submission_id: str) -> Dict[str, Any]:
    """Get all visible reviews and comments for a submission (useful for Authors)."""
    # Reuse get_submission_details but we can filter or format specifically for authors
    return get_submission_details(submission_id)


@mcp.tool()
def get_reviewer_emails(
    reviewer_ids: List[str], venue_id: Optional[str] = None
) -> Dict[str, str]:
    """
    Get the preferred emails for a list of reviewer profile IDs.

    This tool uses a multi-stage approach:
    1. Batch searches profiles for preferred emails.
    2. If emails are masked (common in v2 API for privacy), it searches recent
       message logs for the venue to find actual delivery addresses.
    """
    client = get_client()
    results = {}

    # 1. Batch lookup profiles
    try:
        # split into batches of 50 to be safe
        for i in range(0, len(reviewer_ids), 50):
            batch = reviewer_ids[i : i + 50]
            profiles = client.search_profiles(ids=batch)
            for p in profiles:
                email = p.content.get("preferredEmail")
                if email and "****" not in email:
                    results[p.id] = email
                else:
                    # Mark as masked or missing for the next stage
                    results[p.id] = email if email else "Not found"
    except Exception:
        # Fallback to individual lookups if search fails
        for rid in reviewer_ids:
            if rid not in results:
                try:
                    p = client.get_profile(rid)
                    email = p.content.get("preferredEmail")
                    results[rid] = email if email else "Not found"
                except Exception:
                    results[rid] = "Error"

    # 2. If venue_id is provided, try to unmask using message logs
    masked_ids = [
        rid for rid, email in results.items() if "****" in email or email == "Not found"
    ]
    if venue_id and masked_ids:
        try:
            # Search for messages in this venue
            messages = client.get_messages(parentGroup=venue_id)
            # Create a map of name/signature to email from messages
            for msg in messages:
                recipient_email = msg.get("content", {}).get("to")
                text = msg.get("content", {}).get("text", "")

                # Try to match the recipient_email to one of our masked IDs
                # Delivery logs in v2 often have the profile ID in the signature or metadata
                # but we can also check if the profile name appears in the text
                for rid in masked_ids:
                    if rid in text or rid == msg.get("signature"):
                        if recipient_email and "****" not in recipient_email:
                            results[rid] = recipient_email
        except Exception:
            pass

    return results


@mcp.tool()
def get_inbox_summary(
    venue_id: str, since: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Get a dense summary of all recent activity in your assigned forums.
    Returns only the most critical information (who, what, paper #, snippet)
    to minimize token usage for automated agents.
    """
    client = get_client()
    my_id = client.profile.id
    since_ts = _parse_since(since)
    import time

    # Get all assigned forums (AC or Reviewer)
    ac_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )
    rev_assignments = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Assignment", tail=my_id
    )
    forums = list(set([e.head for e in ac_assignments + rev_assignments]))
    if not forums:
        return []

    # Pre-fetch submission notes to get paper numbers
    submissions = client.get_notes_by_ids(ids=forums)
    forum_to_number = {s.id: s.number for s in submissions}

    summary_list = []
    for forum_id in forums:
        notes = client.get_all_notes(forum=forum_id, mintcdate=since_ts)
        for n in notes:
            # Exclude the submission note itself
            if n.id == forum_id:
                continue

            # Identify the type of message
            invs = getattr(n, "invitations", [])
            msg_type = "Comment"
            if any("Review" in inv for inv in invs):
                msg_type = "Review"
            elif any("Decision" in inv for inv in invs):
                msg_type = "Decision"
            elif any("Rebuttal" in inv for inv in invs):
                msg_type = "Rebuttal"

            # Get the content snippet
            content = n.content
            snippet = ""
            for field in ["comment", "review", "summary", "decision", "confirmation"]:
                val = (
                    content.get(field, {}).get("value")
                    if isinstance(content.get(field), dict)
                    else content.get(field)
                )
                if val:
                    snippet = (
                        str(val)[:200] + "..." if len(str(val)) > 200 else str(val)
                    )
                    break

            summary_list.append(
                {
                    "paper": forum_to_number.get(forum_id, "Unknown"),
                    "from": n.signatures[0].split("/")[-1],
                    "type": msg_type,
                    "snippet": snippet,
                    "date": (
                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(n.cdate / 1000.0))
                        if n.cdate
                        else "Unknown"
                    ),
                }
            )

    summary_list.sort(key=lambda x: x["date"], reverse=True)
    return summary_list[:limit]


if __name__ == "__main__":
    import sys

    # Check for SSE transport request
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        port = 8000
        if len(sys.argv) > 2:
            try:
                port = int(sys.argv[2])
            except ValueError:
                print(f"Invalid port: {sys.argv[2]}. Using default 8000.")

        import uvicorn

        print(f"Starting OpenReview MCP server on port {port} using SSE...")
        uvicorn.run(mcp.sse_app, host="127.0.0.1", port=port)
    else:
        # Default to stdio
        mcp.run(transport="stdio")
