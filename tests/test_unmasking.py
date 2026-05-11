import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import get_reviewer_emails

@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_get_reviewer_emails_get_profiles(mock_client):
    # Test unmasking via get_profiles (v2 format)
    mock_p = MagicMock()
    mock_p.id = "~User1"
    mock_p.content = {"preferredEmail": {"value": "unmasked@example.com"}}
    mock_client.get_profiles.return_value = [mock_p]
    
    result = get_reviewer_emails(["~User1"])
    assert result["~User1"] == "unmasked@example.com"

def test_get_reviewer_emails_registration_fallback(mock_client):
    # Test unmasking via Registration notes
    mock_p = MagicMock()
    mock_p.id = "~User2"
    mock_p.content = {"preferredEmail": "masked****@example.com"}
    mock_client.get_profiles.return_value = [mock_p]
    
    mock_note = MagicMock()
    mock_note.content = {"email": {"value": "reg@example.com"}}
    mock_client.get_all_notes.side_effect = [[mock_note]]
    
    result = get_reviewer_emails(["~User2"], venue_id="venue")
    assert result["~User2"] == "reg@example.com"

def test_get_reviewer_emails_tilde_fallback(mock_client):
    # Test unmasking via Tilde group members
    mock_p = MagicMock()
    mock_p.id = "~User3"
    mock_p.content = {"preferredEmail": "masked****@example.com"}
    mock_client.get_profiles.return_value = [mock_p]
    mock_client.get_all_notes.return_value = []
    
    mock_group = MagicMock()
    mock_group.members = ["real@example.com"]
    mock_client.get_group.return_value = mock_group
    
    result = get_reviewer_emails(["~User3"], venue_id="venue")
    assert result["~User3"] == "real@example.com"

def test_get_reviewer_emails_message_fallback(mock_client):
    # Test unmasking via messages
    mock_p = MagicMock()
    mock_p.id = "~User4"
    mock_p.content = {"preferredEmail": "masked****@example.com"}
    mock_client.get_profiles.return_value = [mock_p]
    mock_client.get_all_notes.return_value = []
    mock_client.get_group.side_effect = Exception("Not found")
    
    mock_msg = {
        "content": {"to": "msg@example.com", "text": "Hi ~User4"},
        "to": "~User4"
    }
    mock_client.get_messages.return_value = [mock_msg]
    
    result = get_reviewer_emails(["~User4"], venue_id="venue")
    assert result["~User4"] == "msg@example.com"
