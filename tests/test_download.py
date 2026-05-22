import os
import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import download_batch_pdfs


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
