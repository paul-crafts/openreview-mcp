import os
import time
import re
import json
import html
import string
import datetime
from functools import wraps
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from openreview.api import OpenReviewClient, Edge
from openreview import OpenReviewException
import openreview.tools
import urllib.parse


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


def _get_assigned_submissions(
    client, venue_id: str, role: str = "Area_Chairs"
) -> List[Any]:
    """
    Fetch the Note objects for every submission assigned to the current user under
    a given role (e.g. 'Area_Chairs', 'Reviewers'). This relies only on the
    Assignment-edge mechanic, which is a stable, platform-wide OpenReview convention
    (not something individual venues customize) — safe to keep deterministic.
    """
    my_id = client.profile.id
    assignments = client.get_all_edges(
        invitation=f"{venue_id}/{role}/-/Assignment", tail=my_id
    )
    submission_ids = [edge.head for edge in assignments]
    if not submission_ids:
        return []
    return [client.get_note(sid) for sid in submission_ids]


def _is_withdrawn(note) -> bool:
    """
    Whether a submission Note has been withdrawn. Also a stable, platform-wide
    OpenReview convention (the default conference template marks withdrawals via a
    'Withdrawn_Submission' venue-group suffix on every venue that uses it), unlike
    venue-specific review-form conventions, which this codebase deliberately does not
    try to guess at in tool code (see dump_ac_batch_submissions).
    """
    return "Withdrawn_Submission" in str(
        note.content.get("venueid", {}).get("value", "")
    )


def _sanitize_filename_component(text: str, max_len: int = 50) -> str:
    """Sanitize a string (e.g. a paper title) for safe use as part of a filename."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\s]", "", text)
    return re.sub(r"\s+", "_", cleaned.strip())[:max_len]


@mcp.tool()
@retry_on_429()
def get_ac_submissions(venue_id: str) -> List[Dict[str, Any]]:
    """Get submissions assigned to the current user as an Area Chair."""
    client = get_client()
    submissions = _get_assigned_submissions(client, venue_id, "Area_Chairs")

    return [
        {
            "id": s.id,
            "title": s.content.get("title", {}).get("value", "No Title"),
            "number": s.number,
            "forum": s.forum,
            "is_withdrawn": _is_withdrawn(s),
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
        # Check invitation.edge
        edge_config = getattr(invitation, "edge", {})
        if edge_config and "label" in edge_config:
            labels = edge_config["label"].get("param", {}).get("enum", [])

        # Check invitation.edit
        if not labels:
            edit_config = getattr(invitation, "edit", {})
            if edit_config and "label" in edit_config:
                labels = edit_config["label"].get("param", {}).get("enum", [])

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
        # Check invitation.edge or invitation.edit for v2
        edge_config = getattr(invitation, "edge", {})
        if not edge_config:
            edge_config = getattr(invitation, "edit", {})

        def resolve_placeholders(val):
            # Extract list from potential dict structure in v2
            group_list = []
            if isinstance(val, list):
                group_list = val
            elif isinstance(val, dict):
                group_list = val.get("values", val.get("value", []))

            if not isinstance(group_list, list):
                return [venue_id, my_id]

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
            "details": {
                "invitation": inv_id,
                "readers": readers,
                "writers": writers,
                "signatures": [my_id],
            },
            "tip": "Check if the readers/writers above match what the venue requires. Ensure you are using the correct role (Reviewers vs Area_Chairs).",
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


def _get_submission_contact_info(
    client, venue_id, submission_number, role="Area_Chair"
):
    """
    Helper to discover the correct signature and parent group for a submission.
    In v2, Area Chairs must often sign as their paper-specific anonymized group.
    """
    my_id = client.profile.id

    # 1. Discover the anonymized signature group for this paper
    # e.g., venue/Submission1/Area_Chair_xxxx
    sig = my_id
    try:
        groups = client.get_groups(
            prefix=f"{venue_id}/Submission{submission_number}/{role}_", signatory=my_id
        )
        if groups:
            sig = groups[0].id
    except Exception:
        pass

    # 2. Determine the parent group (usually the role group for that submission)
    # e.g., venue/Submission1/Reviewers
    parent = f"{venue_id}/Submission{submission_number}/Reviewers"

    return sig, parent


@mcp.tool()
@retry_on_429()
def send_reminders(
    venue_id: str,
    subject: str,
    message: str,
    reply_to: Optional[str] = None,
    signature: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Identify reviewers who haven't submitted their assigned reviews and send them a reminder.

    HINT: This tool automatically handles the complex invitation, anonymized signature,
    and parent group requirements for each submission. It is the recommended way for
    Area Chairs to send reminders in v2 venues.

    Args:
        venue_id: The ID of the venue (e.g., 'collas.org/2026/Conference').
        subject: Subject line of the email.
        message: Body text of the email.
        reply_to: Optional email address for replies.
        signature: Optional signature. If not provided, it will be auto-discovered (e.g. anonymized AC group).
        dry_run: If True (default), only previews the messages without sending.
    """
    targets = get_missing_review_reminders_preview(venue_id)
    if not targets:
        return {
            "status": "success",
            "message": "No missing reviews found. No reminders sent.",
        }

    # Group targets by submission to send one message per paper
    from collections import defaultdict

    by_submission = defaultdict(list)
    for t in targets:
        by_submission[t["submission_id"]].append(t["reviewer"])

    results = []
    client = get_client()
    for sub_id, reviewers in by_submission.items():
        # Get the submission number to build the invitation
        try:
            sub = client.get_note(sub_id)
            number = sub.number

            # Auto-discover correct signature and parent group for this submission
            auto_sig, auto_parent = _get_submission_contact_info(
                client, venue_id, number
            )

            # Per-submission message invitation usually looks like this
            inv_pattern = f"{venue_id}/Submission{number}/-/Message"

            res = send_bulk_message(
                venue_id=venue_id,
                recipients=reviewers,
                subject=subject,
                message=message,
                reply_to=reply_to,
                invitation=inv_pattern,
                signature=signature or auto_sig,
                parent_group=auto_parent,
                dry_run=dry_run,
            )
            results.append(
                {
                    "submission_id": sub_id,
                    "submission_number": number,
                    "reviewers": reviewers,
                    "result": res,
                }
            )
        except Exception as e:
            results.append(
                {
                    "submission_id": sub_id,
                    "reviewers": reviewers,
                    "result": {"status": "error", "message": str(e)},
                }
            )

    return {
        "status": "success",
        "dry_run": dry_run,
        "reminders_sent": len(results),
        "details": results,
    }


@mcp.prompt()
def openreview_instructions() -> str:
    """
    Get the official prompting instructions and best practices for the OpenReview MCP.
    Agents should read this to understand role-based workflows and Pro Tips.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompt_path = os.path.join(base_dir, "PROMPTING.md")
        with open(prompt_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading PROMPTING.md: {e}"


@mcp.prompt()
def ac_meta_review_workflow() -> str:
    """
    Get the detailed workflow and JSON schema for synthesizing a structured,
    per-paper meta-review (grouped strengths/weaknesses, disagreements, and a
    verbatim-quote-backed action-item list ranked Critical/Medium/Low) from an
    AC's assigned batch, using dump_ac_batch_submissions, verify_quotes_in_batch,
    and render_meta_review_report. Call this ONLY when actually doing this task —
    for routine AC progress-monitoring, use 'openreview_instructions' instead.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc_path = os.path.join(base_dir, "docs", "AC_META_REVIEW_WORKFLOW.md")
        with open(doc_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading docs/AC_META_REVIEW_WORKFLOW.md: {e}"


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

    HINT: In v2 venues, invitations and signatures are often paper-specific.
    If invitation/signature are not provided, the tool will attempt to use
    the venue's meta-invitation. For paper-specific messages, it is recommended
    to use `send_reminders` or specify the params manually.

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
            "invitation": invitation or f"{venue_id}/-/Edit (default fallback)",
            "signature": signature or f"{client.profile.id} (default)",
            "parent_group": parent_group or venue_id,
            "body_preview": message[:100] + "..." if len(message) > 100 else message,
        }

    # Default to the meta-invitation if none is provided, as required by OpenReview API v2
    if not invitation:
        invitation = f"{venue_id}/-/Edit"

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
        error_msg = str(e)
        if "MissingRequiredError" in error_msg and "invitation" in error_msg:
            return {
                "status": "error",
                "message": f"API error: {error_msg}. The default fallback ({venue_id}/-/Edit) might not exist. Please provide a specific invitation ID.",
            }
        return {"status": "error", "message": error_msg}


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


