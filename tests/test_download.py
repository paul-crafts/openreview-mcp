import os
import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import (
    download_batch_pdfs,
    download_submission_attachments,
    download_batch_attachments,
)


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_download_batch_pdfs_success(mock_client, tmp_path):
    # Mock profiles
    mock_client.profile.id = "~Test_AC1"

    # Mock assignments (2 papers)
    mock_edge1 = MagicMock()
    mock_edge1.head = "paper1_id"
    mock_edge2 = MagicMock()
    mock_edge2.head = "paper2_id"
    mock_client.get_all_edges.return_value = [mock_edge1, mock_edge2]

    # Mock notes
    mock_note1 = MagicMock()
    mock_note1.id = "paper1_id"
    mock_note1.number = 1
    mock_note1.content = {"title": {"value": "Title of Paper One"}}

    mock_note2 = MagicMock()
    mock_note2.id = "paper2_id"
    mock_note2.number = 2
    mock_note2.content = {"title": {"value": "Title of Paper Two?"}}

    mock_client.get_note.side_effect = [mock_note1, mock_note2]

    # Mock PDF binary content
    mock_client.get_pdf.side_effect = [
        b"%PDF-1.4 paper 1 content",
        b"%PDF-1.4 paper 2 content",
    ]

    # Target directory using pytest's tmp_path
    output_dir = os.path.join(tmp_path, "downloads")

    with patch("time.sleep") as mock_sleep:
        res = download_batch_pdfs(
            venue_id="test.venue/2026/Conference",
            role="ac",
            output_dir=output_dir,
            delay=1.5,
        )

        assert res["status"] == "completed"
        assert len(res["downloaded"]) == 2
        assert len(res["failed"]) == 0
        assert res["downloaded"][0]["number"] == 1
        assert res["downloaded"][1]["number"] == 2

        # Verify rate-limiting sleep was called once (for the second paper)
        mock_sleep.assert_called_once_with(1.5)

        # Verify files are actually written to tmp_path
        files = os.listdir(output_dir)
        assert len(files) == 2
        assert "paper_1_Title_of_Paper_One.pdf" in files
        assert "paper_2_Title_of_Paper_Two.pdf" in files

        # Verify content
        with open(
            os.path.join(output_dir, "paper_1_Title_of_Paper_One.pdf"), "rb"
        ) as f:
            assert f.read() == b"%PDF-1.4 paper 1 content"


def test_download_batch_pdfs_partial_failure(mock_client, tmp_path):
    mock_client.profile.id = "~Test_Reviewer1"

    # Mock assignments (2 papers)
    mock_edge1 = MagicMock()
    mock_edge1.head = "paper1_id"
    mock_edge2 = MagicMock()
    mock_edge2.head = "paper2_id"
    mock_client.get_all_edges.return_value = [mock_edge1, mock_edge2]

    # Mock notes
    mock_note1 = MagicMock()
    mock_note1.id = "paper1_id"
    mock_note1.number = 1
    mock_note1.content = {"title": {"value": "Paper One"}}

    mock_note2 = MagicMock()
    mock_note2.id = "paper2_id"
    mock_note2.number = 2
    mock_note2.content = {"title": {"value": "Paper Two"}}

    mock_client.get_note.side_effect = [mock_note1, mock_note2]

    # Mock PDF download (first succeeds, second fails)
    mock_client.get_pdf.side_effect = [
        b"content 1",
        Exception("Rate limited or connection error"),
    ]

    output_dir = os.path.join(tmp_path, "downloads_partial")

    with patch("time.sleep"):
        res = download_batch_pdfs(
            venue_id="test.venue/2026/Conference",
            role="reviewers",
            output_dir=output_dir,
            delay=0.1,
        )

        assert res["status"] == "completed"
        assert len(res["downloaded"]) == 1
        assert len(res["failed"]) == 1
        assert res["downloaded"][0]["id"] == "paper1_id"
        assert res["failed"][0]["id"] == "paper2_id"
        assert "Rate limited or connection error" in res["failed"][0]["error"]

        # Verify files written
        files = os.listdir(output_dir)
        assert len(files) == 1
        assert "paper_1_Paper_One.pdf" in files


def test_download_submission_attachments_by_url_and_id(mock_client, tmp_path):
    mock_note = MagicMock()
    mock_note.id = "TmGjiyXgaq"
    mock_note.number = 1121
    mock_note.content = {
        "title": {"value": "SynthGD: Leveraging Image Generation"},
        "venueid": {"value": "thecvf.com/WACV/2027/Conference_Round_2"},
        "rebuttal": {"value": "/attachment/210dfa224e0f4c866fb6ecf0215ee6ed926d55ff.pdf"},
        "supplementary_material": {"value": "/attachment/1526d914533eaeaa178aaa457d560c05898eb10c.pdf"},
        "pdf": {"value": "/pdf/afa3aab82c6e84245d69040f981ad97054a203be.pdf"},
    }
    mock_client.get_note.return_value = mock_note
    mock_client.get_all_notes.return_value = [mock_note]
    mock_client.get_attachment.return_value = b"%PDF-1.5 Rebuttal content"

    output_dir = os.path.join(tmp_path, "test_att")
    url = "https://openreview.net/forum?id=TmGjiyXgaq&referrer=%5BReviewers%20Console%5D(%2Fgroup%3Fid%3Dthecvf.com)"

    res = download_submission_attachments(
        submission_id_or_url=url,
        attachment_type="rebuttal",
        output_dir=output_dir,
    )

    assert res["status"] == "completed"
    assert res["submission_id"] == "TmGjiyXgaq"
    assert res["number"] == 1121
    assert len(res["downloaded"]) == 1
    assert res["downloaded"][0]["field_name"] == "rebuttal"
    assert "rebuttal" in res["downloaded"][0]["filename"]

    # Verify file content
    saved_file = res["downloaded"][0]["file_path"]
    with open(saved_file, "rb") as f:
        assert f.read() == b"%PDF-1.5 Rebuttal content"

    # Verify call to client.get_attachment
    mock_client.get_attachment.assert_called_once_with("rebuttal", id="TmGjiyXgaq")


