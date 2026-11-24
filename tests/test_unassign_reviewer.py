import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import openreview_mcp.server as server
from openreview_mcp.server import unassign_reviewer

V, FORUM, REV = "TMLR", "paper1", "~Good_Reviewer1"
SIG = "TMLR/Paper42/Action_Editor_abcd"


def make_client(*, ae_group=True, assigned=True, review_by_reviewer=False, other_reviews=False):
    c = MagicMock()
    c.profile.id = "~Pau_Rodriguez_Lopez1"
    c.get_note.return_value = SimpleNamespace(number=42)
    edge = SimpleNamespace(ddate=None, signatures=["TMLR"], tail=REV)
    c.get_edges.return_value = [edge] if assigned else []
    c.edge = edge

    def get_groups(prefix, signatory):
        if "Action_Editor_" in prefix:
            return [SimpleNamespace(id=SIG)] if ae_group else []
        return [SimpleNamespace(id="TMLR/Paper42/Reviewer_zzzz")]

    c.get_groups.side_effect = get_groups
    reviews = []
    if review_by_reviewer:
        reviews = [SimpleNamespace(signatures=["TMLR/Paper42/Reviewer_zzzz"])]
    elif other_reviews:
        reviews = [SimpleNamespace(signatures=["TMLR/Paper42/Reviewer_other"])]
    c.get_notes.return_value = reviews
    return c


def run(client, **kw):
    with patch("openreview_mcp.server.get_client", return_value=client):
        return unassign_reviewer(V, FORUM, REV, **kw)


@pytest.fixture(autouse=True)
def clear_cache():
    server._REVIEWER_LOAD_CACHE.clear()
    yield
    server._REVIEWER_LOAD_CACHE.clear()


def test_dry_run_default_never_writes():
    c = make_client()
    res = run(c)
    assert res["status"] == "dry_run"
    assert res["edge"]["signatures"] == [SIG]
    c.post_edge.assert_not_called()
    assert c.edge.ddate is None


def test_deletes_by_setting_ddate_and_ae_signature():
    c = make_client()
    server._REVIEWER_LOAD_CACHE[V] = (time.time(), {"assignments": {REV: 3}})
    before = int(time.time() * 1000)
    res = run(c, dry_run=False)
    assert res["status"] == "unassigned"
    c.post_edge.assert_called_once_with(c.edge)
    assert c.edge.ddate >= before
    assert c.edge.signatures == [SIG]
    assert server._REVIEWER_LOAD_CACHE[V][1]["assignments"][REV] == 2


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"ae_group": False}, "not this paper's Action Editor"),
        ({"assigned": False}, "not currently assigned"),
        ({"review_by_reviewer": True}, "already posted a review"),
    ],
)
def test_refusals_never_write(kwargs, fragment):
    c = make_client(**kwargs)
    res = run(c, dry_run=False)
    assert res["status"] == "refused"
    assert any(fragment in r for r in res["reasons"]), res["reasons"]
    c.post_edge.assert_not_called()


def test_someone_elses_review_does_not_block():
    c = make_client(other_reviews=True)
    assert run(c)["status"] == "dry_run"
