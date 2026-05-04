import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import (
    get_inbox_summary,
    get_invitation_status,
    get_server_time,
    _parse_since,
)


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_parse_since():
    import time

    now = time.time() * 1000

    # Test '1d'
    ts = _parse_since("1d")
    assert ts < now - (23 * 3600 * 1000)

    # Test '2h'
    ts = _parse_since("2h")
    assert ts < now - (1 * 3600 * 1000)

    # Test None
    assert _parse_since(None) == 0


def test_get_server_time(mock_client):
    # Mock response with Date header
    mock_response = MagicMock()
    mock_response.headers = {"Date": "Mon, 04 May 2026 10:00:00 GMT"}
    mock_client.session.get.return_value = mock_response
    mock_client.baseurl = "https://api2.openreview.net"

    result = get_server_time()
    assert "server_time_gmt" in result
    assert "local_time_utc" in result
    assert "drift_seconds" in result


def test_get_inbox_summary_empty(mock_client):
    mock_client.profile.id = "~Test_User1"
    mock_client.get_all_edges.return_value = []

    result = get_inbox_summary("test.venue")
    assert result == []


def test_get_invitation_status_basic(mock_client):
    mock_client.profile.id = "~Test_AC1"

    # Mock assignments
    mock_edge = MagicMock()
    mock_edge.head = "paper1"
    mock_client.get_all_edges.side_effect = [
        [mock_edge],  # AC assignments
        [MagicMock(tail="~Reviewer1")],  # Paper1 reviewer assignments
    ]

    # Mock submission info
    mock_sub = MagicMock()
    mock_sub.id = "paper1"
    mock_sub.number = 1
    mock_sub.content = {"title": "Test Paper"}
    mock_client.get_notes_by_ids.return_value = [mock_sub]

    # Mock confirmations
    mock_conf = MagicMock()
    mock_conf.signatures = ["test.venue/Submission1/Reviewer_1"]
    mock_client.get_all_notes.return_value = [mock_conf]

    # Mock group resolution
    mock_group = MagicMock()
    mock_group.members = ["~Reviewer1"]
    mock_client.get_group.return_value = mock_group

    result = get_invitation_status("test.venue", "Review_Confirmation")

    assert len(result) == 1
    assert result[0]["paper_number"] == 1
    assert result[0]["completed_count"] == 1
    assert len(result[0]["missing_participants"]) == 0


def test_get_reviewer_emails_unmasking(mock_client):
    from openreview_mcp.server import get_reviewer_emails

    # Mock masked profile
    mock_profile = MagicMock()
    mock_profile.id = "~Reviewer1"
    mock_profile.content = {"preferredEmail": "rev****@gmail.com"}
    mock_client.search_profiles.return_value = [mock_profile]

    # Mock message log for unmasking
    mock_msg = {
        "content": {"to": "actual_email@gmail.com", "text": "Dear ~Reviewer1..."},
        "signature": "~Reviewer1",
    }
    mock_client.get_messages.return_value = [mock_msg]

    result = get_reviewer_emails(["~Reviewer1"], venue_id="test_venue")

    assert result["~Reviewer1"] == "actual_email@gmail.com"


def test_retry_on_429():
    from openreview_mcp.server import retry_on_429
    from openreview import OpenReviewException
    import time

    # Mock time.sleep to avoid waiting during tests
    with patch("time.sleep") as mock_sleep:
        call_count = 0

        @retry_on_429(max_retries=3)
        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # Raise a mock 429
                raise OpenReviewException(
                    {"status": 429, "message": "Please try again in 1 seconds"}
                )
            return "success"

        result = failing_func()
        assert result == "success"
        assert call_count == 3
        assert mock_sleep.call_count == 2