def test_download_submission_attachments_all(mock_client, tmp_path):
    mock_note = MagicMock()
    mock_note.id = "TmGjiyXgaq"
    mock_note.number = 1121
    mock_note.content = {
        "title": {"value": "SynthGD"},
        "venueid": {"value": "thecvf.com/WACV/2027/Conference_Round_2"},
        "rebuttal": {"value": "/attachment/rebuttal.pdf"},
        "supplementary_material": {"value": "/attachment/supp.zip"},
    }
    mock_client.get_note.return_value = mock_note
    mock_client.get_all_notes.return_value = [mock_note]
    mock_client.get_attachment.side_effect = [
        b"%PDF rebuttal",
        b"PK zip content",
    ]

    output_dir = os.path.join(tmp_path, "all_att")
    res = download_submission_attachments(
        submission_id_or_url="TmGjiyXgaq",
        attachment_type="all",
        output_dir=output_dir,
    )

    assert res["status"] == "completed"
    assert len(res["downloaded"]) == 2
    filenames = [d["filename"] for d in res["downloaded"]]
    assert any("rebuttal" in fn and fn.endswith(".pdf") for fn in filenames)
    assert any("supplementary_material" in fn and fn.endswith(".zip") for fn in filenames)


def test_download_submission_attachments_from_reply(mock_client, tmp_path):
    # Main submission without rebuttal
    mock_sub = MagicMock()
    mock_sub.id = "sub1"
    mock_sub.number = 42
    mock_sub.content = {"title": {"value": "Paper with Reply Rebuttal"}}

    # Reply note containing an author rebuttal attachment
    mock_reply = MagicMock()
    mock_reply.id = "reply_rebuttal_123"
    mock_reply.invitations = ["NeurIPS.cc/2026/Conference/Submission42/-/Author_Rebuttal"]
    mock_reply.content = {
        "pdf": {"value": "/attachment/author_rebuttal.pdf"}
    }

    mock_client.get_note.return_value = mock_sub
    mock_client.get_all_notes.return_value = [mock_sub, mock_reply]
    mock_client.get_attachment.return_value = b"%PDF reply rebuttal"

    output_dir = os.path.join(tmp_path, "reply_att")
    res = download_submission_attachments(
        submission_id_or_url="sub1",
        attachment_type="rebuttal",
        output_dir=output_dir,
        include_replies=True,
    )

    assert res["status"] == "completed"
    assert len(res["downloaded"]) == 1
    assert res["downloaded"][0]["note_id"] == "reply_rebuttal_123"
    mock_client.get_attachment.assert_called_once_with("pdf", id="reply_rebuttal_123")


def test_download_submission_attachments_not_found(mock_client, tmp_path):
    mock_note = MagicMock()
    mock_note.id = "paper_no_att"
    mock_note.number = 5
    mock_note.content = {"title": {"value": "No attachments here"}}
    mock_client.get_note.return_value = mock_note
    mock_client.get_all_notes.return_value = [mock_note]

    res = download_submission_attachments(
        submission_id_or_url="paper_no_att",
        attachment_type="rebuttal",
    )

    assert res["status"] == "not_found"
    assert len(res["downloaded"]) == 0
    assert "No attachments matching type 'rebuttal'" in res["message"]


def test_download_batch_attachments_success(mock_client, tmp_path):
    mock_client.profile.id = "~Test_Reviewer1"

    mock_edge1 = MagicMock()
    mock_edge1.head = "paper1_id"
    mock_edge2 = MagicMock()
    mock_edge2.head = "paper2_id"
    mock_client.get_all_edges.return_value = [mock_edge1, mock_edge2]

    mock_note1 = MagicMock()
    mock_note1.id = "paper1_id"
    mock_note1.number = 1
    mock_note1.content = {
        "title": {"value": "Paper 1"},
        "rebuttal": {"value": "/attachment/p1_reb.pdf"},
    }

    mock_note2 = MagicMock()
    mock_note2.id = "paper2_id"
    mock_note2.number = 2
    mock_note2.content = {
        "title": {"value": "Paper 2"},
        "rebuttal": {"value": "/attachment/p2_reb.pdf"},
    }

    mock_client.get_note.side_effect = [
        mock_note1,  # _get_assigned_submissions paper1
        mock_note2,  # _get_assigned_submissions paper2
        mock_note1,  # download_submission_attachments paper1
        mock_note2,  # download_submission_attachments paper2
    ]
    mock_client.get_all_notes.side_effect = [
        [mock_note1],
        [mock_note2],
    ]
    mock_client.get_attachment.side_effect = [
        b"%PDF rebuttal 1",
        b"%PDF rebuttal 2",
    ]

    output_dir = os.path.join(tmp_path, "batch_rebuttals")

    with patch("time.sleep") as mock_sleep:
        res = download_batch_attachments(
            venue_id="test.venue/2026/Conference",
            role="reviewers",
            attachment_type="rebuttal",
            output_dir=output_dir,
            delay=2.0,
        )

        assert res["status"] == "completed"
        assert len(res["downloaded"]) == 2
        assert len(res["failed"]) == 0
        mock_sleep.assert_called_once_with(2.0)

