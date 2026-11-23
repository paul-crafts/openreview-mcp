"""Shared helpers for reading dump_ac_batch_submissions output (stdlib only).

Each dump file is `<dir>/<ref>.json` with shape {"submission": {...}, "replies": [...]}.
Nothing here assumes a venue-specific review form: callers pass the invitation
suffix and field names (see the venue profile).
"""

import datetime as _dt
import glob
import json
import os


def load_dump_dir(dump_dir):
    """Return {forum_id: {"ref", "path", "submission", "replies"}} for a dump folder."""
    papers = {}
    for path in sorted(glob.glob(os.path.join(dump_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "submission" not in data:
            continue
        sub = data["submission"]
        papers[sub["id"]] = {
            "ref": os.path.splitext(os.path.basename(path))[0],
            "path": path,
            "submission": sub,
            "replies": data.get("replies", []),
        }
    return papers


def value(field):
    """OpenReview v2 content fields are {"value": x}; tolerate bare values."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def number_of(paper):
    sub = paper["submission"]
    if sub.get("number") is not None:
        return sub["number"]
    # The number is not always serialized; fall back to the ref "paper_<n>_...".
    parts = paper["ref"].split("_")
    if len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    for inv in sub.get("invitations", []):
        for piece in inv.split("/"):
            if piece.startswith("Submission") and piece[10:].isdigit():
                return int(piece[10:])
    return None


def title_of(paper):
    return value(paper["submission"].get("content", {}).get("title")) or ""


def venueid_of(paper):
    return value(paper["submission"].get("content", {}).get("venueid")) or ""


def is_excluded(paper):
    """Withdrawn or desk-rejected, judged from venueid AND from forum notes."""
    vid = venueid_of(paper).lower()
    if "withdrawn" in vid or "desk_reject" in vid:
        return True
    for r in paper["replies"]:
        if inv_suffix(r) in ("Desk_Rejection", "Withdrawal"):
            return True
    return False


def inv_suffix(note):
    invs = note.get("invitations") or [note.get("invitation", "")]
    return invs[0].split("/-/")[-1] if invs and invs[0] else ""


def signer(note):
    """Short signer label: 'Reviewer_Rv2b', 'Authors', 'Area_Chair_AbCd', 'Program_Chairs'."""
    sigs = note.get("signatures") or [""]
    return sigs[0].split("/")[-1]


def short_id(sig):
    """'Reviewer_Rv2b' -> 'Rv2b'; other signers are returned unchanged."""
    return sig[len("Reviewer_"):] if sig.startswith("Reviewer_") else sig


def readers_short(note):
    return [r.split("/")[-1] for r in note.get("readers", [])]


def is_reviewer_visible(note):
    rd = readers_short(note)
    return "everyone" in rd or "Reviewers" in rd or any(r.startswith("Reviewer_") for r in rd)


def note_strings(note):
    """All string leaves of a note's content, in order."""
    out = []

    def walk(o):
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(note.get("content", {}))
    return out


def ts(ms):
    if not ms:
        return ""
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def visible_to_all_reviewers(note):
    rd = readers_short(note)
    return "everyone" in rd or "Reviewers" in rd


def unanswered_author_replies(data):
    """{reviewer id: [author notes]} for author notes that all reviewers can read, that reply
    (directly or further down the thread) to that reviewer's notes, and that were posted after
    the reviewer's latest note all reviewers can read. Oldest first."""
    notes = data.get("replies", [])
    by_id = {n["id"]: n for n in notes}
    last = {}
    for n in notes:
        sig = signer(n)
        if sig.startswith("Reviewer_") and visible_to_all_reviewers(n):
            rid = short_id(sig)
            last[rid] = max(last.get(rid, 0), n.get("cdate") or 0)
    out = {}
    for n in notes:
        if signer(n) != "Authors" or not visible_to_all_reviewers(n):
            continue
        p, hops = by_id.get(n.get("replyto")), 0
        while p is not None and not signer(p).startswith("Reviewer_") and hops < 50:
            p, hops = by_id.get(p.get("replyto")), hops + 1
        if p is None:
            continue
        rid = short_id(signer(p))
        if (n.get("cdate") or 0) > last.get(rid, 0):
            out.setdefault(rid, []).append(n)
    for rid in out:
        out[rid].sort(key=lambda x: x.get("cdate") or 0)
    return out
