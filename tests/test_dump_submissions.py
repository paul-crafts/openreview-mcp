import json
import os
import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import dump_ac_batch_submissions


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def _make_note(id_, number, title, venueid_value="", extra_content=None):
    note = MagicMock()
    note.id = id_
    note.number = number
    content = {"title": {"value": title}, "venueid": {"value": venueid_value}}
    if extra_content:
        content.update(extra_content)
    note.content = content
    note.to_json.return_value = {"id": id_, "number": number, "content": content}
    return note


def _make_reply(id_, invitations, signatures=None):
    reply = MagicMock()
    reply.id = id_
    reply.invitations = invitations
    reply.signatures = signatures or ["~Reviewer1"]
    reply.to_json.return_value = {
        "id": id_,
        "invitations": invitations,
        "signatures": reply.signatures,
        "content": {"summary": {"value": f"content for {id_}"}},
    }
    return reply


def test_dump_excludes_withdrawn(mock_client, tmp_path):
    mock_client.profile.id = "~Test_AC1"

    edge1, edge2 = MagicMock(head="paper1"), MagicMock(head="paper2")
    mock_client.get_all_edges.return_value = [edge1, edge2]

    active_note = _make_note("paper1", 1, "Active Paper")
    withdrawn_note = _make_note(
        "paper2",
        2,
        "Withdrawn Paper",
        venueid_value="test.venue/2026/Conference/Withdrawn_Submission",
    )
    mock_client.get_note.side_effect = [active_note, withdrawn_note]

    reply = _make_reply(
        "r1", ["test.venue/2026/Conference/Submission1/-/Official_Review"]
    )
    mock_client.get_all_notes.return_value = [reply]

    output_dir = os.path.join(tmp_path, "dumps")

    with patch("time.sleep"):
        res = dump_ac_batch_submissions(
            venue_id="test.venue/2026/Conference", output_dir=output_dir, delay=0.1
        )

    assert res["status"] == "completed"
    assert len(res["dumped"]) == 1
    assert res["dumped"][0]["number"] == 1
    assert len(res["excluded_withdrawn"]) == 1
    assert res["excluded_withdrawn"][0]["id"] == "paper2"
    assert (
        res["excluded_withdrawn"][0]["venueid"]
        == "test.venue/2026/Conference/Withdrawn_Submission"
    )

    # The withdrawn paper's forum must never even be fetched.
    assert mock_client.get_all_notes.call_count == 1

    files = os.listdir(output_dir)
    assert len(files) == 1
    assert files[0].endswith(".json")


def test_dump_writes_raw_replies_and_tally_without_filtering(mock_client, tmp_path):
    mock_client.profile.id = "~Test_AC1"
    mock_client.get_all_edges.return_value = [MagicMock(head="paper1")]
    note = _make_note("paper1", 7, "Some Paper")
    mock_client.get_note.side_effect = [note]

    review_a = _make_reply(
        "r1",
        ["test.venue/2026/Conference/Submission7/-/Official_Review"],
        signatures=["test.venue/2026/Conference/Submission7/Reviewer_xuxJ"],
    )
    review_b = _make_reply(
        "r2",
        ["test.venue/2026/Conference/Submission7/-/Official_Review"],
        signatures=["~Reviewer2"],
    )
    comment = _make_reply(
        "r3", ["test.venue/2026/Conference/Submission7/-/Official_Comment"]
    )
    mock_client.get_all_notes.return_value = [review_a, review_b, comment]

    output_dir = os.path.join(tmp_path, "dumps")

    with patch("time.sleep"):
        res = dump_ac_batch_submissions(
            venue_id="test.venue/2026/Conference", output_dir=output_dir
        )

    entry = res["dumped"][0]
    assert entry["note_count"] == 3
    assert entry["reply_invitation_tally"] == {
        "Official_Review": 2,
        "Official_Comment": 1,
    }

    # Nothing filtered: the raw file must contain all 3 replies verbatim.
    with open(entry["file_path"]) as f:
        data = json.load(f)
    assert len(data["replies"]) == 3
    assert data["submission"]["id"] == "paper1"
    ids_in_file = {r["id"] for r in data["replies"]}
    assert ids_in_file == {"r1", "r2", "r3"}


def test_dump_partial_failure_continues_batch(mock_client, tmp_path):
    mock_client.profile.id = "~Test_AC1"
    mock_client.get_all_edges.return_value = [
        MagicMock(head="paper1"),
        MagicMock(head="paper2"),
    ]
    note1 = _make_note("paper1", 1, "Paper One")
    note2 = _make_note("paper2", 2, "Paper Two")
    mock_client.get_note.side_effect = [note1, note2]

    ok_reply = _make_reply("r1", ["v/-/Official_Review"])
    mock_client.get_all_notes.side_effect = [
        Exception("boom"),
        [ok_reply],
    ]

    output_dir = os.path.join(tmp_path, "dumps")

    with patch("time.sleep"):
        res = dump_ac_batch_submissions(venue_id="v", output_dir=output_dir, delay=0.1)

    assert len(res["failed"]) == 1
    assert res["failed"][0]["id"] == "paper1"
    assert "boom" in res["failed"][0]["error"]
    assert len(res["dumped"]) == 1
    assert res["dumped"][0]["id"] == "paper2"
