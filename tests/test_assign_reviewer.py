from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import openreview_mcp.server as server
from openreview_mcp.server import assign_reviewer

V = "TMLR"
FORUM = "paper1"
REV = "~Good_Reviewer1"
SIG = "TMLR/Paper42/Action_Editor_abcd"


def make_client(
    *,
    ae_group=True,
    in_pool=True,
    already=False,
    conflict=False,
    unavailable=False,
    pending=0,
    assigned=1,
    max_papers=6,
):
    c = MagicMock()
    c.profile.id = "~Pau_Rodriguez_Lopez1"
    c.get_note.return_value = SimpleNamespace(number=42)

    def get_groups(**kw):
        if "prefix" in kw:
            return [SimpleNamespace(id=SIG)] if ae_group else []
        return [SimpleNamespace(id=kw["id"])] if in_pool else []

    def get_edges(invitation, **kw):
        if invitation.endswith("/Assignment"):
            return [SimpleNamespace()] if already else []
        if invitation.endswith("/Conflict"):
            return [SimpleNamespace()] if conflict else []
        return []

    c.get_groups.side_effect = get_groups
    c.get_edges.side_effect = get_edges
    server._REVIEWER_LOAD_CACHE[V] = (
        __import__("time").time(),
        {
            "unavailable": {REV} if unavailable else set(),
            "pending": {REV: pending},
            "custom_max": {REV: max_papers},
            "assignments": {REV: assigned},
        },
    )
    return c


@pytest.fixture(autouse=True)
def clear_cache():
    server._REVIEWER_LOAD_CACHE.clear()
    yield
    server._REVIEWER_LOAD_CACHE.clear()


def run(client, **kw):
    with patch("openreview_mcp.server.get_client", return_value=client):
        return assign_reviewer(V, FORUM, REV, **kw)


def test_dry_run_is_default_and_never_writes():
    c = make_client()
    res = run(c)
    assert res["status"] == "dry_run"
    assert res["edge"] == {
        "invitation": "TMLR/Reviewers/-/Assignment",
        "head": FORUM,
        "tail": REV,
        "weight": 1,
        "signatures": [SIG],
    }
    assert res["checks"]["assignments"] == 1
    c.post_edge.assert_not_called()


def test_posts_edge_when_not_dry_run():
    c = make_client()
    res = run(c, dry_run=False)
    assert res["status"] == "assigned"
    c.post_edge.assert_called_once()
    edge = c.post_edge.call_args.args[0]
    assert (edge.invitation, edge.head, edge.tail, edge.weight, edge.signatures) == (
        "TMLR/Reviewers/-/Assignment", FORUM, REV, 1, [SIG],
    )
    # cached load is bumped so later checks in the same session stay accurate
    assert server._REVIEWER_LOAD_CACHE[V][1]["assignments"][REV] == 2


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"ae_group": False}, "not this paper's Action Editor"),
        ({"in_pool": False}, "not in TMLR/Reviewers"),
        ({"already": True}, "already assigned"),
        ({"conflict": True}, "conflict"),
        ({"unavailable": True}, "Unavailable"),
        ({"pending": 1}, "pending review"),
        ({"assigned": 6, "max_papers": 6}, "at quota (6/6)"),
    ],
)
def test_refusals_never_write_even_when_not_dry_run(kwargs, fragment):
    c = make_client(**kwargs)
    res = run(c, dry_run=False)
    assert res["status"] == "refused"
    assert any(fragment in r for r in res["reasons"]), res["reasons"]
    c.post_edge.assert_not_called()


def test_allow_at_quota_override():
    c = make_client(assigned=6, max_papers=6)
    assert run(c, dry_run=True)["status"] == "refused"
    res = run(c, dry_run=False, allow_at_quota=True)
    assert res["status"] == "assigned"
    c.post_edge.assert_called_once()


def test_default_quota_used_when_no_custom_edge():
    c = make_client(assigned=6)
    server._REVIEWER_LOAD_CACHE[V][1]["custom_max"].clear()
    res = run(c)
    assert res["status"] == "refused"
    assert res["checks"]["custom_max_papers"] == 6
