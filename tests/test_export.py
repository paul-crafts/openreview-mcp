import os
import json
import pytest
from unittest.mock import MagicMock, patch
from openreview_mcp.server import export_venue_submissions


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_export_venue_submissions_mock(mock_client, tmp_path):
    # Mock submission note
    sub_note = MagicMock()
    sub_note.id = "paper123"
    sub_note.number = 1
    sub_note.content = {
        "title": {"value": "Sample Paper Title"},
        "authors": {"value": ["Author One", "Author Two"]},
        "abstract": {"value": "This is the sample paper abstract."},
        "venue": {"value": "ICML 2026 spotlight"},
        "venueid": {"value": "ICML.cc/2026/Conference"},
    }

    mock_client.get_all_notes.return_value = [sub_note]

    # Mock review note
    rev_note = MagicMock()
    rev_note.signatures = ["ICML.cc/2026/Conference/Submission1/Reviewer_1"]
    rev_note.content = {
        "overall_recommendation": {"value": 8},
        "soundness": {"value": 4},
        "presentation": {"value": 4},
        "confidence": {"value": 4},
    }

    mock_client.get_notes.return_value = [rev_note]

    out_file = os.path.join(tmp_path, "output.json")

    res = export_venue_submissions(
        venue_id="ICML.cc/2026/Conference",
        tab_or_venue_name="ICML 2026 spotlight",
        output_file=out_file,
        output_format="both",
    )

    assert res["status"] == "completed"
    assert res["total_papers"] == 1
    assert os.path.exists(out_file)

    with open(out_file, "r") as f:
        data = json.load(f)

    assert len(data) == 1
    assert data[0]["title"] == "Sample Paper Title"
    assert data[0]["abstract"] == "This is the sample paper abstract."
    assert data[0]["avg_overall_recommendation"] == 8
    assert data[0]["overall_recommendation_scores"] == [8]

    # Verify CSV export
    csv_file = os.path.join(tmp_path, "output.csv")
    assert os.path.exists(csv_file)
