#!/usr/bin/env python3
"""Plan a wave of Area Chair messages: one talking-points file per paper for the drafting
subagents. The plans select material only; they never write prose.

Categories, from each dossier's lean (override with --category 5678=decide):
  decide         Borderline                     the thread asks reviewers to settle the open points
  verify-reject  Reject / Lean reject           the thread confirms the reject side and tests the accept side
  verify-accept  Accept / Lean accept           the mirror image

Wave goals: `discussion` (wave 1: discuss and work towards agreement, no verdict asked) and
`final` (a later wave: explicit accept or reject with a matching rating).

Messages per paper:
  <num>-thread          forum thread to all of the paper's reviewers
  <num>-email-<rid>     short email to each reviewer silent since the rebuttal (only if there was a rebuttal)

Each plan holds the category, wave goal, ratings, the open concerns the dossier verified as valid
(with severity, status, validity and resolution verdicts), disagreements, verified fixes a reviewer
never acknowledged, raise statements, outliers, silent reviewers, review-quality flags per reviewer
(Program Chair notes, dossier quality tags), each reviewer's latest note that all reviewers can read,
and the author replies each reviewer has not answered.

Usage:
  python3 -I plan_wave.py --dossiers <ws>/ac_discussion --dump-dir forum_dump_<date> \
      --plan-dir <ws>/ac_discussion/messages/plans --boundary 2.5 --no-author-replies \
      [--wave-goal discussion|final] [--category 5678=decide ...]
"""

import argparse
import glob
import json
import os
import re
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-sync", "scripts"))
import dashboard as db  # noqa: E402
import dumplib as dl  # noqa: E402

SEV = {"Critical": 0, "Major": 1, "Minor": 2}
OPEN = ("Unresolved", "Disputed", "Partially", "Not addressed")
STATUS_PRIORITY = {"Unresolved": 0, "Disputed": 0, "Not addressed": 1, "Partially": 2}
VALID = ("verified true", "partly true")
RAISE_RE = re.compile(r"\b(inclined|willing|consider\w*|open to|would raise|happy to raise|could raise)\b", re.I)
LEAN_CAT = {"Borderline": "decide", "Reject": "verify-reject", "Lean reject": "verify-reject",
            "Accept": "verify-accept", "Lean accept": "verify-accept"}


def open_valid(d):
    """Critical and Major concerns still open whose validity was verified (true or partly true):
    fully open first, then by severity, then by how many reviewers share them."""
    cs = [c for c in d.get("concerns", []) if c.get("severity") in ("Critical", "Major")
          and c.get("status") in OPEN and (c.get("validity") or {}).get("verdict") in VALID]
    return sorted(cs, key=lambda c: (STATUS_PRIORITY.get(c.get("status"), 3), SEV.get(c["severity"], 9),
                                     -len(c.get("raised_by", []))))


def unacknowledged_fixes(d):
    """{reviewer: [concerns they raised whose fix the AC verified in the revision, with no follow-up from them]}"""
    out = {}
    for c in sorted(d.get("concerns", []), key=lambda c: SEV.get(c.get("severity"), 9)):
        if c.get("severity") not in ("Critical", "Major"):
            continue
        if (c.get("resolution_check") or {}).get("verdict") != "verified in revision":
            continue
        if c.get("status") not in ("Resolved", "Partially"):
            continue  # a fix that is present but did not resolve the concern is not 'addressed'
        answered = {q.get("who") for q in c.get("followups", [])}
        for r in c.get("raised_by", []):
            if r not in answered:
                out.setdefault(r, []).append(c)
    return out


