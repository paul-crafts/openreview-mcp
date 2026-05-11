import os
import time
import re
from functools import wraps
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from openreview.api import OpenReviewClient, Edge
from openreview import OpenReviewException
from dotenv import load_dotenv


# --- Rate Limit Handling ---


def retry_on_429(max_retries: int = 5):
    """Decorator to automatically retry on OpenReview rate limits (429)."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Check for OpenReviewException with 429 status
                    is_429 = False
                    msg = str(e)

                    if isinstance(e, OpenReviewException):
                        error_data = e.args[0] if e.args else {}
                        if (
                            isinstance(error_data, dict)
                            and error_data.get("status") == 429
                        ):
                            is_429 = True
                            msg = error_data.get("message", "")
                    elif "429" in msg or "Too Many Requests" in msg:
                        is_429 = True

                    if is_429:
                        # Try to parse "Please try again in X seconds"
                        match = re.search(r"try again in (\d+) seconds", msg)
                        wait_time = int(match.group(1)) + 1 if match else (30 * (2**i))

                        print(
                            f"Rate limited by OpenReview. Waiting {wait_time}s before retry {i + 1}/{max_retries}..."
                        )
                        time.sleep(wait_time)
                    else:
                        raise e
            return func(*args, **kwargs)

        return wrapper

    return decorator


# --- Client Management ---

# Load environment variables from .env file if it exists
load_dotenv()

# Initialize FastMCP
mcp = FastMCP("OpenReview MCP")

# Global client cache
_client_instance: Optional[OpenReviewClient] = None


@retry_on_429()
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
@retry_on_429()
def get_profile() -> Dict[str, Any]:
    """Get the current user's OpenReview profile information."""
    client = get_client()
    profile = client.profile
    return profile.to_json()


@mcp.tool()
@retry_on_429()
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
@retry_on_429()
def search_venues(query: str) -> List[Dict[str, str]]:
    """Search for a venue by name or ID (e.g., 'ICLR')."""
    # Get all venues and filter by query
    all_venues = list_venues(active_only=False)
    query = query.lower()
    return [v for v in all_venues if query in v["id"].lower()]


