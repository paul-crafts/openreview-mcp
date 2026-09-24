from unittest.mock import MagicMock, patch
import pytest

from openreview_mcp.server import (
    get_bidding_info,
    place_bid,
    get_bidding_status,
)


@pytest.fixture
def mock_client():
    with patch("openreview_mcp.server.get_client") as mock_get_client:
        client = MagicMock()
        client.profile.id = "~TestUser1"
        mock_get_client.return_value = client
        yield client


def test_get_bidding_info_bid_range(mock_client):
    """Test get_bidding_info with personalized candidate pool (Bid_Range)."""
    # 1. Mock invitation with allowed labels
    mock_inv = MagicMock()
    mock_inv.edit = {"label": {"param": {"enum": ["Unwilling"]}}}
    mock_inv.edge = {}
    mock_client.get_invitation.return_value = mock_inv

    # 2. Mock current bids
    mock_bid_edge = MagicMock(head="paper_1", label="Unwilling")
    # 3. Mock Bid_Range edges
    edge_1 = MagicMock(head="paper_1", weight=0.85)
    edge_2 = MagicMock(head="paper_2", weight=0.95)

    def mock_get_all_edges(invitation, tail):
        if "Bid_Range" in invitation:
            return [edge_1, edge_2]
        if invitation.endswith("/-/Bid"):
            return [mock_bid_edge]
        return []

    mock_client.get_all_edges.side_effect = mock_get_all_edges

    # 4. Mock notes
    note_1 = MagicMock()
    note_1.id = "paper_1"
    note_1.number = 101
    note_1.forum = "paper_1"
    note_1.content = {
        "title": {"value": "Continual Learning Paper"},
        "abstract": {"value": "A study on continual learning..."},
        "keywords": {"value": ["continual learning", "deep learning"]},
        "primary_area": {"value": "machine learning"},
        "venueid": {"value": "ICLR.cc/2027/Conference"},
    }

    note_2 = MagicMock()
    note_2.id = "paper_2"
    note_2.number = 102
    note_2.forum = "paper_2"
    note_2.content = {
        "title": {"value": "Novel Transformer Paper"},
        "abstract": {"value": "A study on transformers..."},
        "keywords": {"value": ["transformers"]},
        "primary_area": {"value": "deep learning"},
        "venueid": {"value": "ICLR.cc/2027/Conference"},
    }

    mock_client.get_notes_by_ids.return_value = [note_1, note_2]

    res = get_bidding_info(
        venue_id="ICLR.cc/2027/Conference",
        role="Area_Chairs",
        limit=10,
        offset=0,
    )

    assert res["bidding_mode"] == "bid_range"
    assert res["total_pool_size"] == 2
    assert res["allowed_bids"] == ["Unwilling"]
    assert len(res["papers"]) == 2

    # Paper 2 should come first due to higher affinity weight (0.95 > 0.85)
    first_paper = res["papers"][0]
    assert first_paper["id"] == "paper_2"
    assert first_paper["affinity_score"] == 0.95
    assert first_paper["title"] == "Novel Transformer Paper"
    assert first_paper["current_bid"] == "No Bid"

    second_paper = res["papers"][1]
    assert second_paper["id"] == "paper_1"
    assert second_paper["affinity_score"] == 0.85
    assert second_paper["current_bid"] == "Unwilling"


def test_get_bidding_info_direct_submissions(mock_client):
    """Test get_bidding_info with standard direct submission venues (e.g. CoLLAs)."""
    # 1. Mock invitation with allowed labels
    mock_inv = MagicMock()
    mock_inv.edit = {}
    mock_inv.edge = {
        "label": {
            "param": {
                "enum": ["Very High", "High", "Neutral", "Low", "Very Low"]
            }
        }
    }
    mock_client.get_invitation.return_value = mock_inv

    # No Bid_Range edges
    mock_client.get_all_edges.return_value = []

    # Mock submissions
    sub = MagicMock()
    sub.id = "collas_1"
    sub.number = 1
    sub.forum = "collas_1"
    sub.content = {
        "title": "Lifelong RL",
        "abstract": "Reinforcement learning that never forgets.",
    }
    mock_client.get_notes.return_value = [sub]

    res = get_bidding_info(venue_id="collas.org/2026/Conference", limit=10)

    assert res["bidding_mode"] == "direct_submissions"
    assert len(res["papers"]) == 1
    assert res["papers"][0]["title"] == "Lifelong RL"
    assert res["papers"][0]["abstract"] == "Reinforcement learning that never forgets."


def test_place_bid_normal(mock_client):
    """Test placing a normal bid."""
    mock_inv = MagicMock()
    mock_inv.edge = {}
    mock_inv.edit = {
        "label": {"param": {"enum": ["Unwilling"]}},
        "readers": ["ICLR.cc/2027/Conference", "${2/tail}"],
        "writers": ["ICLR.cc/2027/Conference", "${2/tail}"],
    }
    mock_client.get_invitation.return_value = mock_inv

    res = place_bid(
        venue_id="ICLR.cc/2027/Conference",
        submission_id="paper_123",
        bid="Unwilling",
        role="Area_Chairs",
    )

    assert res["status"] == "success"
    assert res["action"] == "bid"
    assert res["bid"] == "Unwilling"
    mock_client.post_edge.assert_called_once()
    posted_edge = mock_client.post_edge.call_args[0][0]
    assert posted_edge.label == "Unwilling"
    assert posted_edge.head == "paper_123"
    assert posted_edge.tail == "~TestUser1"


def test_place_bid_unbid(mock_client):
    """Test unbidding by passing 'No Bid' or 'none'."""
    res = place_bid(
        venue_id="ICLR.cc/2027/Conference",
        submission_id="paper_123",
        bid="No Bid",
        role="Area_Chairs",
    )

    assert res["status"] == "success"
    assert res["action"] == "unbid"
    assert res["bid"] == "No Bid"
    mock_client.delete_edges.assert_called_once_with(
        invitation="ICLR.cc/2027/Conference/Area_Chairs/-/Bid",
        head="paper_123",
        tail="~TestUser1",
    )


def test_place_bid_invalid_label(mock_client):
    """Test rejecting an invalid bid label."""
    mock_inv = MagicMock()
    mock_inv.edge = {}
    mock_inv.edit = {
        "label": {"param": {"enum": ["Unwilling"]}},
    }
    mock_client.get_invitation.return_value = mock_inv

    res = place_bid(
        venue_id="ICLR.cc/2027/Conference",
        submission_id="paper_123",
        bid="Very High",
        role="Area_Chairs",
    )

    assert res["status"] == "error"
    assert "Invalid bid label" in res["message"]
    mock_client.post_edge.assert_not_called()


def test_get_bidding_status_with_pool(mock_client):
    """Test get_bidding_status calculates unbid count and pool size."""
    bids = [
        MagicMock(head="p1", label="Unwilling"),
        MagicMock(head="p2", label="Unwilling"),
    ]
    pool = [MagicMock(head=f"p{i}") for i in range(10)]

    def mock_get_all_edges(invitation, tail):
        if "Bid_Range" in invitation:
            return pool
        return bids

    mock_client.get_all_edges.side_effect = mock_get_all_edges

    status = get_bidding_status("ICLR.cc/2027/Conference", role="Area_Chairs")

    assert status["total_bids"] == 2
    assert status["total_papers_in_pool"] == 10
    assert status["total_unbid"] == 8
    assert status["summary"] == {"Unwilling": 2}
