#!/usr/bin/env python3
"""Build the auto-generated part of the AC batch dashboard from a dump folder.

Writes between <!-- AUTO:BEGIN --> and <!-- AUTO:END --> in the output file and
keeps everything outside the markers (the AC's own notes) untouched.

All text it shows from the forum is verbatim (sentences or prefixes ending in
"..."), never paraphrased. Times are UTC.

Usage:
  python3 -I dashboard.py DUMP_DIR --out ac_discussion/00_dashboard.md
      [--prev OLDER_DUMP ...]            # chronological, for score trajectories
      [--review-suffix Official_Review] [--rating-field rating]
      [--confidence-field confidence] [--extra-fields soundness,presentation]
      [--boundary 2.5]                   # rating between reject and accept side
"""

import argparse
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import dumplib as dl  # noqa: E402

BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"

PROMISE_RE = re.compile(
    r"\b(rais\w*|increas\w*|lower\w*|decreas\w*|upgrad\w*|downgrad\w*|chang\w*|updat\w*|revis\w*|adjust\w*|bump\w*|keep\w*|maintain\w*|stick\w*)\b"
    r"[^.\n]{0,40}\b(my|the|our|your|this)\b[^.\n]{0,25}\b(score|rating)s?\b"
    r"|\b(score|rating)s?\b[^.\n]{0,20}\b(to|from|of)\s+\d",
    re.I,
)
SENT_RE = re.compile(r"[^\n.!?]*[.!?]?", re.S)


def sentences_matching(text, rx, limit=400):
    out = []
    for m in SENT_RE.finditer(text):
        s = m.group(0).strip()
        if s and rx.search(s):
            out.append(s if len(s) <= limit else s[:limit] + "...")
    return out


def prefix(text, n=300):
    """Verbatim prefix with runs of whitespace collapsed (keeps markdown lists intact)."""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n] + "..."


def tier(ratings, boundary):
    if boundary is None or not ratings:
        return ""
    acc = [r > boundary for r in ratings]
    mean = statistics.mean(ratings)
    if all(acc):
        return "clear accept" if mean >= boundary + 1 else "lean accept"
    if not any(acc):
        return "clear reject" if mean <= boundary - 1 else "lean reject"
    return "contested"


def num(v):
    try:
        return float(str(v).split(":")[0].strip())
    except (TypeError, ValueError):
        return None


def fmt(x):
    if x is None:
        return "-"
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def collect(a):
    """Structured per-paper status from the dump (shared by the markdown and HTML dashboards)."""
    cur = dl.load_dump_dir(a.dump_dir)
    prevs = [dl.load_dump_dir(p) for p in a.prev]
    extra = [f for f in a.extra_fields.split(",") if f]
    comment_kinds = set(a.comment_suffixes.split(","))
    papers = []
    for fid, p in sorted(cur.items(), key=lambda kv: dl.number_of(kv[1]) or 0):
        n, title, reps = dl.number_of(p), dl.title_of(p), p["replies"]
        P = {"number": n, "title": title, "ref": p["ref"], "forum": fid,
             "url": f"https://openreview.net/forum?id={fid}", "venueid": dl.venueid_of(p),
             "excluded": dl.is_excluded(p)}
        papers.append(P)
        if P["excluded"]:
            continue
        by_id = {r["id"]: r for r in reps}
        reviews = [r for r in reps if dl.inv_suffix(r) == a.review_suffix]
        author_notes = [r for r in reps if dl.signer(r) == "Authors" and dl.inv_suffix(r) in comment_kinds]
        rebuttal_start = min((r["cdate"] for r in author_notes), default=None)
        revs, promises = [], []
        for rv in sorted(reviews, key=lambda r: dl.signer(r)):
            sig = dl.signer(rv)
            rid = dl.short_id(sig)
            c = rv.get("content", {})
            rating = num(dl.value(c.get(a.rating_field)))
            traj = []
            for pv in prevs:
                old = pv.get(fid)
                if old:
                    o = next((r for r in old["replies"] if r["id"] == rv["id"]), None)
                    if o is not None:
                        traj.append(num(dl.value(o.get("content", {}).get(a.rating_field))))
            traj.append(rating)
            traj = [t for i, t in enumerate(traj) if i == 0 or t != traj[i - 1]]
            mine = [r for r in reps if dl.signer(r) == sig and r["id"] != rv["id"]]
            after = [r for r in mine if rebuttal_start and r["cdate"] > rebuttal_start]
            last = max((r["cdate"] for r in mine), default=None)
            flags = [
                dl.value(r.get("content", {}).get("title")) or dl.inv_suffix(r)
                for r in reps
                if r.get("replyto") == rv["id"] and dl.signer(r) == "Program_Chairs"
            ]
            revs.append({
                "id": rid, "rating": rating, "trajectory": traj,
                "confidence": num(dl.value(c.get(a.confidence_field))),
                "extra": {f: num(dl.value(c.get(f))) for f in extra},
                "edited_after_rebuttal": bool(rebuttal_start and (rv.get("mdate") or 0) > rebuttal_start),
                "n_comments": len(mine), "last_comment": dl.ts(last),
                "engaged": bool(after), "pc_flags": flags,
            })
            seen = set()
            for note in [rv] + mine:
                for s in dl.note_strings(note):
                    for sent in sentences_matching(s, PROMISE_RE):
                        if (rid, sent) not in seen:
                            seen.add((rid, sent))
                            promises.append({"reviewer": rid, "kind": dl.inv_suffix(note), "text": sent})
        acs = [r for r in reps if dl.signer(r).startswith("Area_Chair") and dl.inv_suffix(r) in comment_kinds]
        conf = []
        for r in author_notes:
            if dl.is_reviewer_visible(r):
                continue
            parent = by_id.get(r.get("replyto"))
            conf.append({
                "date": dl.ts(r["cdate"]),
                "to": dl.signer(parent) if parent and parent["id"] != fid else "",
                "title": dl.value(r.get("content", {}).get("title")) or "(no title)",
                "prefix": prefix(dl.value(r.get("content", {}).get("comment")) or ""),
            })
        ratings = [r["rating"] for r in revs if r["rating"] is not None]
        P.update({
            "reviews": revs, "ratings": ratings,
            "mean": statistics.mean(ratings) if ratings else None,
            "spread": (max(ratings) - min(ratings)) if ratings else None,
            "tier": tier(ratings, a.boundary),
            "n_author_comments": len(author_notes), "rebuttal_start": dl.ts(rebuttal_start),
            "n_ac_comments": len(acs), "last_ac_comment": dl.ts(max((r["cdate"] for r in acs), default=None)),
            "promises": promises, "confidential": conf,
            "silent": [r["id"] for r in revs if not r["engaged"]],
            "flagged": [r["id"] for r in revs if r["pc_flags"]],
        })
    return papers