@mcp.tool()
@retry_on_429()
def export_venue_submissions(
    venue_id: str,
    tab_or_venue_name: Optional[str] = None,
    score_fields: Optional[List[str]] = None,
    output_file: Optional[str] = None,
    output_format: str = "json",
    include_abstract: bool = True,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Export paper titles, abstracts, authors, links, and extracted reviewer scores for a venue or specific venue tab.
    Extracts reviewer score patterns (e.g. overall_recommendation, rating, soundness, presentation, confidence)
    without needing to download full forum HTML pages or load full forum threads into an LLM.

    Args:
        venue_id: The venue group ID (e.g., 'ICML.cc/2026/Conference').
        tab_or_venue_name: Optional tab name or venue string (e.g. 'accept-spotlight', 'ICML 2026 spotlight', 'accept-regular').
            If omitted, exports all submissions associated with venue_id.
        score_fields: List of content fields to extract from Official_Review notes.
            Defaults to ["overall_recommendation", "rating", "soundness", "presentation", "confidence"].
        output_file: Optional filepath to save the exported data (e.g., 'icml_spotlight_papers.json' or '.csv').
        output_format: Output format if saving to file: 'json', 'csv', or 'both' (default: 'json').
        include_abstract: Whether to include abstract text in output records (default: True).
        max_workers: Concurrent thread pool size for review extraction (default: 10).
    """
    client = get_client()

    if score_fields is None:
        score_fields = [
            "overall_recommendation",
            "rating",
            "soundness",
            "presentation",
            "confidence",
        ]

    # Resolve venue string if tab_or_venue_name is given
    venue_query = None
    if tab_or_venue_name:
        try:
            domain = client.get_group(venue_id)
            decision_map = (
                domain.content.get("decision_heading_map", {}).get("value", {})
            )
            if tab_or_venue_name in decision_map:
                venue_query = tab_or_venue_name
            else:
                norm_tab = (
                    tab_or_venue_name.lower()
                    .replace(" ", "-")
                    .replace("_", "-")
                    .replace("(", "")
                    .replace(")", "")
                )
                for v_str, heading in decision_map.items():
                    norm_h = (
                        heading.lower()
                        .replace(" ", "-")
                        .replace("_", "-")
                        .replace("(", "")
                        .replace(")", "")
                    )
                    norm_v = (
                        v_str.lower()
                        .replace(" ", "-")
                        .replace("_", "-")
                        .replace("(", "")
                        .replace(")", "")
                    )
                    if (
                        norm_tab == norm_h
                        or norm_tab == norm_v
                        or norm_tab in norm_h
                        or norm_tab in norm_v
                    ):
                        venue_query = v_str
                        break
        except Exception:
            pass

        if not venue_query:
            venue_query = tab_or_venue_name

    # Fetch submission notes
    if venue_query:
        notes = client.get_all_notes(content={"venue": venue_query})
    else:
        notes = client.get_all_notes(content={"venueid": venue_id})

    if not notes:
        return {
            "status": "success",
            "message": f"No submissions found for venue '{venue_id}' matching query '{venue_query or tab_or_venue_name}'.",
            "count": 0,
            "data": [],
        }

    valid_notes = notes

    def extract_val(field_val):
        if isinstance(field_val, dict):
            return field_val.get("value")
        return field_val

    def parse_numeric(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            m = re.match(r"^(\d+(?:\.\d+)?)", val.strip())
            if m:
                num_str = m.group(1)
                return int(num_str) if num_str.isdigit() else float(num_str)
        return val

    # Helper function to fetch reviews for a submission
    def fetch_paper_data(note):
        number = note.number
        title = extract_val(note.content.get("title"))
        authors = extract_val(note.content.get("authors"))
        abstract = extract_val(note.content.get("abstract"))
        venue_str = extract_val(note.content.get("venue"))

        item: Dict[str, Any] = {
            "number": number,
            "id": note.id,
            "title": title,
            "authors": authors if isinstance(authors, list) else [authors]
            if authors
            else [],
            "venue": venue_str,
            "pdf_url": f"https://openreview.net/pdf?id={note.id}",
            "forum_url": f"https://openreview.net/forum?id={note.id}",
            "reviews_count": 0,
            "reviews": [],
        }

        if include_abstract:
            item["abstract"] = abstract

        # Attempt to fetch per-submission Official_Review notes
        inv_id = f"{venue_id}/Submission{number}/-/Official_Review"
        try:
            rev_notes = client.get_notes(invitation=inv_id)
        except Exception:
            rev_notes = []

        if rev_notes:
            item["reviews_count"] = len(rev_notes)
            for idx, r in enumerate(rev_notes):
                sig = r.signatures[0].split("/")[-1] if r.signatures else f"Reviewer_{idx+1}"
                r_content = r.content
                scores = {"reviewer": sig}
                for f in score_fields:
                    if f in r_content:
                        raw_v = extract_val(r_content[f])
                        scores[f] = raw_v
                item["reviews"].append(scores)

        # Calculate score summaries
        for f in score_fields:
            vals = []
            for r in item["reviews"]:
                if f in r and r[f] is not None:
                    parsed = parse_numeric(r[f])
                    if isinstance(parsed, (int, float)):
                        vals.append(parsed)
            if vals:
                item[f"{f}_scores"] = vals
                item[f"avg_{f}"] = round(sum(vals) / len(vals), 2)
                item[f"min_{f}"] = min(vals)
                item[f"max_{f}"] = max(vals)

        return item

    import csv
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        records = list(executor.map(fetch_paper_data, valid_notes))

    # Sort records by paper number
    records.sort(key=lambda x: x.get("number") or 0)

    saved_files = []
    if output_file:
        base_path, ext = os.path.splitext(output_file)
        fmt = output_format.lower()

        # Write JSON if requested or matching extension
        if fmt in ["json", "both"] or ext.lower() == ".json":
            json_path = output_file if ext.lower() == ".json" else f"{base_path}.json"
            os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            saved_files.append(os.path.abspath(json_path))

        # Write CSV if requested or matching extension
        if fmt in ["csv", "both"] or ext.lower() == ".csv":
            csv_path = output_file if ext.lower() == ".csv" else f"{base_path}.csv"
            os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
            if records:
                fieldnames = ["number", "id", "title", "authors", "venue", "pdf_url", "forum_url", "reviews_count"]
                if include_abstract:
                    fieldnames.append("abstract")

                # Add score summary fieldnames
                for f in score_fields:
                    fieldnames.extend([f"avg_{f}", f"{f}_scores"])

                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    for r in records:
                        row = dict(r)
                        if isinstance(row.get("authors"), list):
                            row["authors"] = "; ".join(row["authors"])
                        for f in score_fields:
                            scores_key = f"{f}_scores"
                            if isinstance(row.get(scores_key), list):
                                row[scores_key] = json.dumps(row[scores_key])
                        writer.writerow(row)
            saved_files.append(os.path.abspath(csv_path))

    return {
        "status": "completed",
        "venue_id": venue_id,
        "query": venue_query or tab_or_venue_name,
        "total_papers": len(records),
        "saved_files": saved_files,
        "sample": records[:2] if records else [],
    }



@mcp.tool()
@retry_on_429()
def dump_ac_batch_submissions(
    venue_id: str,
    output_dir: Optional[str] = None,
    delay: float = 1.0,
) -> Dict[str, Any]:
    """
    Batch-fetch every paper assigned to the user as an Area Chair and persist the
    full raw forum data (submission note + every reply, verbatim, exactly the shape
    get_submission_details returns) to one JSON file per paper. Use this instead of
    re-typing review text through a Write tool — that wastes tokens and this tool's
    response never contains review content, only a compact manifest.

    Deliberately does NOT try to identify which replies are official reviews, or
    parse rating/confidence/free-text fields out of review content — those are
    venue-customizable review-form details that vary across conferences and tracks,
    and guessing at them with a fixed heuristic is exactly the kind of assumption
    that silently breaks on a venue with a different template. Call the
    'ac_meta_review_workflow' prompt for the full guide on how an LLM should read
    these dumps and do that identification/extraction itself.

    Withdrawn submissions are excluded from dumping (no forum fetch is even made for
    them) but are still listed in 'excluded_withdrawn', including their raw venueid
    value, so the classification stays double-checkable rather than opaque.

    Args:
        venue_id: The ID of the venue (e.g., 'NeurIPS.cc/2026/Conference').
        output_dir: Directory where per-submission .json files will be written.
            Defaults to 'downloads/<sanitized_venue_id>/ac_review_dumps'.
        delay: Proactive delay in seconds between consecutive per-submission forum
            fetches, to avoid rate limits (default: 1.0s). Mirrors download_batch_pdfs.
    """
    client = get_client()
    all_submissions = _get_assigned_submissions(client, venue_id, "Area_Chairs")

    if not all_submissions:
        return {
            "status": "success",
            "message": f"No Area Chair assignments found for venue '{venue_id}'.",
            "output_dir": None,
            "dumped": [],
            "excluded_withdrawn": [],
            "failed": [],
        }

    active_submissions = []
    excluded_withdrawn = []
    for s in all_submissions:
        if _is_withdrawn(s):
            excluded_withdrawn.append(
                {
                    "id": s.id,
                    "number": s.number,
                    "title": s.content.get("title", {}).get("value", "No Title"),
                    "venueid": s.content.get("venueid", {}).get("value", ""),
                }
            )
        else:
            active_submissions.append(s)

    if not output_dir:
        safe_venue = re.sub(r"[^a-zA-Z0-9_\-]", "_", venue_id)
        output_dir = os.path.join("downloads", safe_venue, "ac_review_dumps")
    os.makedirs(output_dir, exist_ok=True)

    dumped = []
    failed = []

    for i, s in enumerate(active_submissions):
        title = s.content.get("title", {}).get("value", "No Title")
        number = s.number

        if i > 0 and delay > 0:
            time.sleep(delay)

        try:
            replies = client.get_all_notes(forum=s.id)

            # Purely descriptive tally over each reply's invitation suffixes — not a
            # classification of which replies "are" reviews, just a fast hint of
            # what's present so the LLM doesn't have to open the file to orient.
            tally: Dict[str, int] = {}
            for r in replies:
                for inv in getattr(r, "invitations", None) or []:
                    suffix = inv.rsplit("/", 1)[-1]
                    tally[suffix] = tally.get(suffix, 0) + 1

            clean_title = _sanitize_filename_component(title)
            ref = f"paper_{number}_{clean_title}"
            file_path = os.path.join(output_dir, f"{ref}.json")

            with open(file_path, "w") as f:
                json.dump(
                    {
                        "submission": s.to_json(),
                        "replies": [r.to_json() for r in replies],
                    },
                    f,
                    indent=2,
                )

            dumped.append(
                {
                    "ref": ref,
                    "id": s.id,
                    "number": number,
                    "title": title,
                    "file_path": os.path.abspath(file_path),
                    "note_count": len(replies),
                    "reply_invitation_tally": tally,
                }
            )
        except Exception as e:
            failed.append(
                {"id": s.id, "number": number, "title": title, "error": str(e)}
            )

    return {
        "status": "completed",
        "message": (
            f"Dumped {len(dumped)} submissions "
            f"({len(excluded_withdrawn)} withdrawn, excluded). Failed: {len(failed)}."
        ),
        "output_dir": os.path.abspath(output_dir),
        "dumped": dumped,
        "excluded_withdrawn": excluded_withdrawn,
        "failed": failed,
    }


def _load_searchable_text(file_path: str) -> str:
    """
    Load a file's content as one searchable text corpus. For '.json' files (the
    dump_ac_batch_submissions output), parses the JSON and flattens every string
    leaf value (recursively) into the corpus, joined by newlines — this correctly
    matches quotes against the *decoded* text (e.g. a quote containing a literal
    newline or double-quote character, which json.dump would otherwise escape),
    rather than against the raw escaped JSON bytes. This is a fully generic
    JSON-flattening operation with no assumptions about field meaning.
    """
    if file_path.endswith(".json"):
        with open(file_path, "r") as f:
            data = json.load(f)

        strings: List[str] = []

        def _walk(obj):
            if isinstance(obj, str):
                strings.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(v)

        _walk(data)
        return "\n".join(strings)

    with open(file_path, "r") as f:
        return f.read()


@mcp.tool()
def verify_quotes_in_batch(
    quotes: List[Dict[str, str]], output_dir: str
) -> Dict[str, Any]:
    """
    Verify that each quote is a literal (grep -F equivalent) substring of the raw
    dump file it claims to come from. Use this to fact-check every attributed quote
    in a meta-review synthesis — including any external citation named in an Action
    Item (paper titles, table/figure/proposition numbers), as long as that text also
    appears verbatim in the dump — before calling render_meta_review_report.

    This tool touches no network and requires no OpenReview credentials — it is a
    pure local file-search step.

    Args:
        quotes: List of {"ref": str, "quote": str} pairs. 'ref' must match a 'ref'
            value from dump_ac_batch_submissions's manifest (the filename stem,
            with or without the '.json' extension) and resolves to
            '<output_dir>/<ref>.json'. 'quote' is checked as an exact literal
            substring — no normalization, no whitespace collapsing, no regex — so a
            paraphrase that isn't truly verbatim will correctly fail.
        output_dir: The output_dir returned by dump_ac_batch_submissions for this batch.

    Returns:
        Totals, plus full detail ONLY for failures, so an all-passing run over a
        large batch stays small regardless of how many quotes were checked.
    """
    text_cache: Dict[str, Optional[str]] = {}
    failures = []
    passed = 0

    for index, item in enumerate(quotes):
        ref = item.get("ref", "")
        quote = item.get("quote", "")
        file_name = ref if ref.endswith(".json") else f"{ref}.json"
        file_path = os.path.join(output_dir, file_name)

        if file_path not in text_cache:
            try:
                text_cache[file_path] = _load_searchable_text(file_path)
            except Exception:
                text_cache[file_path] = None

        text = text_cache[file_path]
        if text is None:
            failures.append(
                {
                    "index": index,
                    "ref": ref,
                    "quote": quote,
                    "reason": "file_not_found",
                    "file_path": file_path,
                }
            )
        elif quote in text:
            passed += 1
        else:
            failures.append(
                {
                    "index": index,
                    "ref": ref,
                    "quote": quote,
                    "reason": "quote_not_found",
                    "file_path": file_path,
                }
            )

    return {
        "total_checked": len(quotes),
        "passed": passed,
        "failed": len(failures),
        "all_passed": len(failures) == 0,
        "failures": failures,
    }


# --- Meta-Review Rendering ---
#
# Every function below is a pure rendering step over data the caller has already
# assembled (and ideally verified with verify_quotes_in_batch) -- no OpenReview
# API calls, no venue-specific interpretation. html.escape() is applied centrally,
# inside these shared helpers, to every reviewer/author-authored string (never at
# call sites) so it is structurally impossible to forget escaping on one path.


def _md_code_span(text: str) -> str:
    """Wrap text in Markdown inline code, using a longer backtick fence if the
    text itself contains a run of backticks that would otherwise end it early."""
    text = str(text)
    max_run = 0
    current = 0
    for ch in text:
        if ch == "`":
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    fence = "`" * (max_run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _item_who(item: Dict[str, Any]) -> List[str]:
    """Every leaf item (strength/weakness/disagreement/action-item) uses 'who'
    (a list); isolated points use 'reviewer' (a single string) since they are by
    definition ungrouped. Normalize both to a list for shared rendering code."""
    if item.get("who"):
        return list(item["who"])
    if item.get("reviewer"):
        return [item["reviewer"]]
    return []


def _md_item_block(item: Dict[str, Any]) -> str:
    claim = item.get("claim", "")
    who_str = f" ({', '.join(_item_who(item))})" if _item_who(item) else ""
    out = [f"**{claim}**{who_str}"]
    if item.get("note"):
        out.append(f"*{item['note']}*")
    for q in item.get("quotes") or []:
        out.append(f"- {q.get('reviewer', '')}: {_md_code_span(q.get('text', ''))}")
    return "\n".join(out)


def _md_isolated_inline(item: Dict[str, Any]) -> str:
    quote_bits = "; ".join(
        _md_code_span(q.get("text", "")) for q in (item.get("quotes") or [])
    )
    claim = item.get("claim", "")
    return f"{claim} — {quote_bits}" if quote_bits else claim


def _md_action_inline(item: Dict[str, Any]) -> str:
    who = _item_who(item)
    refs = f" *[{', '.join(who)}]*" if who else ""
    note = f" ({item['note']})" if item.get("note") else ""
    return f"{item.get('claim', '')}{note}{refs}"


def _build_markdown(synthesis: Dict[str, Any]) -> str:
    """Build the single source-of-truth Markdown report. Heading structure
    ('## Paper <ref>: ...') is matched by the HTML template's client-side JS to
    split this same text into per-paper chunks for the Copy/View-as-Markdown
    buttons -- keep the two in sync if this structure ever changes."""
    venue_title = synthesis.get("venue_display_name") or synthesis.get(
        "venue_id", "Untitled Venue"
    )
    generated_at = synthesis.get("generated_at") or (
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    lines = [
        f"# {venue_title} — Meta-Review Synthesis",
        "",
        f"Generated {generated_at}.",
        "",
    ]

    for paper in synthesis.get("papers", []):
        lines.append(f"## Paper {paper.get('ref', '')}: {paper.get('title', '')}")
        lines.append("")

        lines.append("### Strengths")
        lines.append("")
        strengths = paper.get("strengths") or []
        if strengths:
            for item in strengths:
                lines.append(_md_item_block(item))
                lines.append("")
        else:
            lines.append("*(none noted)*")
            lines.append("")

        lines.append("### Weaknesses")
        lines.append("")
        weaknesses = paper.get("weaknesses") or []
        if weaknesses:
            for item in weaknesses:
                lines.append(_md_item_block(item))
                lines.append("")
        else:
            lines.append("*(none noted)*")
            lines.append("")
        if paper.get("minor"):
            lines.append("#### Minor")
            lines.append("")
            for item in paper["minor"]:
                lines.append(_md_item_block(item))
                lines.append("")

        lines.append("### Disagreements / Conflicts")
        lines.append("")
        disagreements = paper.get("disagreements") or []
        if disagreements:
            for item in disagreements:
                lines.append(_md_item_block(item))
                lines.append("")
        else:
            lines.append("No explicit disagreements were identified among reviewers.")
            lines.append("")

        lines.append("### Isolated points")
        lines.append("")
        isolated = paper.get("isolatedPoints") or []
        if isolated:
            by_reviewer: Dict[str, List[Dict[str, Any]]] = {}
            for item in isolated:
                by_reviewer.setdefault(item.get("reviewer", "Unknown"), []).append(item)
            for reviewer, items in by_reviewer.items():
                lines.append(f"- **{reviewer}:**")
                for item in items:
                    lines.append(f"  - {_md_isolated_inline(item)}")
            lines.append("")
        else:
            lines.append("*(none noted)*")
            lines.append("")

        lines.append("### Action Items for Authors")
        lines.append("")
        action_items = paper.get("actionItems") or {}
        for tier_key, tier_label in (
            ("critical", "Critical"),
            ("medium", "Medium"),
            ("low", "Low"),
        ):
            tier_items = action_items.get(tier_key) or []
            lines.append(f"**{tier_label}**")
            if tier_items:
                for i, item in enumerate(tier_items, start=1):
                    lines.append(f"{i}. {_md_action_inline(item)}")
            else:
                lines.append("*(none)*")
            lines.append("")

        lines.append("---")
        lines.append("")

    excluded = synthesis.get("excludedWithdrawn") or []
    if excluded:
        lines.append("## Excluded (Withdrawn) Submissions")
        lines.append("")
        for e in excluded:
            lines.append(
                f"- #{e.get('number', '?')} {e.get('title', '')} (`{e.get('id', '')}`)"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Verification Log")
    lines.append("")
    vlog = synthesis.get("verificationLog") or {}
    lines.append(
        f"Total quotes checked: {vlog.get('total_checked', 0)}. "
        f"Passed: {vlog.get('passed', 0)}. Failed: {vlog.get('failed', 0)}."
    )
    lines.append("")
    for f in vlog.get("failures") or []:
        lines.append(
            f"- **FAILED** `{f.get('ref', '')}` — "
            f"{_md_code_span(f.get('quote', ''))} ({f.get('reason', '')})"
        )
    lines.append("")

    return "\n".join(lines)


def _html_quotes(quotes: Optional[List[Dict[str, str]]]) -> str:
    items = []
    for q in quotes or []:
        reviewer = html.escape(str(q.get("reviewer", "")), quote=True)
        text = html.escape(str(q.get("text", "")), quote=True)
        items.append(
            f'<li><span class="rid">{reviewer}</span>'
            f'<span class="quote-text">&quot;{text}&quot;</span></li>'
        )
    return "\n".join(items)


def _html_point(item: Dict[str, Any], css_class: str) -> str:
    claim = html.escape(str(item.get("claim", "")), quote=True)
    who_html = ", ".join(html.escape(str(w), quote=True) for w in _item_who(item))
    who_span = f' <span class="point-who">({who_html})</span>' if who_html else ""
    note_html = (
        f'<p class="point-note">{html.escape(str(item["note"]), quote=True)}</p>'
        if item.get("note")
        else ""
    )
    return (
        f'<div class="point {css_class}">'
        f'<p class="point-claim">{claim}{who_span}</p>'
        f"{note_html}"
        f'<ul class="quotes">{_html_quotes(item.get("quotes"))}</ul>'
        f"</div>"
    )


def _html_group(
    items: List[Dict[str, Any]],
    css_class: str,
    heading_text: str,
    heading_class: str,
    empty_note: str,
) -> str:
    heading = f'<h3 class="section-heading {heading_class}">{heading_text}</h3>'
    if not items:
        return (
            heading
            + f'<p class="no-conflict-note">{html.escape(empty_note, quote=True)}</p>'
        )
    return heading + "\n".join(_html_point(it, css_class) for it in items)


def _html_isolated_points(items: List[Dict[str, Any]]) -> str:
    heading = '<h3 class="section-heading h-isolated">Isolated points</h3>'
    if not items:
        return heading + '<p class="no-conflict-note">None noted.</p>'
    by_reviewer: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        by_reviewer.setdefault(item.get("reviewer", "Unknown"), []).append(item)
    groups = []
    for reviewer, group_items in by_reviewer.items():
        rid = html.escape(str(reviewer), quote=True)
        lis = []
        for it in group_items:
            claim = html.escape(str(it.get("claim", "")), quote=True)
            quote_bits = "; ".join(
                f"&quot;{html.escape(str(q.get('text', '')), quote=True)}&quot;"
                for q in (it.get("quotes") or [])
            )
            suffix = f" — {quote_bits}" if quote_bits else ""
            lis.append(f"<li>{claim}{suffix}</li>")
        groups.append(
            f'<div class="isolated-group"><h5>{rid}</h5><ul>{"".join(lis)}</ul></div>'
        )
    return heading + "\n".join(groups)


def _html_action_tier(
    items: List[Dict[str, Any]], tier_key: str, tier_label: str
) -> str:
    lis = []
    for item in items:
        text = html.escape(str(item.get("claim", "")), quote=True)
        refs_html = "".join(
            f'<span class="rid">{html.escape(str(w), quote=True)}</span>'
            for w in _item_who(item)
        )
        note_html = (
            f" — {html.escape(str(item['note']), quote=True)}"
            if item.get("note")
            else ""
        )
        lis.append(
            f'<li><p class="action-text">{text}</p>'
            f'<p class="action-refs">{refs_html}{note_html}</p></li>'
        )
    body = "".join(lis) if lis else "<li><em>None.</em></li>"
    return (
        f'<div class="action-tier tier-{tier_key}"><span class="tier-label">{tier_label}</span>'
        f'<ol class="action-list">{body}</ol></div>'
    )


def _html_paper_section(paper: Dict[str, Any]) -> str:
    ref = html.escape(str(paper.get("ref", "")), quote=True)
    number = html.escape(str(paper.get("number", "")), quote=True)
    title = html.escape(str(paper.get("title", "")), quote=True)

    reviewers = paper.get("reviewers")
    if not reviewers:
        seen: List[str] = []
        for group_key in ("strengths", "weaknesses", "minor", "disagreements"):
            for item in paper.get(group_key) or []:
                for q in item.get("quotes") or []:
                    r = q.get("reviewer")
                    if r and r not in seen:
                        seen.append(r)
        for item in paper.get("isolatedPoints") or []:
            r = item.get("reviewer")
            if r and r not in seen:
                seen.append(r)
        reviewers = [{"id": r} for r in seen]

    badges = "".join(
        '<span class="rbadge"><b>{}</b>{}</span>'.format(
            html.escape(str(r.get("id", "")), quote=True),
            f" · rating {html.escape(str(r['rating']), quote=True)}"
            if r.get("rating") is not None
            else "",
        )
        for r in reviewers
    )

    action_items = paper.get("actionItems") or {}
    minor_html = ""
    if paper.get("minor"):
        minor_body = "\n".join(_html_point(it, "weak") for it in paper["minor"])
        minor_html = f'<div class="minor-block"><p class="minor-label">Minor</p>{minor_body}</div>'

    return (
        f'<section class="submission" id="{ref}">'
        f'<h2><span class="sub-id">#{number}</span>{title}</h2>'
        f'<div class="reviewer-badges">{badges}</div>'
        f'<div class="md-actions">'
        f'<button class="md-btn" data-mdkey="{ref}" data-action="copy">📋 Copy as Markdown</button>'
        f'<button class="md-btn" data-mdkey="{ref}" data-action="view">👁 View Markdown</button>'
        f"</div>"
        f'<pre class="md-raw" id="md-raw-{ref}" hidden></pre>'
        + _html_group(
            paper.get("strengths") or [],
            "strength",
            "Strengths",
            "h-strengths",
            "No strengths noted.",
        )
        + _html_group(
            paper.get("weaknesses") or [],
            "weak",
            "Weaknesses",
            "h-weaknesses",
            "No weaknesses noted.",
        )
        + minor_html
        + _html_group(
            paper.get("disagreements") or [],
            "conflict",
            "Disagreements / Conflicts",
            "h-conflicts",
            "No explicit disagreements were identified among reviewers.",
        )
        + _html_isolated_points(paper.get("isolatedPoints") or [])
        + '<h3 class="section-heading h-actions">Action Items for Authors</h3>'
        + _html_action_tier(action_items.get("critical") or [], "critical", "Critical")
        + _html_action_tier(action_items.get("medium") or [], "medium", "Medium")
        + _html_action_tier(action_items.get("low") or [], "low", "Low")
        + "</section>"
    )


def _html_sidebar_nav(papers: List[Dict[str, Any]]) -> str:
    items = []
    for p in papers:
        ref = html.escape(str(p.get("ref", "")), quote=True)
        title = html.escape(str(p.get("title", "")), quote=True)
        number = html.escape(str(p.get("number", "")), quote=True)
        items.append(
            f'<li><a class="navlink" href="#{ref}" data-target="{ref}">'
            f"<span>#{number} {title}</span></a></li>"
        )
    return "\n".join(items)


def _html_excluded_withdrawn(excluded: List[Dict[str, Any]]) -> str:
    if not excluded:
        return ""
    lis = "".join(
        f"<li>#{html.escape(str(e.get('number', '')), quote=True)} "
        f"{html.escape(str(e.get('title', '')), quote=True)} "
        f"(<code>{html.escape(str(e.get('id', '')), quote=True)}</code>)</li>"
        for e in excluded
    )
    return (
        '<section class="submission" id="excluded-withdrawn">'
        "<h2>Excluded (Withdrawn) Submissions</h2>"
        f"<ul>{lis}</ul>"
        "</section>"
    )


def _html_verification_log(vlog: Dict[str, Any]) -> str:
    total = vlog.get("total_checked", 0)
    passed = vlog.get("passed", 0)
    failed = vlog.get("failed", 0)
    failures = vlog.get("failures") or []
    status_class = "pass" if failed == 0 else "fail"
    status_text = f"{passed}/{total} PASS" if failed == 0 else f"{failed} FAILED"
    failure_html = ""
    if failures:
        items = "".join(
            f"<li><code>{html.escape(str(f.get('ref', '')), quote=True)}</code>: "
            f"&quot;{html.escape(str(f.get('quote', '')), quote=True)}&quot; "
            f"({html.escape(str(f.get('reason', '')), quote=True)})</li>"
            for f in failures
        )
        failure_html = f"<ul>{items}</ul>"
    return (
        '<details class="vlog" open>'
        f'<summary><span>Quote verification</span><span class="{status_class}">{status_text}</span></summary>'
        f"<p>Total checked: {total}. Passed: {passed}. Failed: {failed}.</p>"
        f"{failure_html}"
        "</details>"
    )


def _escape_for_inline_script(text: str) -> str:
    """Make text safe to embed inside a <script type="text/plain"> block by
    splitting any literal '</script' sequence (case-insensitive) so it can never
    prematurely close the tag. Do not otherwise alter the text -- it must stay
    byte-identical to the standalone Markdown file for 'Copy as Markdown' parity."""
    return re.sub(r"(?i)</script", "<\\/script", text)


def _build_html(synthesis: Dict[str, Any], raw_markdown: str) -> str:
    template_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "templates",
        "meta_review_report.html.tmpl",
    )
    with open(template_path, "r") as f:
        template_text = f.read()

    venue_title = synthesis.get("venue_display_name") or synthesis.get(
        "venue_id", "Untitled Venue"
    )
    generated_at = synthesis.get("generated_at") or (
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    papers = synthesis.get("papers", [])

    return string.Template(template_text).substitute(
        venue_title=html.escape(str(venue_title), quote=True),
        generated_at=html.escape(str(generated_at), quote=True),
        sidebar_nav_html=_html_sidebar_nav(papers),
        papers_html="\n".join(_html_paper_section(p) for p in papers),
        excluded_withdrawn_html=_html_excluded_withdrawn(
            synthesis.get("excludedWithdrawn") or []
        ),
        verification_log_html=_html_verification_log(
            synthesis.get("verificationLog") or {}
        ),
        raw_markdown_escaped=_escape_for_inline_script(raw_markdown),
    )


@mcp.tool()
def render_meta_review_report(
    synthesis: Dict[str, Any],
    output_dir: str,
    filename_stem: str = "meta_review_report",
) -> Dict[str, Any]:
    """
    Render a per-paper meta-review synthesis into both a Markdown report and a
    single self-contained HTML report (sidebar nav with scroll-spy, color-coded
    strength/weakness/disagreement/action-item sections, collapsible verification
    log, and copy/view/download-as-markdown buttons), using the bundled template at
    openreview_mcp/templates/meta_review_report.html.tmpl.

    Call the 'ac_meta_review_workflow' prompt for the full synthesis JSON schema.
    This tool performs no network calls and requires no OpenReview credentials --
    it only renders data the caller has already assembled and (ideally) verified
    with verify_quotes_in_batch.

    Args:
        synthesis: The synthesis payload (see the JSON schema documented in the
            'ac_meta_review_workflow' prompt).
        output_dir: Directory to write '<filename_stem>.md' and
            '<filename_stem>.html' into. Created if missing.
        filename_stem: Base filename (without extension) for both outputs.

    Returns:
        Paths and counts only -- never the rendered content inline.
    """
    os.makedirs(output_dir, exist_ok=True)

    markdown_text = _build_markdown(synthesis)
    html_text = _build_html(synthesis, markdown_text)

    md_path = os.path.join(output_dir, f"{filename_stem}.md")
    html_path = os.path.join(output_dir, f"{filename_stem}.html")

    with open(md_path, "w") as f:
        f.write(markdown_text)
    with open(html_path, "w") as f:
        f.write(html_text)

    papers = synthesis.get("papers", [])
    counts = {
        "strengths": sum(len(p.get("strengths") or []) for p in papers),
        "weaknesses": sum(len(p.get("weaknesses") or []) for p in papers),
        "minor": sum(len(p.get("minor") or []) for p in papers),
        "disagreements": sum(len(p.get("disagreements") or []) for p in papers),
        "isolated_points": sum(len(p.get("isolatedPoints") or []) for p in papers),
        "action_items": {
            tier: sum(len((p.get("actionItems") or {}).get(tier) or []) for p in papers)
            for tier in ("critical", "medium", "low")
        },
    }

    return {
        "status": "success",
        "output_dir": os.path.abspath(output_dir),
        "markdown_path": os.path.abspath(md_path),
        "html_path": os.path.abspath(html_path),
        "paper_count": len(papers),
        "excluded_withdrawn_count": len(synthesis.get("excludedWithdrawn") or []),
        "counts": counts,
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
    results = []
    for s in submissions:
        data = s.to_json()
        data["is_withdrawn"] = "Withdrawn_Submission" in str(
            s.content.get("venueid", {}).get("value", "")
        )
        results.append(data)
    return results


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
def get_venue_invitation_types(venue_id: str) -> List[str]:
    """
    Get all unique invitation suffixes (types) available for a venue.
    This helps discover the exact names of tasks (e.g., 'Review', 'Official_Comment', 'Rebuttal_Acknowledgement').
    """
    client = get_client()
    invitations = client.get_all_invitations(prefix=f"{venue_id}/")

    suffixes = set()
    for inv in invitations:
        parts = inv.id.split("/-/")
        if len(parts) > 1:
            suffixes.add(parts[-1])
        else:
            suffixes.add(inv.id.split("/")[-1])

    return sorted(list(suffixes))


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
                "Comment" in inv
                or "Rebuttal" in inv
                or "Decision" in inv
                or "Acknowledgement" in inv
                or "Acknowledgment" in inv
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
                "/-/" in inv
                and (
                    "Comment" in inv
                    or "Rebuttal" in inv
                    or "Acknowledgement" in inv
                    or "Acknowledgment" in inv
                )
                for inv in invs
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
    masked_ids = [
        rid for rid, email in results.items() if email in ["Masked", "Not found"]
    ]
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
    masked_ids = [
        rid for rid, email in results.items() if email in ["Masked", "Not found"]
    ]
    if venue_id and masked_ids:
        try:
            # Common registration invitation in v2
            for rid in masked_ids:
                # Registration notes usually have an 'email' field in content
                # and the reviewer is the signature.
                notes = client.get_all_notes(
                    invitation=f"{venue_id}/Reviewers/-/Registration", signature=rid
                )
                if not notes:
                    # Try general Registration if the above is too specific
                    notes = client.get_all_notes(
                        invitation=f"{venue_id}/-/Registration", signature=rid
                    )

                if notes:
                    reg_email = extract_email_from_val(notes[0].content.get("email"))
                    if reg_email:
                        results[rid] = reg_email
        except Exception:
            pass

    # 4. Tilde group members fallback
    # Tilde groups (~Name1) often contain the email in their members list
    masked_ids = [
        rid for rid, email in results.items() if email in ["Masked", "Not found"]
    ]
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
    masked_ids = [
        rid for rid, email in results.items() if email in ["Masked", "Not found"]
    ]
    if venue_id and masked_ids:
        try:
            # Fetch recent messages. We check first 300 messages.
            for i in range(3):
                messages = client.get_messages(offset=i * 100, limit=100)
                if not messages:
                    break

                for msg in messages:
                    recipient_email = msg.get("content", {}).get("to")
                    if (
                        not recipient_email
                        or "@" not in recipient_email
                        or "****" in recipient_email
                    ):
                        continue

                    text = msg.get("content", {}).get("text", "")
                    subject = msg.get("content", {}).get("subject", "")

                    for rid in masked_ids:
                        # Check if message mentions the ID or was sent to it
                        if (
                            rid in text
                            or rid in subject
                            or rid == msg.get("signature")
                            or rid == msg.get("to")
                        ):
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

        # Get all participants of this role for this paper
        part_edges = client.get_all_edges(
            invitation=f"{venue_id}/{role}/-/Assignment", head=forum_id
        )
        assigned_participants = [e.tail for e in part_edges]

        # Get all notes for this invitation
        # We fetch all notes in the forum and filter locally to handle deeply nested
        # invitation paths (e.g., Submission5/Official_Review1/Rebuttal1/-/Rebuttal_Acknowledgement)
        try:
            all_forum_notes = client.get_all_notes(forum=forum_id)
            completed_notes = []
            for n in all_forum_notes:
                invs = getattr(n, "invitations", [])
                if any(f"/-/{invitation_suffix}" in inv for inv in invs):
                    completed_notes.append(n)
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


@mcp.tool()
@retry_on_429()
def get_rebuttal_acknowledgement_status(venue_id: str) -> List[Dict[str, Any]]:
    """
    Check which reviewers have completed the Rebuttal Acknowledgement.
    This is a convenience wrapper that automatically detects the correct
    invitation name (e.g., Rebuttal_Acknowledgement vs Rebuttal_Acknowledgment).
    """
    report1 = get_invitation_status(
        venue_id=venue_id, invitation_suffix="Rebuttal_Acknowledgement"
    )
    report2 = get_invitation_status(
        venue_id=venue_id, invitation_suffix="Rebuttal_Acknowledgment"
    )

    total_completed1 = sum(r.get("completed_count", 0) for r in report1)
    total_completed2 = sum(r.get("completed_count", 0) for r in report2)

    if total_completed2 > total_completed1:
        return report2
    return report1


@mcp.tool()
@retry_on_429()
def download_batch_pdfs(
    venue_id: str,
    role: str = "Reviewers",
    output_dir: Optional[str] = None,
    delay: float = 1.0,
) -> Dict[str, Any]:
    """
    Batch download all PDFs for papers assigned to the user as an Area Chair or Reviewer.
    Includes proactive delays between downloads to prevent hitting OpenReview rate limits.

    Args:
        venue_id: The ID of the venue (e.g., 'collas.org/2026/Conference').
        role: The role, either 'Reviewers' (default) or 'Area_Chairs' (also accepts 'Area_Chair', 'AC', 'Reviewer').
        output_dir: Directory where PDFs will be saved. Defaults to 'downloads/<venue_id>/<role>' under current workspace.
        delay: Proactive delay in seconds between consecutive PDF downloads (default: 1.0s).
    """
    client = get_client()

    # Normalize role
    normalized_role = role.lower().strip()
    if normalized_role in ["ac", "area_chair", "area_chairs"]:
        role_name = "Area_Chairs"
    elif normalized_role in ["reviewer", "reviewers"]:
        role_name = "Reviewers"
    else:
        role_name = role

    # Get assignments
    submissions = _get_assigned_submissions(client, venue_id, role_name)

    if not submissions:
        return {
            "status": "success",
            "message": f"No assignments found for role '{role_name}' in venue '{venue_id}'.",
            "downloaded": [],
            "failed": [],
        }

    # Resolve output directory
    if not output_dir:
        # Sanitize venue_id for folder naming
        safe_venue = re.sub(r"[^a-zA-Z0-9_\-]", "_", venue_id)
        output_dir = os.path.join("downloads", safe_venue, role_name)

    os.makedirs(output_dir, exist_ok=True)

    downloaded = []
    failed = []

    for i, s in enumerate(submissions):
        title = s.content.get("title", {}).get("value", "No Title")
        number = s.number

        # Sanitize title for filename
        clean_title = _sanitize_filename_component(title)
        filename = f"paper_{number}_{clean_title}.pdf"
        file_path = os.path.join(output_dir, filename)

        # Proactive delay to avoid rate limit
        if i > 0 and delay > 0:
            time.sleep(delay)

        try:
            # Download PDF binary content
            pdf_data = client.get_pdf(id=s.id)

            with open(file_path, "wb") as f:
                f.write(pdf_data)

            downloaded.append(
                {
                    "id": s.id,
                    "number": number,
                    "title": title,
                    "file_path": os.path.abspath(file_path),
                }
            )
        except Exception as e:
            failed.append(
                {"id": s.id, "number": number, "title": title, "error": str(e)}
            )

    return {
        "status": "completed",
        "message": f"Batch download completed. Successful: {len(downloaded)}, Failed: {len(failed)}",
        "output_dir": os.path.abspath(output_dir),
        "downloaded": downloaded,
        "failed": failed,
    }


def _parse_submission_id(submission_id_or_url: str) -> str:
    """Extract a submission or forum ID from a raw ID string or an OpenReview URL."""
    s = submission_id_or_url.strip()
    if "openreview.net" in s or "id=" in s:
        parsed = urllib.parse.urlparse(s)
        qs = urllib.parse.parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]
    return s


def _extract_note_attachments(note: Any) -> List[Dict[str, Any]]:
    """
    Discover all attachment fields from an OpenReview Note object.
    Detects attachments when content value contains '/attachment/' or '/pdf/'
    or has common file extensions.
    """
    attachments = []
    content = getattr(note, "content", {}) or {}
    for field_name, field_val in content.items():
        if isinstance(field_val, dict):
            val_str = str(field_val.get("value") or "")
        else:
            val_str = str(field_val or "")

        if not val_str:
            continue

        is_attachment = False
        if "/attachment/" in val_str or "/pdf/" in val_str:
            is_attachment = True
        elif field_name.lower() in [
            "rebuttal",
            "supplementary_material",
            "attachment",
            "rebuttal_file",
            "author_rebuttal",
            "rebuttal_attachment",
            "decision_file",
        ] and (
            re.search(
                r"\.(pdf|zip|tar|gz|tgz|bz2|7z|csv|tsv|docx?|pptx?)$",
                val_str,
                re.I,
            )
            or val_str.startswith("/")
        ):
            is_attachment = True

        if is_attachment:
            ext_match = re.search(r"\.([a-zA-Z0-9]+)(?:$|\?)", val_str)
            ext = ext_match.group(1).lower() if ext_match else "pdf"

            attachments.append(
                {
                    "note_id": note.id,
                    "field_name": field_name,
                    "value": val_str,
                    "ext": ext,
                    "invitations": getattr(note, "invitations", []),
                }
            )
    return attachments


def _download_attachment_data(
    client: Any, note_id: str, field_name: str, value: str = ""
) -> bytes:
    """Download attachment binary content from OpenReview client with fallbacks."""
    try:
        if hasattr(client, "get_attachment"):
            try:
                return client.get_attachment(field_name, id=note_id)
            except TypeError:
                return client.get_attachment(note_id, field_name)
    except Exception as e:
        if field_name == "pdf" or "/pdf/" in value:
            try:
                return client.get_pdf(id=note_id)
            except Exception:
                pass
        raise e

    if field_name == "pdf" or "/pdf/" in value:
        return client.get_pdf(id=note_id)

    raise ValueError(
        f"Unable to download attachment '{field_name}' for note '{note_id}'."
    )


@mcp.tool()
@retry_on_429()
def download_submission_attachments(
    submission_id_or_url: str,
    attachment_type: str = "rebuttal",
    output_dir: Optional[str] = None,
    include_replies: bool = True,
) -> Dict[str, Any]:
    """
    Download attached files (such as author rebuttals, supplementary material, etc.)
    for a specific OpenReview submission or forum.

    Args:
        submission_id_or_url: The OpenReview submission/forum ID (e.g. 'TmGjiyXgaq')
            or full forum URL (e.g. 'https://openreview.net/forum?id=TmGjiyXgaq&...').
        attachment_type: Filter for which attachment to download:
            - 'rebuttal' (default): downloads author rebuttal attachment(s).
            - 'supplementary_material': downloads supplementary material.
            - 'all': downloads all attached files found in the submission (and replies).
            - or any specific field name (e.g., 'pdf', 'code').
        output_dir: Target directory where files will be saved.
            Defaults to 'downloads/<sanitized_venue>/paper_<number>_attachments'.
        include_replies: If True (default), also searches reply notes in the forum
            (e.g., author rebuttal comment notes) for attachments.
    """
    client = get_client()
    sub_id = _parse_submission_id(submission_id_or_url)
    submission = client.get_note(sub_id)

    notes = [submission]
    if include_replies:
        try:
            replies = client.get_all_notes(forum=sub_id)
            for r in replies:
                if r.id != submission.id:
                    notes.append(r)
        except Exception:
            pass

    all_attachments = []
    for n in notes:
        all_attachments.extend(_extract_note_attachments(n))

    # Filter attachments
    filter_type = attachment_type.lower().strip()
    matching_attachments = []
    for att in all_attachments:
        fn = att["field_name"].lower()
        if filter_type == "all":
            matching_attachments.append(att)
        elif filter_type == "rebuttal":
            invs = [inv.lower() for inv in att.get("invitations", [])]
            if "rebuttal" in fn or any("rebuttal" in inv for inv in invs):
                matching_attachments.append(att)
        elif filter_type == fn:
            matching_attachments.append(att)

    title = ""
    if hasattr(submission, "content") and isinstance(submission.content, dict):
        title_val = submission.content.get("title")
        if isinstance(title_val, dict):
            title = str(title_val.get("value") or "No Title")
        else:
            title = str(title_val or "No Title")

    number = (
        submission.number
        if hasattr(submission, "number") and submission.number is not None
        else sub_id
    )

    if not matching_attachments:
        available = list({att["field_name"] for att in all_attachments})
        return {
            "status": "not_found",
            "message": (
                f"No attachments matching type '{attachment_type}' found for submission {sub_id}. "
                f"Available attachment fields: {available}"
            ),
            "submission_id": sub_id,
            "number": number,
            "title": title,
            "available_attachments": available,
            "downloaded": [],
            "failed": [],
        }

    # Resolve output directory
    if not output_dir:
        venue_id = ""
        if hasattr(submission, "content") and isinstance(submission.content, dict):
            venue_val = submission.content.get("venueid")
            if isinstance(venue_val, dict):
                venue_id = str(venue_val.get("value") or "")
            else:
                venue_id = str(venue_val or "")

        safe_venue = (
            re.sub(r"[^a-zA-Z0-9_\-]", "_", venue_id)
            if venue_id
            else "attachments"
        )
        output_dir = os.path.join("downloads", safe_venue, f"paper_{number}_attachments")

    os.makedirs(output_dir, exist_ok=True)

    clean_title = _sanitize_filename_component(title)
    downloaded = []
    failed = []

    for att in matching_attachments:
        fn = att["field_name"]
        ext = att["ext"]
        note_id = att["note_id"]

        # Filename construction
        if note_id == submission.id:
            filename = f"paper_{number}_{fn}_{clean_title}.{ext}"
        else:
            filename = f"paper_{number}_{fn}_{note_id[:8]}_{clean_title}.{ext}"

        file_path = os.path.join(output_dir, filename)

        try:
            data = _download_attachment_data(
                client, note_id=note_id, field_name=fn, value=att["value"]
            )
            with open(file_path, "wb") as f:
                f.write(data)

            downloaded.append(
                {
                    "note_id": note_id,
                    "field_name": fn,
                    "filename": filename,
                    "file_path": os.path.abspath(file_path),
                    "size_bytes": len(data),
                }
            )
        except Exception as e:
            failed.append(
                {
                    "note_id": note_id,
                    "field_name": fn,
                    "filename": filename,
                    "error": str(e),
                }
            )

    return {
        "status": "completed" if not failed else ("partial" if downloaded else "failed"),
        "message": f"Downloaded {len(downloaded)} attachment(s). Failed: {len(failed)}.",
        "submission_id": sub_id,
        "number": number,
        "title": title,
        "output_dir": os.path.abspath(output_dir),
        "downloaded": downloaded,
        "failed": failed,
    }


@mcp.tool()
@retry_on_429()
def download_batch_attachments(
    venue_id: str,
    role: str = "Reviewers",
    attachment_type: str = "rebuttal",
    output_dir: Optional[str] = None,
    delay: float = 1.0,
) -> Dict[str, Any]:
    """
    Batch download attached files (e.g., rebuttals or supplementary materials)
    for papers assigned to the user as an Area Chair or Reviewer.
    Includes proactive delays between downloads to prevent hitting OpenReview rate limits.

    Args:
        venue_id: The ID of the venue (e.g., 'thecvf.com/WACV/2027/Conference_Round_2').
        role: The role, either 'Reviewers' (default) or 'Area_Chairs' (also accepts 'Area_Chair', 'AC', 'Reviewer').
        attachment_type: Type of attachment to download ('rebuttal' default, 'supplementary_material', or 'all').
        output_dir: Directory where attachments will be saved. Defaults to 'downloads/<venue_id>/<role>_<attachment_type>s'.
        delay: Proactive delay in seconds between consecutive downloads (default: 1.0s).
    """
    client = get_client()

    # Normalize role
    normalized_role = role.lower().strip()
    if normalized_role in ["ac", "area_chair", "area_chairs"]:
        role_name = "Area_Chairs"
    elif normalized_role in ["reviewer", "reviewers"]:
        role_name = "Reviewers"
    else:
        role_name = role

    # Get assignments
    submissions = _get_assigned_submissions(client, venue_id, role_name)

    if not submissions:
        return {
            "status": "success",
            "message": f"No assignments found for role '{role_name}' in venue '{venue_id}'.",
            "downloaded": [],
            "failed": [],
        }

    # Resolve output directory
    safe_venue = re.sub(r"[^a-zA-Z0-9_\-]", "_", venue_id)
    clean_att = re.sub(r"[^a-zA-Z0-9_\-]", "_", attachment_type.lower())
    if not output_dir:
        output_dir = os.path.join(
            "downloads", safe_venue, f"{role_name.lower()}_{clean_att}s"
        )

    os.makedirs(output_dir, exist_ok=True)

    downloaded = []
    failed = []

    for i, s in enumerate(submissions):
        if i > 0 and delay > 0:
            time.sleep(delay)

        sub_res = download_submission_attachments(
            submission_id_or_url=s.id,
            attachment_type=attachment_type,
            output_dir=output_dir,
            include_replies=True,
        )

        for d in sub_res.get("downloaded", []):
            d["submission_id"] = s.id
            d["number"] = s.number
            downloaded.append(d)

        for f in sub_res.get("failed", []):
            f["submission_id"] = s.id
            f["number"] = s.number
            failed.append(f)

    return {
        "status": "completed",
        "message": f"Batch download completed. Successful attachments: {len(downloaded)}, Failed: {len(failed)}.",
        "output_dir": os.path.abspath(output_dir),
        "downloaded": downloaded,
        "failed": failed,
    }


@mcp.tool()
@retry_on_429()
def get_top_10_emergency_reviewers(venue_id: str, forum_id: str) -> Dict[str, List[str]]:
    """
    Find the top-10 reviewers with the highest affinity for a given paper that haven't reached their reviewing quota.
    Returns a mapping of {reviewer_profile_id: [recent_paper_titles_from_openreview]}.
    """
    client = get_client()

    # 1. Fetch Affinity Scores for this paper
    affinity_edges = client.get_edges(
        invitation=f"{venue_id}/Reviewers/-/Affinity_Score",
        head=forum_id,
        limit=1000
    )
    
    if not affinity_edges:
        return {}
        
    affinity_edges.sort(key=lambda e: getattr(e, 'weight', 0) or 0, reverse=True)
    affinity_edges = affinity_edges[:100]

    # 2. Fetch Reviewer Quotas
    max_papers_edges = client.get_all_edges(
        invitation=f"{venue_id}/Reviewers/-/Custom_Max_Papers"
    )
    quota_map = {e.tail: int(e.weight) for e in max_papers_edges if e.weight is not None}
    DEFAULT_QUOTA = 5
    
    top_10_reviewers = []
    candidate_ids = [e.tail for e in affinity_edges]
    
    for candidate_id in candidate_ids:
        if len(top_10_reviewers) >= 10:
            break
            
        assignments = client.get_edges(
            invitation=f"{venue_id}/Reviewers/-/Assignment",
            tail=candidate_id
        )
        current_load = len(assignments)
        candidate_quota = quota_map.get(candidate_id, DEFAULT_QUOTA)
        
        if current_load < candidate_quota:
            top_10_reviewers.append(candidate_id)
            
    if not top_10_reviewers:
        return {}
        
    # 3. Fetch profiles and recent publications
    profiles = openreview.tools.get_profiles(client, ids_or_emails=top_10_reviewers, with_publications=True)
    
    result = {}
    for profile in profiles:
        profile_id = profile.id
        pubs = profile.content.get('publications', [])
        # Sort by creation date or publication date
        pubs.sort(key=lambda x: getattr(x, 'cdate', 0) or getattr(x, 'pdate', 0) or 0, reverse=True)
        recent_pubs = pubs[:20]
        
        # Check if they have recent papers (from 2025 onwards)
        has_recent = False
        YEAR_2025_MS = 1735689600000
        for p in recent_pubs:
            if getattr(p, 'cdate', 0) >= YEAR_2025_MS or getattr(p, 'pdate', 0) >= YEAR_2025_MS:
                has_recent = True
                break
                
        def get_title(note):
            content = getattr(note, 'content', {})
            if isinstance(content, dict):
                title_field = content.get('title')
                if isinstance(title_field, dict):
                    return title_field.get('value', 'Unknown Title')
                return title_field or 'Unknown Title'
            return 'Unknown Title'
                
        titles = [get_title(p) for p in recent_pubs]
        
        # Fallback to Google Scholar if no recent papers
        if not has_recent:
            import urllib.parse
            import requests
            import time
            import re
            
            names = profile.content.get('names', [])
            if names:
                preferred = next((n for n in names if n.get('preferred')), names[0])
                first = preferred.get('first', '')
                last = preferred.get('last', '')
                author_query = f"{first} {last}".strip()
                
                if author_query:
                    url = f'https://scholar.google.com/scholar?hl=en&q=author:"{urllib.parse.quote_plus(author_query)}"&as_ylo=2025'
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    try:
                        time.sleep(5)  # Big delay to avoid rate limits as requested
                        r = requests.get(url, headers=headers, timeout=10)
                        if r.status_code == 200:
                            for match in re.finditer(r'<h3 class="gs_rt".*?>(?:<a.*?>)?(.*?)(?:</a>)?</h3>', r.text):
                                clean_title = re.sub(r'<[^>]+>', '', match.group(1))
                                if "User profiles for author" not in clean_title and "[PDF]" not in clean_title:
                                    titles.append(f"[Scholar Fallback] {clean_title}")
                    except Exception as e:
                        print(f"Scholar fallback failed for {author_query}: {e}")

        result[profile_id] = titles
        
    return result


@mcp.tool()
@retry_on_429()
def invite_reviewer(venue_id: str, forum_id: str, reviewer_id: str) -> str:
    """
    Invite a specific reviewer to review a given paper using the Invite_Assignment edge.
    """
    client = get_client()

    try:
        sub = client.get_note(forum_id)
        number = sub.number
        # Discover the correct Area Chair signature for this paper
        sig, parent = _get_submission_contact_info(client, venue_id, number)
    except Exception:
        # Fallback if note fetching fails
        sig = venue_id

    invite_edge = Edge(
        invitation=f"{venue_id}/Reviewers/-/Invite_Assignment",
        head=forum_id,
        tail=reviewer_id,
        label="Invitation Sent",
        weight=0,
        signatures=[sig],
        readers=[venue_id, reviewer_id, sig],
        writers=[venue_id, sig]
    )
    
    client.post_edge(invite_edge)
    return f"Successfully sent invitation to {reviewer_id} for forum {forum_id}."


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