@mcp.tool()
@retry_on_429()
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
@retry_on_429()
def get_bidding_info(
    venue_id: str, role: str = "Reviewers", limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    """
    Get papers available for bidding and current bids for the user.

    Args:
        venue_id: The ID of the venue (e.g., 'collas.org/2026/Conference').
        role: The role (default: 'Reviewers', can be 'Area_Chairs').
        limit: Max papers to return (default 50).
        offset: Pagination offset (default 0).
    """
    client = get_client()
    my_id = client.profile.id

    inv_id = f"{venue_id}/{role}/-/Bid"

    # 1. Get bidding invitation for allowed labels
    try:
        invitation = client.get_invitation(inv_id)
        # Extract labels from invitation (v2 structure)
        labels = []
        edge_config = getattr(invitation, "edge", {})
        if edge_config and "label" in edge_config:
            labels = edge_config["label"].get("param", {}).get("enum", [])

        if not labels:
            # Fallback if structure is slightly different or it's v1-like
            content = getattr(invitation, "content", {})
            if "label" in content:
                # Some v2 invitations store params in content
                label_val = content["label"].get("value", {})
                if isinstance(label_val, dict):
                    labels = label_val.get("param", {}).get("enum", [])

        if not labels:
            # Standard default labels if not found in invitation
            labels = ["Very High", "High", "Neutral", "Low", "Very Low", "Conflict"]
    except Exception as e:
        # If invitation is not found, bidding might not be open
        return {"error": f"Bidding invitation {inv_id} not found or not open. {str(e)}"}

    # 2. Get current bids
    current_bids = client.get_all_edges(invitation=inv_id, tail=my_id)
    bid_map = {edge.head: edge.label for edge in current_bids}

    # 3. Get submissions
    # Try common submission invitations in order of likelihood
    sub_inv_patterns = [
        f"{venue_id}/-/Submission",
        f"{venue_id}/-/Submission_Note",
        f"{venue_id}/-/Blind_Submission",
    ]

    submissions = []
    error_msg = ""
    for pattern in sub_inv_patterns:
        try:
            submissions = client.get_notes(
                invitation=pattern, limit=limit, offset=offset
            )
            if submissions or limit == 0:
                break
        except Exception as e:
            error_msg = str(e)
            continue

    if not submissions and limit > 0:
        return {
            "error": f"Could not find any submissions for {venue_id}. Tried patterns: {sub_inv_patterns}. Last error: {error_msg}"
        }

    papers = []
    for s in submissions:
        papers.append(
            {
                "id": s.id,
                "number": s.number,
                "title": s.content.get("title", {}).get("value", "No Title"),
                "current_bid": bid_map.get(s.id, "No Bid"),
            }
        )

    return {
        "venue_id": venue_id,
        "role": role,
        "allowed_bids": labels,
        "papers": papers,
        "total_papers_returned": len(papers),
    }


@mcp.tool()
@retry_on_429()
def get_bid_invitation(venue_id: str, role: str = "Reviewers") -> Dict[str, Any]:
    """
    Get the bidding invitation configuration, including allowed labels.

    Args:
        venue_id: Venue ID.
        role: Role (default: 'Reviewers').
    """
    client = get_client()
    inv_id = f"{venue_id}/{role}/-/Bid"

    try:
        invitation = client.get_invitation(inv_id)
        return invitation.to_json()
    except Exception as e:
        return {"error": f"Bidding invitation {inv_id} not found. {str(e)}"}


@mcp.tool()
@retry_on_429()
def place_bid(
    venue_id: str, submission_id: str, bid: str, role: str = "Reviewers"
) -> Dict[str, Any]:
    """
    Place or update a bid for a specific submission.

    Args:
        venue_id: Venue ID.
        submission_id: Paper ID (Note ID).
        bid: The bid label (e.g., 'Very High', 'Neutral').
        role: Role (default: 'Reviewers').
    """
    client = get_client()
    my_id = client.profile.id
    inv_id = f"{venue_id}/{role}/-/Bid"
    # Fetch invitation to get required readers/writers
    readers = None
    writers = None
    try:
        invitation = client.get_invitation(inv_id)
        edge_config = getattr(invitation, "edge", {})

        def resolve_placeholders(group_list):
            if not isinstance(group_list, list):
                return group_list
            resolved = []
            for g in group_list:
                if g == "${2/tail}":
                    resolved.append(my_id)
                elif g == "${2/head}":
                    resolved.append(submission_id)
                elif isinstance(g, str):
                    resolved.append(g)
            return resolved

        if "readers" in edge_config:
            readers = resolve_placeholders(edge_config["readers"])
        if "writers" in edge_config:
            writers = resolve_placeholders(edge_config["writers"])

        # Fallback if resolve_placeholders didn't find anything or wasn't a list
        if not readers:
            readers = [venue_id, my_id]
        if not writers:
            writers = [venue_id, my_id]

    except Exception:
        # Fallback if invitation not found or structure unexpected
        readers = [venue_id, my_id]
        writers = [venue_id, my_id]

    # Standard v2 Edge construction
    edge = Edge(
        invitation=inv_id,
        head=submission_id,
        tail=my_id,
        label=bid,
        readers=readers,
        writers=writers,
        signatures=[my_id],
    )

    try:
        client.post_edge(edge)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to place bid: {str(e)}",
            "tip": "Ensure you have the correct role for this bidding invitation and that the readers/writers defined in the invitation are accessible to you.",
        }

    return {"status": "success", "bid": bid, "submission_id": submission_id}


@mcp.tool()
@retry_on_429()
def get_bidding_status(venue_id: str, role: str = "Reviewers") -> Dict[str, Any]:
    """Summary of current bids for the venue."""
    client = get_client()
    my_id = client.profile.id
    inv_id = f"{venue_id}/{role}/-/Bid"

    try:
        bids = client.get_all_edges(invitation=inv_id, tail=my_id)
    except Exception:
        return {"error": f"Could not fetch bids for {inv_id}"}

    summary = {}
    high_interest = []

    for b in bids:
        label = b.label
        summary[label] = summary.get(label, 0) + 1
        if label in ["Very High", "High"]:
            high_interest.append(b.head)

    return {
        "venue_id": venue_id,
        "total_bids": len(bids),
        "summary": summary,
        "high_interest_paper_ids": high_interest,
    }



