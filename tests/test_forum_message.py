import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import (
    get_forum_message_options,
    post_forum_message,
    batch_post_forum_messages,
    _resolve_forum_readers,
    _format_template,
)


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_post_forum_message_validation_empty_comment():
    with pytest.raises(ValueError, match="cannot be empty"):
        post_forum_message(
            venue_id="ICLR.cc/2027/Conference",
            submission_id_or_number=5963,
            comment="",
            dry_run=True,
        )


def test_post_forum_message_validation_comment_too_long():
    long_comment = "a" * 5001
    with pytest.raises(ValueError, match="exceeds 5000 characters"):
        post_forum_message(
            venue_id="ICLR.cc/2027/Conference",
            submission_id_or_number=5963,
            comment=long_comment,
            dry_run=True,
        )


def test_post_forum_message_validation_title_too_long():
    long_title = "a" * 501
    with pytest.raises(ValueError, match="exceeds 500 characters"):
        post_forum_message(
            venue_id="ICLR.cc/2027/Conference",
            submission_id_or_number=5963,
            comment="Valid comment",
            title=long_title,
            dry_run=True,
        )


def test_format_template():
    # Regular placeholders
    res = _format_template(
        "Paper #{number}: {title} (url: {forum_url})",
        number=123,
        title="Sample Paper",
        forum_url="https://openreview.net/forum?id=abc",
    )
    assert res == "Paper #123: Sample Paper (url: https://openreview.net/forum?id=abc)"

    # LaTeX formula with curly braces should not trigger KeyError or break
    latex_text = "Formula: $\\sum_{i=1}^{N} x_i$ for Paper #{number}"
    res_latex = _format_template(latex_text, number=123)
    assert res_latex == "Formula: $\\sum_{i=1}^{N} x_i$ for Paper #123"


def test_resolve_forum_readers():
    inv = MagicMock()
    inv.edit = {
        "note": {
            "readers": {
                "param": {
                    "items": [
                        {
                            "value": "ICLR.cc/2027/Conference/Program_Chairs",
                            "optional": False,
                        },
                        {
                            "value": "ICLR.cc/2027/Conference/Submission100/Senior_Area_Chairs",
                            "optional": False,
                        },
                        {
                            "value": "ICLR.cc/2027/Conference/Submission100/Area_Chairs",
                            "optional": True,
                        },
                        {
                            "value": "ICLR.cc/2027/Conference/Submission100/Reviewers",
                            "optional": True,
                        },
                        {
                            "inGroup": "ICLR.cc/2027/Conference/Submission100/Reviewers",
                            "optional": True,
                        },
                    ]
                }
            }
        }
    }

    client = MagicMock()
    venue_id = "ICLR.cc/2027/Conference"
    sub_num = 100

    # 1. Default (reviewers / None)
    readers = _resolve_forum_readers(client, venue_id, sub_num, None, inv)
    assert "ICLR.cc/2027/Conference/Program_Chairs" in readers
    assert "ICLR.cc/2027/Conference/Submission100/Senior_Area_Chairs" in readers
    assert "ICLR.cc/2027/Conference/Submission100/Area_Chairs" in readers
    assert "ICLR.cc/2027/Conference/Submission100/Reviewers" in readers

    # 2. SAC only
    sac_readers = _resolve_forum_readers(
        client, venue_id, sub_num, "sac_only", inv
    )
    assert "ICLR.cc/2027/Conference/Program_Chairs" in sac_readers
    assert "ICLR.cc/2027/Conference/Submission100/Senior_Area_Chairs" in sac_readers
    assert "ICLR.cc/2027/Conference/Submission100/Area_Chairs" in sac_readers
    assert (
        "ICLR.cc/2027/Conference/Submission100/Reviewers" not in sac_readers
    )

    # 3. Individual reviewer
    ind_readers = _resolve_forum_readers(
        client, venue_id, sub_num, ["Reviewer_abc1"], inv
    )
    assert (
        "ICLR.cc/2027/Conference/Submission100/Reviewer_abc1" in ind_readers
    )
    assert "ICLR.cc/2027/Conference/Program_Chairs" in ind_readers
    assert "ICLR.cc/2027/Conference/Submission100/Senior_Area_Chairs" in ind_readers


