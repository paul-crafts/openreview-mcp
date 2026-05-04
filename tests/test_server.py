import os
import pytest
from openreview_mcp.server import (
    get_profile,
    list_venues,
    get_ac_submissions,
    get_review_status_report,
)


# Ensure credentials are set for live tests
@pytest.fixture(scope="module")
def check_credentials():
    if not os.environ.get("OPENREVIEW_USERNAME") or not os.environ.get(
        "OPENREVIEW_PASSWORD"
    ):
        pytest.skip("Credentials not set in environment variables")


def test_get_profile(check_credentials):
    """Test retrieving the current user's profile."""
    profile = get_profile()
    assert "id" in profile
    assert profile["id"].startswith("~") or "@" in profile["id"]
    print(f"Logged in as: {profile['id']}")


def test_list_venues(check_credentials):
    """Test listing venues for the user."""
    venues = list_venues()
    assert isinstance(venues, list)
    # Even if empty, it should be a list
    if venues:
        assert "id" in venues[0]
        assert "role" in venues[0]
        print(f"Found {len(venues)} venues.")


def test_ac_tools_real_data(check_credentials):
    """Test AC tools on the first available venue."""
    venues = list_venues()
    ac_venues = [v for v in venues if "Area_Chair" in v["role"] or "AC" in v["role"]]

    if not ac_venues:
        pytest.skip("No AC roles found for the current user.")

    venue_id = ac_venues[0]["id"]
    print(f"Testing tools for venue: {venue_id}")

    submissions = get_ac_submissions(venue_id)
    assert isinstance(submissions, list)

    if submissions:
        report = get_review_status_report(venue_id)
        assert isinstance(report, list)
        assert len(report) > 0
        print(f"Report retrieved for {len(report)} submissions.")


def test_send_message_dry_run(check_credentials):
    """Test the messaging tool with dry_run=True (safe)."""
    from openreview_mcp.server import send_bulk_message

    result = send_bulk_message(
        venue_id="test.cc/2026/Conference",
        recipients=["test@example.com"],
        subject="Test Subject",
        message="Test Message",
        dry_run=True,
    )

    assert result["status"] == "preview"
    assert "test@example.com" in result["recipients"]