@mcp.tool()
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
        "invitations": getattr(note, "invitations", []),
        "readers": note.readers,
        "content": summary_content,
        "cdate": note.cdate,
    }


@mcp.tool()
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
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
@retry_on_429()
def get_submission_feedback(submission_id: str) -> Dict[str, Any]:
    """Get all visible reviews and comments for a submission (useful for Authors)."""
    # Reuse get_submission_details but we can filter or format specifically for authors
    return get_submission_details(submission_id)


@mcp.tool()
@retry_on_429()
def get_reviewer_emails(
    reviewer_ids: List[str], venue_id: Optional[str] = None
) -> Dict[str, str]:
    """
    Get the preferred emails for a list of reviewer profile IDs.

    This tool uses a multi-stage approach to unmask emails:
    1. Direct profile lookup using get_profiles.
    2. Lookup using Preferred_Email edges (v2 standard).
    3. Fallback to venue-specific Registration notes (v2).
    4. Fallback to Tilde group members.
    5. Fallback to recent message delivery logs.
    """
    client = get_client()
    results = {}

    def extract_email_from_val(val):
        """Helper to extract email from potential v2 nested value."""
        if isinstance(val, dict):
            val = val.get("value")
        
        if isinstance(val, list):
            for e in val:
                if e and isinstance(e, str) and "@" in e and "****" not in e:
                    return e
        elif val and isinstance(val, str) and "@" in val and "****" not in val:
            return val
        return None

    def extract_email(profile):
        """Helper to extract unmasked email from a profile object."""
        # Try different fields, handling both v1 and v2 (nested) formats
        fields = ["preferredEmail", "emailsConfirmed", "emails"]
        for field in fields:
            email = extract_email_from_val(profile.content.get(field))
            if email:
                return email
        return None

    # 1. Batch lookup profiles (using get_profiles)
    try:
        # split into batches of 50 to be safe
        for i in range(0, len(reviewer_ids), 50):
            batch = reviewer_ids[i : i + 50]
            # get_profiles is often more permissive than search_profiles for direct IDs
            profiles = client.get_profiles(id=batch)
            for p in profiles:
                email = extract_email(p)
                if email:
                    results[p.id] = email
                else:
                    results[p.id] = "Masked"
    except Exception:
        # Individual lookup fallback
        for rid in reviewer_ids:
            if rid not in results:
                try:
                    p = client.get_profile(rid)
                    email = extract_email(p)
                    results[rid] = email if email else "Masked"
                except Exception:
                    results[rid] = "Not found"

    # 2. Preferred_Email edges (v2 mechanism)
    # Some venues store preferred emails in edges readable by ACs
    masked_ids = [rid for rid, email in results.items() if email in ["Masked", "Not found"]]
    if venue_id and masked_ids:
        try:
            # Invitation is typically venue_id/-/Preferred_Email
            # We try to get all edges and filter locally
            edges = client.get_all_edges(invitation=f"{venue_id}/-/Preferred_Emails")
            edge_map = {e.head: e.tail for e in edges if e.head in masked_ids}
            for rid, email in edge_map.items():
                if email and "@" in email:
                    results[rid] = email
        except Exception:
            pass

    # 3. If still masked and venue_id is provided, try Registration notes
    masked_ids = [rid for rid, email in results.items() if email in ["Masked", "Not found"]]
    if venue_id and masked_ids:
        try:
            # Common registration invitation in v2
            for rid in masked_ids:
                # Registration notes usually have an 'email' field in content
                # and the reviewer is the signature.
                notes = client.get_all_notes(
                    invitation=f"{venue_id}/Reviewers/-/Registration",
                    signature=rid
                )
                if not notes:
                    # Try general Registration if the above is too specific
                    notes = client.get_all_notes(
                        invitation=f"{venue_id}/-/Registration",
                        signature=rid
                    )
                
                if notes:
                    reg_email = extract_email_from_val(notes[0].content.get("email"))
                    if reg_email:
                        results[rid] = reg_email
        except Exception:
            pass

    # 4. Tilde group members fallback
    # Tilde groups (~Name1) often contain the email in their members list
    masked_ids = [rid for rid, email in results.items() if email in ["Masked", "Not found"]]
    for rid in masked_ids:
        if rid.startswith("~"):
            try:
                group = client.get_group(rid)
                for member in group.members:
                    if "@" in member and "****" not in member:
                        results[rid] = member
                        break
            except Exception:
                pass

    # 5. Final fallback: search message logs
    masked_ids = [rid for rid, email in results.items() if email in ["Masked", "Not found"]]
    if venue_id and masked_ids:
        try:
            # Fetch recent messages. We check first 300 messages.
            for i in range(3):
                messages = client.get_messages(offset=i*100, limit=100)
                if not messages: break
                
                for msg in messages:
                    recipient_email = msg.get("content", {}).get("to")
                    if not recipient_email or "@" not in recipient_email or "****" in recipient_email:
                        continue
                    
                    text = msg.get("content", {}).get("text", "")
                    subject = msg.get("content", {}).get("subject", "")
                    
                    for rid in masked_ids:
                        # Check if message mentions the ID or was sent to it
                        if rid in text or rid in subject or rid == msg.get("signature") or rid == msg.get("to"):
                            results[rid] = recipient_email
        except Exception:
            pass

    return results