def test_post_forum_message_dry_run(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(
        id="sub_test_id",
        number=5963,
        forum="sub_test_id",
        content={"title": {"value": "Test Paper Title"}},
    )
    mock_client.get_all_edges.return_value = [MagicMock(head="sub_test_id")]
    mock_client.get_note.return_value = sub

    inv = MagicMock(id="ICLR.cc/2027/Conference/Submission5963/-/Official_Comment")
    inv.edit = {
        "note": {
            "readers": {
                "param": {
                    "items": [
                        {
                            "value": "ICLR.cc/2027/Conference/Program_Chairs",
                            "optional": False,
                        },
                        {
                            "value": "ICLR.cc/2027/Conference/Submission5963/Senior_Area_Chairs",
                            "optional": False,
                        },
                    ]
                }
            }
        }
    }
    mock_client.get_invitation.return_value = inv
    mock_client.get_groups.return_value = [
        MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna")
    ]

    res = post_forum_message(
        venue_id="ICLR.cc/2027/Conference",
        submission_id_or_number=5963,
        comment="Please check the revision.",
        title="Action Required",
        dry_run=True,
    )

    assert res["status"] == "preview"
    assert res["paper_number"] == 5963
    assert res["submission_id"] == "sub_test_id"
    assert res["paper_title"] == "Test Paper Title"
    assert res["signature"] == "ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna"
    assert "ICLR.cc/2027/Conference/Program_Chairs" in res["readers"]
    assert "ICLR.cc/2027/Conference/Submission5963/Senior_Area_Chairs" in res["readers"]
    assert res["comment_title"] == "Action Required"
    assert res["full_comment"] == "Please check the revision."
    # Crucial: verify post_note_edit was NOT called
    mock_client.post_note_edit.assert_not_called()


def test_post_forum_message_mock_post(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(
        id="sub_test_id",
        number=5963,
        forum="sub_test_id",
        content={"title": {"value": "Test Paper Title"}},
    )
    mock_client.get_all_edges.return_value = [MagicMock(head="sub_test_id")]
    mock_client.get_note.return_value = sub

    inv = MagicMock(id="ICLR.cc/2027/Conference/Submission5963/-/Official_Comment")
    inv.edit = {"note": {"readers": {"param": {"items": []}}}}
    mock_client.get_invitation.return_value = inv
    mock_client.get_groups.return_value = [
        MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna")
    ]
    mock_client.post_note_edit.return_value = {"id": "edit_123", "note": {"id": "note_456"}}

    res = post_forum_message(
        venue_id="ICLR.cc/2027/Conference",
        submission_id_or_number=5963,
        comment="This is a test comment",
        dry_run=False,
    )

    assert res["status"] == "success"
    assert res["action"] == "created"
    assert res["note_id"] == "note_456"
    assert mock_client.post_note_edit.called

    call_args = mock_client.post_note_edit.call_args[1]
    assert call_args["invitation"] == "ICLR.cc/2027/Conference/Submission5963/-/Official_Comment"
    assert call_args["signatures"] == ["ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna"]
    posted_note = call_args["note"]
    assert posted_note.content["comment"]["value"] == "This is a test comment"
    assert posted_note.forum == "sub_test_id"
    assert posted_note.replyto == "sub_test_id"


def test_post_forum_message_update_existing(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(
        id="sub_test_id",
        number=5963,
        forum="sub_test_id",
        content={"title": {"value": "Test Paper Title"}},
    )
    mock_client.get_all_edges.return_value = [MagicMock(head="sub_test_id")]
    mock_client.get_note.return_value = sub

    inv = MagicMock(id="ICLR.cc/2027/Conference/Submission5963/-/Official_Comment")
    inv.edit = {"note": {"readers": {"param": {"items": []}}}}
    mock_client.get_invitation.return_value = inv
    mock_client.get_groups.return_value = [
        MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna")
    ]
    mock_client.post_note_edit.return_value = {"id": "edit_123", "note": {"id": "existing_comment_id"}}

    res = post_forum_message(
        venue_id="ICLR.cc/2027/Conference",
        submission_id_or_number=5963,
        comment="Updated comment text",
        comment_id="existing_comment_id",
        dry_run=False,
    )

    assert res["status"] == "success"
    assert res["action"] == "updated"
    call_args = mock_client.post_note_edit.call_args[1]
    assert call_args["note"].id == "existing_comment_id"


def test_batch_post_forum_messages_dry_run(mock_client):
    mock_client.profile.id = "~AC1"
    sub1 = MagicMock(
        id="sub1",
        number=1,
        forum="sub1",
        content={"title": {"value": "First Paper"}, "venueid": "ICLR.cc/2027/Conference"},
    )
    sub2 = MagicMock(
        id="sub2",
        number=2,
        forum="sub2",
        content={"title": {"value": "Second Paper"}, "venueid": "ICLR.cc/2027/Conference"},
    )

    mock_client.get_all_edges.return_value = [
        MagicMock(head="sub1"),
        MagicMock(head="sub2"),
    ]
    mock_client.get_note.side_effect = lambda sid: sub1 if sid == "sub1" else sub2

    inv = MagicMock(id="ICLR.cc/2027/Conference/Submission1/-/Official_Comment")
    inv.edit = {"note": {"readers": {"param": {"items": []}}}}
    mock_client.get_invitation.return_value = inv
    mock_client.get_groups.return_value = [
        MagicMock(id="ICLR.cc/2027/Conference/Submission1/Area_Chair_x")
    ]

    res = batch_post_forum_messages(
        venue_id="ICLR.cc/2027/Conference",
        comment="Notice for Paper #{number}: {title}",
        title="Notice #{number}",
        submissions=[1, 2],
        dry_run=True,
    )

    assert res["status"] == "success"
    assert res["dry_run"] is True
    assert res["total"] == 2
    assert len(res["results"]) == 2
    assert res["results"][0]["full_comment"] == "Notice for Paper #1: First Paper"
    assert res["results"][0]["comment_title"] == "Notice #1"
    assert res["results"][1]["full_comment"] == "Notice for Paper #2: Second Paper"
    assert res["results"][1]["comment_title"] == "Notice #2"
    mock_client.post_note_edit.assert_not_called()


def test_get_forum_message_options(mock_client):
    mock_client.profile.id = "~AC1"
    sub = MagicMock(
        id="sub_test_id",
        number=5963,
        forum="sub_test_id",
        content={"title": {"value": "Test Paper Title"}},
    )
    mock_client.get_all_edges.return_value = [MagicMock(head="sub_test_id")]
    mock_client.get_note.return_value = sub

    inv = MagicMock(
        id="ICLR.cc/2027/Conference/Submission5963/-/Official_Comment",
        cdate=1000000,
        expdate=2000000000000,
    )
    inv.edit = {
        "note": {
            "readers": {
                "param": {
                    "items": [
                        {
                            "value": "ICLR.cc/2027/Conference/Program_Chairs",
                            "optional": False,
                        },
                        {
                            "value": "ICLR.cc/2027/Conference/Submission5963/Area_Chairs",
                            "optional": True,
                        },
                    ]
                }
            }
        }
    }
    mock_client.get_invitation.return_value = inv

    # Mock get_groups for AC signature and reviewer groups
    def mock_get_groups(prefix="", **kwargs):
        if "Reviewer_" in prefix:
            return [
                MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Reviewer_1"),
                MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Reviewer_2"),
            ]
        elif "Area_Chair" in prefix:
            return [MagicMock(id="ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna")]
        return []

    mock_client.get_groups.side_effect = mock_get_groups

    options = get_forum_message_options(
        venue_id="ICLR.cc/2027/Conference",
        submission_id_or_number=5963,
    )

    assert options["venue_id"] == "ICLR.cc/2027/Conference"
    assert options["paper_number"] == 5963
    assert options["submission_id"] == "sub_test_id"
    assert options["invitation_id"] == "ICLR.cc/2027/Conference/Submission5963/-/Official_Comment"
    assert "ICLR.cc/2027/Conference/Program_Chairs" in options["mandatory_readers"]
    assert "ICLR.cc/2027/Conference/Submission5963/Reviewer_1" in options["reviewer_groups"]
    assert options["ac_signature"] == "ICLR.cc/2027/Conference/Submission5963/Area_Chair_7cna"
