from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import openreview_mcp.server as server
from openreview_mcp.server import get_reviewer_candidates

V = "TMLR"
FORUM = "paper1"
R = f"{V}/Reviewers"


def edge(tail, weight=None, label=None, head=None):
    return SimpleNamespace(tail=tail, weight=weight, label=label, head=head)


def profile(pid, fullname, position, inst, domain, pubs=()):
    return SimpleNamespace(
        id=pid,
        content={
            "names": [{"fullname": fullname, "preferred": True}],
            "history": [
                {
                    "position": position,
                    "institution": {"name": inst, "domain": domain},
                    "end": None,
                }
            ],
            "publications": list(pubs),
        },
    )


def pub(title, cdate):
    return SimpleNamespace(content={"title": {"value": title}}, cdate=cdate, pdate=None)


@pytest.fixture(autouse=True)
def clear_cache():
    server._REVIEWER_LOAD_CACHE.clear()
    yield
    server._REVIEWER_LOAD_CACHE.clear()


@pytest.fixture
def client():
    edges = {
        # affinity (unsorted on purpose)
        f"{R}/-/Affinity_Score": [
            edge("~at_quota1", 0.97),
            edge("~pending1", 0.96),
            edge("~conflict1", 0.95),
            edge("~away1", 0.94),
            edge("~top1", 0.93),
            edge("~second1", 0.92),
            edge("~third1", 0.91),
        ],
        f"{R}/-/Conflict": [edge("~conflict1", -1, "Conflict", FORUM)],
        f"{R}/-/Assignment_Availability": [edge("~away1", None, "Unavailable")],
        f"{R}/-/Pending_Reviews": [
            edge("~pending1", 1),
            edge("~at_quota1", 0),
            edge("~top1", 0),
            edge("~second1", 0),
        ],
        f"{R}/-/Custom_Max_Papers": [edge("~at_quota1", 2), edge("~third1", 3)],
    }
    c = MagicMock()
    c.get_group.return_value = SimpleNamespace(
        members=["~at_quota1", "~pending1", "~conflict1", "~away1", "~top1", "~second1", "~third1"]
    )
    c.get_all_edges.side_effect = lambda invitation, **kw: list(edges.get(invitation, []))
    c.get_grouped_edges.return_value = [
        {"id": {"tail": "~at_quota1"}, "count": 2},
        {"id": {"tail": "~top1"}, "count": 1},
        {"id": {"tail": "~third1"}, "count": 3},  # == its custom max of 3 -> at quota
    ]
    with patch("openreview_mcp.server.get_client", return_value=c):
        yield c


@pytest.fixture
def profiles():
    ps = [
        profile("~top1", "Top One", "PhD student", "Rice University", "rice.edu",
                [pub("Old paper", 1), pub("New paper", 3), pub("new paper", 2), pub("Mid", 2)]),
        profile("~second1", "Second Two", "Professor", "MIT", "mit.edu"),
    ]
    with patch("openreview_mcp.server.openreview.tools.get_profiles", return_value=ps) as m:
        yield m


def test_matches_assignment_browser_filters(client, profiles):
    res = get_reviewer_candidates(V, FORUM, limit=10)

    # at quota (2/2), pending, conflicted, unavailable and at-quota-by-default are all hidden
    assert [r["id"] for r in res] == ["~top1", "~second1"]
    assert [r["rank"] for r in res] == [1, 2]
    top, second = res
    assert top["affinity_score"] == 0.93
    assert top["assignments"] == 1
    assert top["custom_max_papers"] == 6  # venue default when no custom edge
    assert top["at_quota"] is False
    assert top["pending_reviews"] == 0
    assert top["availability"] == "Available"
    assert second["assignments"] == 0  # absent from grouped counts == 0
    assert top["name"] == "Top One"
    assert top["position"] == "PhD student"
    assert top["institution"] == "Rice University (rice.edu)"


def test_recent_papers_deduped_newest_first(client, profiles):
    res = get_reviewer_candidates(V, FORUM, limit=1, recent_papers=2)
    assert res[0]["recent_papers"] == ["New paper", "Mid"]


def test_recent_papers_zero_skips_publications(client, profiles):
    res = get_reviewer_candidates(V, FORUM, limit=1, recent_papers=0)
    assert "recent_papers" not in res[0]
    assert profiles.call_args.kwargs["with_publications"] is False


def test_filters_can_be_relaxed(client, profiles):
    res = get_reviewer_candidates(
        V, FORUM, limit=10, max_pending_reviews=None,
        include_unavailable=True, include_at_quota=True, recent_papers=0,
    )
    ids = [r["id"] for r in res]
    # the conflicted reviewer is never returned
    assert "~conflict1" not in ids
    assert ids == ["~at_quota1", "~pending1", "~away1", "~top1", "~second1", "~third1"]
    by_id = {r["id"]: r for r in res}
    assert by_id["~at_quota1"]["at_quota"] is True
    assert by_id["~away1"]["availability"] == "Unavailable"
    assert by_id["~third1"]["at_quota"] is True


def test_limit_is_respected(client, profiles):
    res = get_reviewer_candidates(V, FORUM, limit=1)
    assert [r["id"] for r in res] == ["~top1"]


def test_no_affinity_edges_returns_empty(client):
    client.get_all_edges.side_effect = lambda invitation, **kw: []
    assert get_reviewer_candidates(V, FORUM) == []


def test_venue_load_tables_cached_across_papers(client, profiles):
    get_reviewer_candidates(V, "paperA", recent_papers=0)
    calls_after_first = client.get_all_edges.call_count
    get_reviewer_candidates(V, "paperB", recent_papers=0)

    # second paper only re-reads its own affinity + conflict edges
    assert client.get_all_edges.call_count - calls_after_first == 2
    assert client.get_grouped_edges.call_count == 1


def test_missing_journal_invitations_are_not_errors(profiles):
    from openreview import OpenReviewException

    def get_all_edges(invitation, **kw):
        if invitation.endswith("Affinity_Score"):
            return [edge("~top1", 0.9)]
        if invitation.endswith(("Pending_Reviews", "Assignment_Availability")):
            raise OpenReviewException({"name": "NotFoundError", "status": 404})
        return []

    c = MagicMock()
    c.get_all_edges.side_effect = get_all_edges
    c.get_group.return_value = SimpleNamespace(members=["~top1"])
    c.get_grouped_edges.return_value = []
    with patch("openreview_mcp.server.get_client", return_value=c):
        res = get_reviewer_candidates("ICLR.cc/2027/Conference", FORUM, recent_papers=0)
    assert [r["id"] for r in res] == ["~top1"]


def test_non_members_are_skipped(client, profiles):
    # affinity edges outlive pool membership (removed/archived reviewers)
    client.get_group.return_value = SimpleNamespace(members=["~top1", "~third1"])
    res = get_reviewer_candidates(V, FORUM, limit=10, recent_papers=0)
    assert [r["id"] for r in res] == ["~top1"]
