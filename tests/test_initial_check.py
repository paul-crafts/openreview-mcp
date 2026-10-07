import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import (
    get_initial_check_status,
    submit_initial_check,
    batch_submit_initial_checks,
)


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_submit_initial_check_validation_empty_flags():
    with pytest.raises(ValueError, match="must be a non-empty list"):
        submit_initial_check(
            venue_id="test.venue/2027",
            submission_id_or_number=1,
            flag_for_desk_rejection=[],
            dry_run=True,
        )


def test_submit_initial_check_validation_no_issue_combined():
    with pytest.raises(ValueError, match="cannot be combined"):
        submit_initial_check(
            venue_id="test.venue/2027",
            submission_id_or_number=1,
            flag_for_desk_rejection=["no_issue", "out_of_scope"],
            dry_run=True,
        )


def test_submit_initial_check_validation_other_requires_details(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(id="sub1", number=1, forum="sub1", content={"title": {"value": "Test"}})
    mock_client.get_all_edges.return_value = [MagicMock(head="sub1")]
    mock_client.get_note.return_value = sub

    inv = MagicMock()
    inv.edit = {
        "note": {
            "content": {
                "flag_for_desk_rejection": {
                    "value": {
                        "param": {
                            "items": [
                                {"value": "no_issue"},
                                {"value": "other"},
                            ]
                        }
                    }
                }
            }
        }
    }
    mock_client.get_invitation.return_value = inv

    with pytest.raises(ValueError, match="must be provided"):
        submit_initial_check(
            venue_id="test.venue/2027",
            submission_id_or_number=1,
            flag_for_desk_rejection=["other"],
            additional_details="",
            dry_run=True,
        )


def test_submit_initial_check_dry_run_success(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(id="sub1", number=1, forum="sub1", content={"title": {"value": "Test Paper"}})
    mock_client.get_all_edges.return_value = [MagicMock(head="sub1")]
    mock_client.get_note.return_value = sub
    mock_client.get_groups.return_value = [
        MagicMock(id="test.venue/2027/Submission1/Area_Chair_abc")
    ]
    mock_client.get_notes.return_value = []

    inv = MagicMock()
    inv.edit = {
        "note": {
            "content": {
                "flag_for_desk_rejection": {
                    "value": {
                        "param": {
                            "items": [
                                {"value": "no_issue"},
                                {"value": "out_of_scope"},
                            ]
                        }
                    }
                }
            },
            "readers": ["test.venue/2027/Program_Chairs"],
        }
    }
    mock_client.get_invitation.return_value = inv

    res = submit_initial_check(
        venue_id="test.venue/2027",
        submission_id_or_number=1,
        flag_for_desk_rejection=["no_issue"],
        dry_run=True,
    )

    assert res["status"] == "preview"
    assert res["paper_number"] == 1
    assert res["submission_id"] == "sub1"
    assert res["flag_for_desk_rejection"] == ["no_issue"]
    # Ensure post_note_edit was NOT called in dry run
    mock_client.post_note_edit.assert_not_called()


def test_submit_initial_check_post_success(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(id="sub1", number=1, forum="sub1", content={"title": {"value": "Test Paper"}})
    mock_client.get_all_edges.return_value = [MagicMock(head="sub1")]
    mock_client.get_note.return_value = sub
    mock_client.get_groups.return_value = [
        MagicMock(id="test.venue/2027/Submission1/Area_Chair_abc")
    ]
    mock_client.get_notes.return_value = []

    inv = MagicMock()
    inv.edit = {
        "note": {
            "content": {
                "flag_for_desk_rejection": {
                    "value": {
                        "param": {
                            "items": [
                                {"value": "no_issue"},
                                {"value": "improperly_formatted"},
                            ]
                        }
                    }
                }
            },
            "readers": ["test.venue/2027/Program_Chairs"],
        }
    }
    mock_client.get_invitation.return_value = inv
    mock_client.post_note_edit.return_value = {
        "id": "edit123",
        "note": {"id": "note123"},
    }

    res = submit_initial_check(
        venue_id="test.venue/2027",
        submission_id_or_number=1,
        flag_for_desk_rejection=["no_issue"],
        dry_run=False,
    )

    assert res["status"] == "success"
    assert res["action"] == "created"
    assert res["note_id"] == "note123"
    mock_client.post_note_edit.assert_called_once()


def test_get_initial_check_status(mock_client):
    mock_client.profile.id = "~AC1"
    sub1 = MagicMock(id="sub1", number=1, forum="sub1", content={"title": {"value": "Paper 1"}})
    sub2 = MagicMock(id="sub2", number=2, forum="sub2", content={"title": {"value": "Paper 2"}})
    mock_client.get_all_edges.return_value = [
        MagicMock(head="sub1"),
        MagicMock(head="sub2"),
    ]
    mock_client.get_note.side_effect = lambda sid: sub1 if sid == "sub1" else sub2

    inv = MagicMock()
    inv.duedate = 1791633540000
    inv.edit = {
        "note": {
            "content": {
                "flag_for_desk_rejection": {
                    "value": {
                        "param": {
                            "items": [
                                {"value": "no_issue", "description": "No issue"},
                            ]
                        }
                    }
                }
            }
        }
    }
    mock_client.get_invitation.return_value = inv

    # sub1 has note, sub2 does not
    note1 = MagicMock(
        id="note1",
        cdate=1791000000000,
        content={
            "flag_for_desk_rejection": {"value": ["no_issue"]},
            "additional_details": {"value": "All good"},
        },
    )
    mock_client.get_notes.side_effect = lambda invitation: [note1] if "Submission1" in invitation else []

    res = get_initial_check_status(venue_id="test.venue/2027")

    assert res["total_submissions"] == 2
    assert res["submitted_count"] == 1
    assert res["pending_count"] == 1
    assert len(res["submissions"]) == 2
    assert res["submissions"][0]["status"] == "submitted"
    assert res["submissions"][0]["flag_for_desk_rejection"] == ["no_issue"]
    assert res["submissions"][1]["status"] == "pending"
    assert res["submissions"][1]["flag_for_desk_rejection"] is None


def test_batch_submit_initial_checks(mock_client):
    mock_client.profile.id = "~AC1"
    sub1 = MagicMock(id="sub1", number=1, forum="sub1", content={"title": {"value": "Paper 1"}})
    mock_client.get_all_edges.return_value = [MagicMock(head="sub1")]
    mock_client.get_note.return_value = sub1
    mock_client.get_groups.return_value = [
        MagicMock(id="test.venue/2027/Submission1/Area_Chair_abc")
    ]
    mock_client.get_notes.return_value = []

    inv = MagicMock()
    inv.edit = {
        "note": {
            "content": {
                "flag_for_desk_rejection": {
                    "value": {
                        "param": {
                            "items": [{"value": "no_issue"}]
                        }
                    }
                }
            }
        }
    }
    mock_client.get_invitation.return_value = inv

    res = batch_submit_initial_checks(
        venue_id="test.venue/2027",
        checks=[
            {
                "submission_id_or_number": 1,
                "flag_for_desk_rejection": ["no_issue"],
            }
        ],
        dry_run=True,
    )

    assert res["status"] == "success"
    assert res["total"] == 1
    assert res["succeeded_count"] == 1
    assert res["failed_count"] == 0
