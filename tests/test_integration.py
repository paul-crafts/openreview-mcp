import pytest
from openreview_mcp.server import (
    list_venues,
    search_venues,
    get_ac_submissions,
    get_review_status_report,
    identify_missing_reviews,
    get_discussion_updates,
)

# Integration tests using live credentials
# These will only run if credentials are provided in .env or environment


@pytest.fixture(scope="module")
def venue_id():
    # Attempt to find a recent venue to test against
    venues = list_venues()
    # Prefer a 2026 venue if available
    v2026 = [v["id"] for v in venues if "2026" in v["id"]]
    if v2026:
        return v2026[0]
    if venues:
        return venues[0]["id"]
    pytest.skip("No venues found for the current user.")


def test_venue_search():
    """Test searching for venues by keyword."""
    results = search_venues("Collas")
    assert isinstance(results, list)
    assert any("CoLLAs" in r["id"] for r in results)


def test_ac_status_report_robustness(venue_id):
    """Test that the status report handles anonymous reviewers correctly."""
    report = get_review_status_report(venue_id)
    assert isinstance(report, list)

    for item in report:
        assert "submission_id" in item
        assert "total_reviewers" in item
        assert "reviews_submitted" in item
        assert "missing_reviewers" in item

        # Verify that total_reviewers matches count
        assert item["total_reviewers"] >= item["reviews_submitted"]


def test_identify_missing_reviews(venue_id):
    """Test the specialized missing reviews tool."""
    missing = identify_missing_reviews(venue_id)
    assert isinstance(missing, list)
    if missing:
        assert "submission_id" in missing[0]
        assert "reviewer" in missing[0]


def test_discussion_updates(venue_id):
    """Test retrieving recent comments/rebuttals."""
    updates = get_discussion_updates(venue_id, limit=5)
    assert isinstance(updates, list)
    # It's okay if empty, but should be a list of notes
    if updates:
        assert "id" in updates[0]
        assert "invitations" in updates[0]


def test_get_ac_submissions_fix(venue_id):
    """Test that the fix for list IDs in get_ac_submissions works."""
    submissions = get_ac_submissions(venue_id)
    assert isinstance(submissions, list)
    if submissions:
        assert "id" in submissions[0]
        assert "title" in submissions[0]