def load_forum(dump_dir, ref):
    with open(os.path.join(dump_dir, f"{ref}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def latest_visible_notes(data, cap=2500):
    """Each reviewer's most recent note that every reviewer of the paper can read."""
    out = {}
    for note in data.get("replies", []):
        sig = dl.signer(note)
        if not sig.startswith("Reviewer_") or not dl.visible_to_all_reviewers(note):
            continue
        c = note.get("cdate") or note.get("tcdate") or 0
        rid = dl.short_id(sig)
        if rid not in out or c > out[rid][0]:
            text = "\n".join(x for x in dl.note_strings(note) if len(x) > 3)
            out[rid] = (c, {"date": dl.ts(c), "kind": dl.inv_suffix(note),
                            "text": text[:cap] + (" [truncated: see print_forum.py]" if len(text) > cap else "")})
    return {k: v[1] for k, v in out.items()}


def unanswered(data):
    """Author replies each reviewer has not answered (posted after their latest visible comment)."""
    return {rid: [{"date": dl.ts(n.get("cdate")), "id": n["id"],
                   "start": re.sub(r"\s+", " ", " ".join(dl.note_strings(n)))[:300]} for n in notes]
            for rid, notes in dl.unanswered_author_replies(data).items()}


def chair_notes(data):
    """{reviewer: [first 200 chars of each Program Chair note that replies to their review]}"""
    by_id = {n["id"]: n for n in data.get("replies", [])}
    out = {}
    for n in data.get("replies", []):
        if dl.signer(n) != "Program_Chairs":
            continue
        p = by_id.get(n.get("replyto"))
        if p is not None and dl.signer(p).startswith("Reviewer_"):
            out.setdefault(dl.short_id(dl.signer(p)), []).append(re.sub(r"\s+", " ", " ".join(dl.note_strings(n)))[:200])
    return out


def plan_for(n, d, P, cat, a):
    data = load_forum(a.dump_dir, d["ref"])
    has_rebuttal = P.get("n_author_comments", 0) > 0

    def brief(c):
        return {"id": c["id"], "label": c["label"], "severity": c.get("severity"), "status": c.get("status"),
                "raised_by": c.get("raised_by", []), "validity": (c.get("validity") or {}).get("verdict"),
                "resolution": (c.get("resolution_check") or {}).get("verdict")}

    side = {"verify-reject": "reject", "verify-accept": "accept"}.get(cat)
    outliers = []
    if a.boundary is not None and side:
        outliers = [r["id"] for r in P.get("reviews", []) if r.get("rating") is not None and
                    ((side == "reject" and r["rating"] > a.boundary) or (side == "accept" and r["rating"] < a.boundary))]
    raise_intent = []
    for t in d.get("quality", []):
        if t.get("tag") == "conditional promise" and t.get("quotes"):
            raise_intent.append({"reviewer": t.get("reviewer"), "text": t["quotes"][0].get("text"), "condition_met": t.get("met")})
    for pr in P.get("promises", []):
        if RAISE_RE.search(pr.get("text", "")) and pr.get("reviewer") not in [x["reviewer"] for x in raise_intent]:
            raise_intent.append({"reviewer": pr.get("reviewer"), "text": pr.get("text")})
    silent = [r["id"] for r in P.get("reviews", []) if not r.get("engaged")] if has_rebuttal else []
    pcn = chair_notes(data)
    quality = {}
    for r in P.get("reviews", []):
        tags = sorted({t.get("tag") for t in d.get("quality", []) if t.get("reviewer") == r["id"] and t.get("tag")})
        if tags or pcn.get(r["id"]):
            quality[r["id"]] = {"dossier_tags": tags, "program_chair_notes": pcn.get(r["id"], [])}
    msgs = [{"id": f"{n}-thread", "type": "thread", "channel": "forum", "to": [r["id"] for r in P.get("reviews", [])]}]
    msgs += [{"id": f"{n}-email-{r}", "type": "email", "channel": "email", "to": [r]} for r in silent]
    return {
        "paper": n, "ref": d.get("ref"), "title": d.get("title"), "category": cat, "wave_goal": a.wave_goal,
        "lean": (d.get("decision") or {}).get("lean"), "boundary": a.boundary,
        "authors_can_reply": not a.no_author_replies, "has_rebuttal": has_rebuttal,
        "ratings": [{"reviewer": r["id"], "rating": r.get("rating"), "confidence": r.get("confidence"),
                     "engaged_after_rebuttal": r.get("engaged"), "pc_note": bool(r.get("pc_flags") or pcn.get(r["id"]))}
                    for r in P.get("reviews", [])],
        "open_valid_concerns": [brief(c) for c in open_valid(d)],
        "disagreements": [{"label": g.get("label"), "sides": [{"position": sd.get("position"),
                           "reviewers": sorted({q.get("who") for q in sd.get("quotes", [])
                                                if q.get("who") not in ("Authors", "pdf", "AC", "PC")})}
                           for sd in g.get("sides", [])]} for g in d.get("disagreements", [])],
        "unacknowledged_verified_fixes": {r: [c["id"] for c in cs] for r, cs in (unacknowledged_fixes(d) if has_rebuttal else {}).items()},
        "raise_intent": raise_intent, "outliers": outliers, "silent_reviewers": silent,
        "review_quality": quality,
        "latest_visible_note": latest_visible_notes(data),
        "unanswered_author_replies": unanswered(data),
        "messages_to_write": msgs,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossiers", required=True)
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--plan-dir", required=True, help="write one plan_<num>.json per paper here")
    ap.add_argument("--wave-goal", choices=["discussion", "final"], default="discussion")
    ap.add_argument("--category", nargs="*", default=[], help="overrides, e.g. 5678=decide")
    ap.add_argument("--no-author-replies", action="store_true")
    ap.add_argument("--boundary", type=float, default=None, help="rating between the reject and accept sides (venue profile)")
    ap.add_argument("--review-suffix", default="Official_Review")
    ap.add_argument("--rating-field", default="rating")
    ap.add_argument("--confidence-field", default="confidence")
    a = ap.parse_args()

    overrides = dict(x.split("=", 1) for x in a.category)
    status = {P["number"]: P for P in db.collect(SimpleNamespace(
        dump_dir=a.dump_dir, prev=[], extra_fields="", comment_suffixes="Official_Comment,Rebuttal,Author_Comment,Comment",
        review_suffix=a.review_suffix, rating_field=a.rating_field, confidence_field=a.confidence_field, boundary=None))}
    os.makedirs(a.plan_dir, exist_ok=True)
    n_plans = 0
    for f in sorted(glob.glob(os.path.join(a.dossiers, "*_dossier.json")), key=lambda p: int(os.path.basename(p).split("_")[0])):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        n = d["number"]
        P = status.get(n)
        if not P or P.get("excluded"):
            continue  # withdrawn or desk rejected: never message
        cat = overrides.get(str(n), LEAN_CAT.get((d.get("decision") or {}).get("lean"), "skip"))
        if cat == "skip":
            continue
        with open(os.path.join(a.plan_dir, f"plan_{n}.json"), "w", encoding="utf-8") as fh:
            json.dump(plan_for(n, d, P, cat, a), fh, indent=1, ensure_ascii=False)
        n_plans += 1
    print(f"wrote {n_plans} plans to {a.plan_dir}")


if __name__ == "__main__":
    main()