def build(a):
    extra = [f for f in a.extra_fields.split(",") if f]
    summary, sections = [], []
    for P in collect(a):
        n, title, url = P["number"], P["title"], P["url"]
        if P["excluded"]:
            summary.append(f"| {n} | {title[:50]} | EXCLUDED (`{P['venueid']}`) | | | | | |")
            continue
        rows = []
        for r in P["reviews"]:
            rows.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["id"], " -> ".join(fmt(t) for t in r["trajectory"]), fmt(r["confidence"]),
                " / ".join(fmt(r["extra"][f]) for f in extra) or "-",
                "yes" if r["edited_after_rebuttal"] else "no", r["n_comments"], r["last_comment"] or "-",
                "yes" if r["engaged"] else "**NO**", "; ".join(r["pc_flags"]) or "-"))
        rs = "/".join(fmt(x) for x in P["ratings"])
        summary.append(
            f"| {n} | [{title[:50]}]({url}) | {rs} | {fmt(P['mean'])} | {P['tier']} "
            f"| {', '.join(P['silent']) or '-'} | {', '.join(P['flagged']) or '-'} | {len(P['confidential'])} |"
        )
        sec = [f"### {n}: {title}", "", f"Forum: {url}  ",
               f"Ratings: {rs} | mean {fmt(P['mean'])} | spread {fmt(P['spread'])} | tier {P['tier'] or '-'} (heuristic)  ",
               f"Author comments: {P['n_author_comments']} (first {P['rebuttal_start'] or '-'}) | AC comments: {P['n_ac_comments']} (last {P['last_ac_comment'] or '-'})",
               "",
               f"| reviewer | {a.rating_field} | {a.confidence_field} | {' / '.join(extra) or 'extra'} | review edited after rebuttal | own comments | last comment | engaged after rebuttal | PC notes on review |",
               "|---|---|---|---|---|---|---|---|---|", *rows, ""]
        if P["promises"]:
            sec.append("Score-change sentences (verbatim):")
            sec += [f"- {x['reviewer']} ({x['kind']}): \"{x['text']}\"" for x in P["promises"]]
            sec.append("")
        if P["confidential"]:
            sec.append("Confidential author notes (not visible to reviewers; verbatim prefix, whitespace collapsed):")
            for x in P["confidential"]:
                to = f" (reply to {x['to']})" if x["to"] else ""
                sec.append(f"- {x['date']}{to} **{x['title']}**: \"{x['prefix']}\"")
            sec.append("")
        sections.append("\n".join(sec))
    head = [
        BEGIN,
        f"_Auto-generated by dashboard.py from `{a.dump_dir}`. Times UTC. Edit outside the AUTO markers only._",
        "",
        "| # | title | ratings | mean | tier | silent after rebuttal | PC-flagged reviews | confidential author notes |",
        "|---|---|---|---|---|---|---|---|",
        *summary,
        "",
        *sections,
        END,
    ]
    return "\n".join(head)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev", nargs="*", default=[])
    ap.add_argument("--review-suffix", default="Official_Review")
    ap.add_argument("--comment-suffixes", default="Official_Comment,Rebuttal,Author_Comment,Comment")
    ap.add_argument("--rating-field", default="rating")
    ap.add_argument("--confidence-field", default="confidence")
    ap.add_argument("--extra-fields", default="")
    ap.add_argument("--boundary", type=float, default=None)
    a = ap.parse_args()

    auto = build(a)
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            old = f.read()
        if BEGIN in old and END in old:
            pre, rest = old.split(BEGIN, 1)
            post = rest.split(END, 1)[1]
            text = pre + auto + post
        else:
            text = old.rstrip() + "\n\n" + auto + "\n"
    else:
        text = "# AC batch dashboard\n\n## AC notes and next actions\n\n(Edit freely; this part is kept on regeneration.)\n\n## Status\n\n" + auto + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
