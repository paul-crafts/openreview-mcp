import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openreview import OpenReviewException

from openreview_mcp.server import submit_review_approval

V = "TMLR"
FORUM = "paper1"
SIG = "TMLR/Paper42/Action_Editor_abcd"
INV = "TMLR/Paper42/-/Review_Approval"


def make_client(*, venueid="TMLR/Assigned_AE", ae_group=True, existing=False,
                inv="ok", expdate=None):
    c = MagicMock()
    c.profile.id = "~Pau_Rodriguez_Lopez1"
    c.get_note.return_value = SimpleNamespace(
        number=42,
        content={"title": {"value": "A paper"}, "venueid": {"value": venueid}},
    )
    if inv == "missing":
        c.get_invitation.side_effect = OpenReviewException({"name": "NotFoundError", "status": 404})
    else:
        c.get_invitation.return_value = SimpleNamespace(
            expdate=expdate,
            edit={"note": {"content": {"under_review": {"value": {"param": {
                "enum": ["Appropriate for Review", "Desk Reject"]}}}}}},
        )
    c.get_notes.return_value = [SimpleNamespace(id="n")] if existing else []
    c.get_groups.return_value = [SimpleNamespace(id=SIG)] if ae_group else []
    return c


def run(client, **kw):
    with patch("openreview_mcp.server.get_client", return_value=client):
        return submit_review_approval(V, FORUM, **kw)


def test_dry_run_is_default_and_never_writes():
    c = make_client()
    res = run(c)
    assert res["status"] == "dry_run"
    assert res["title"] == "A paper"
    assert res["note"] == {
        "invitation": INV,
        "signatures": [SIG],
        "content": {"under_review": {"value": "Appropriate for Review"}},
    }
    assert any("expires" in e for e in res["effects"])
    c.post_note_edit.assert_not_called()


def test_posts_when_not_dry_run():
    c = make_client()
    res = run(c, dry_run=False, comment="looks fine")
    assert res["status"] == "submitted"
    kw = c.post_note_edit.call_args.kwargs
    assert kw["invitation"] == INV and kw["signatures"] == [SIG]
    assert kw["note"].content == {
        "under_review": {"value": "Appropriate for Review"},
        "comment": {"value": "looks fine"},
    }


@pytest.mark.parametrize(
    "client_kwargs, call_kwargs, fragment",
    [
        ({"venueid": "TMLR/Under_Review"}, {}, "nothing to approve"),
        ({"ae_group": False}, {}, "not this paper's Action Editor"),
        ({"existing": True}, {}, "already exists"),
        ({"inv": "missing"}, {}, "does not exist"),
        ({"expdate": 1}, {}, "expired"),
        ({}, {"decision": "Accept"}, "decision must be one of"),
        ({}, {"decision": "Desk Reject"}, "needs a comment"),
        ({}, {"decision": "Desk Reject", "comment": "   "}, "needs a comment"),
    ],
)
def test_refusals_never_write(client_kwargs, call_kwargs, fragment):
    c = make_client(**client_kwargs)
    res = run(c, dry_run=False, **call_kwargs)
    assert res["status"] == "refused"
    assert any(fragment in r for r in res["reasons"]), res["reasons"]
    c.post_note_edit.assert_not_called()


def test_desk_reject_with_comment_is_allowed():
    c = make_client()
    res = run(c, decision="Desk Reject", comment="Not anonymized.", dry_run=False)
    assert res["status"] == "submitted"
    assert any("Editors in Chief" in e for e in res["effects"])


def test_future_expdate_is_fine():
    c = make_client(expdate=int(time.time() * 1000) + 10**9)
    assert run(c)["status"] == "dry_run"