@mcp.tool()
@retry_on_429()
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


@mcp.tool()
@retry_on_429()
def get_invitation_status(
    venue_id: str, invitation_suffix: str, role: str = "Reviewers"
) -> List[Dict[str, Any]]:
    """
    Check which participants of a given role have completed a specific invitation.
    Useful for tracking 'Review_Confirmation', 'Official_Comment', etc.
    """
    client = get_client()
    my_id = client.profile.id

    # Get all papers assigned to me as AC
    assignments = client.get_all_edges(
        invitation=f"{venue_id}/Area_Chairs/-/Assignment", tail=my_id
    )
    forums = [e.head for e in assignments]
    if not forums:
        return []

    # Get submission details (titles and numbers)
    submissions = client.get_notes_by_ids(ids=forums)
    forum_to_info = {
        s.id: {
            "title": s.content.get("title", {}).get("value")
            if isinstance(s.content.get("title"), dict)
            else s.content.get("title"),
            "number": s.number,
        }
        for s in submissions
    }

    status_report = []
    for forum_id in forums:
        info = forum_to_info.get(forum_id, {"title": "Unknown", "number": "?"})
        paper_number = info["number"]

        # Get all participants of this role for this paper
        part_edges = client.get_all_edges(
            invitation=f"{venue_id}/{role}/-/Assignment", head=forum_id
        )
        assigned_participants = [e.tail for e in part_edges]

        # Get all notes for this invitation
        # Some invitations are per forum, some are per paper number
        try:
            completed_notes = client.get_all_notes(
                invitation=f"{venue_id}/Submission{paper_number}/-/{invitation_suffix}"
            )
        except Exception:
            try:
                completed_notes = client.get_all_notes(
                    invitation=f"{forum_id}/-/{invitation_suffix}"
                )
            except Exception:
                completed_notes = []

        completed_anonymous_groups = []
        for n in completed_notes:
            completed_anonymous_groups.extend(n.signatures)

        # Resolve anonymous groups to their members
        completed_members = []
        for anon_id in completed_anonymous_groups:
            try:
                # Cache results for performance? No, let's keep it simple for now
                g = client.get_group(anon_id)
                completed_members.extend(g.members)
            except Exception:
                continue

        # Identify missing
        missing = []
        for rid in assigned_participants:
            if rid not in completed_members:
                missing.append(rid)

        status_report.append(
            {
                "paper_number": info["number"],
                "title": info["title"],
                "total_assigned": len(assigned_participants),
                "completed_count": len(assigned_participants) - len(missing),
                "missing_participants": missing,
            }
        )

    return status_report


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
