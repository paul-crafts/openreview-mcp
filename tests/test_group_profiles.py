from unittest.mock import MagicMock, patch

import pytest

from openreview_mcp.server import get_group_members, get_profiles


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_get_group_members(mock_client):
    group = MagicMock()
    group.id = "V/Submission1/Reviewer_abcd"
    group.members = ["~Jane_Doe1"]
    mock_client.get_group.return_value = group

    result = get_group_members("V/Submission1/Reviewer_abcd")

    mock_client.get_group.assert_called_once_with("V/Submission1/Reviewer_abcd")
    assert result == {"group_id": "V/Submission1/Reviewer_abcd", "members": ["~Jane_Doe1"]}


def test_get_group_members_empty(mock_client):
    group = MagicMock()
    group.id = "V/Submission1/Area_Chairs"
    group.members = None
    mock_client.get_group.return_value = group

    assert get_group_members("V/Submission1/Area_Chairs")["members"] == []


def _profile(pid, names, email):
    p = MagicMock()
    p.id = pid
    p.content = {"names": names, "preferredEmail": email}
    return p


def test_get_profiles_names_and_emails(mock_client):
    mock_client.get_profiles.return_value = [
        _profile(
            "~Jane_Doe1",
            [
                {"fullname": "J. Doe"},
                {"fullname": "Jane Doe", "preferred": True},
            ],
            {"value": "jane@example.com"},
        ),
        _profile("~Bob_Roe1", [{"fullname": "Bob Roe"}], "bo****@example.com"),
    ]

    result = get_profiles(["~Bob_Roe1", "~Jane_Doe1", "~Missing1"])

    assert result == [
        {"id": "~Bob_Roe1", "name": "Bob Roe", "email": None},
        {"id": "~Jane_Doe1", "name": "Jane Doe", "email": "jane@example.com"},
        {"id": "~Missing1", "name": None, "email": None},
    ]


def test_get_profiles_batch_failure_falls_back(mock_client):
    mock_client.get_profiles.side_effect = Exception("boom")
    mock_client.get_profile.return_value = _profile(
        "~Jane_Doe1", [{"fullname": "Jane Doe"}], "jane@example.com"
    )

    result = get_profiles(["~Jane_Doe1"])

    assert result[0]["name"] == "Jane Doe"
    assert result[0]["email"] == "jane@example.com"
