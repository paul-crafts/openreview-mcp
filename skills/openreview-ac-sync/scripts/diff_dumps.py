#!/usr/bin/env python3
"""Diff two dump_ac_batch_submissions folders (older vs newer).

Reports, per paper: status changes, new notes, edited notes, and changes in
review score fields. Matches papers by forum id, so renamed files are fine.

Usage:
  python3 -I diff_dumps.py OLD_DIR NEW_DIR [--review-suffix Official_Review]
      [--score-fields rating,confidence] [--out FILE]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import dumplib as dl  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old_dir")
    ap.add_argument("new_dir")
    ap.add_argument("--review-suffix", default="Official_Review")
    ap.add_argument("--score-fields", default="rating,confidence")
    ap.add_argument("--out", help="write markdown here instead of stdout")
    a = ap.parse_args()
    fields = [f for f in a.score_fields.split(",") if f]

    old, new = dl.load_dump_dir(a.old_dir), dl.load_dump_dir(a.new_dir)
    lines = [f"# Dump diff: `{a.old_dir}` -> `{a.new_dir}`", ""]
    total_new = total_edit = total_score = 0

    for fid, p in sorted(new.items(), key=lambda kv: dl.number_of(kv[1]) or 0):
        num = dl.number_of(p)
        q = old.get(fid)
        head = f"## {num} {dl.title_of(p)[:80]}"
        out = []
        if q is None:
            out.append("- paper not in old dump")
            old_notes = {}
        else:
            if dl.venueid_of(q) != dl.venueid_of(p):
                out.append(f"- **status**: `{dl.venueid_of(q)}` -> `{dl.venueid_of(p)}`")
            old_notes = {r["id"]: r for r in q["replies"]}
        for r in sorted(p["replies"], key=lambda r: r.get("cdate") or 0):
            kind, sig = dl.inv_suffix(r), dl.signer(r)
            title = dl.value(r.get("content", {}).get("title")) or ""
            vis = "" if dl.is_reviewer_visible(r) else " [not visible to reviewers]"
            o = old_notes.get(r["id"])
            if o is None:
                total_new += 1
                out.append(f"- NEW {kind} by {sig} at {dl.ts(r.get('cdate'))}{vis}: {title}")
                continue
            if (r.get("mdate") or 0) != (o.get("mdate") or 0):
                total_edit += 1
                changes = []
                if kind == a.review_suffix:
                    for f in fields:
                        ov = dl.value(o.get("content", {}).get(f))
                        nv = dl.value(r.get("content", {}).get(f))
                        if ov != nv:
                            changes.append(f"{f} {ov} -> {nv}")
                            total_score += 1
                changed_text = [
                    k for k in r.get("content", {})
                    if dl.value(r["content"].get(k)) != dl.value(o.get("content", {}).get(k)) and k not in fields
                ]
                detail = "; ".join(changes) if changes else "no score change"
                if changed_text:
                    detail += f"; text fields changed: {', '.join(changed_text)}"
                out.append(f"- EDITED {kind} by {sig} at {dl.ts(r.get('mdate'))}: {detail}")
        lines.append(head)
        lines.extend(out or ["- no changes"])
        lines.append("")

    for fid, q in old.items():
        if fid not in new:
            lines.append(f"## {dl.number_of(q)} {dl.title_of(q)[:80]}\n- paper missing from new dump (withdrawn?)\n")

    lines.insert(1, f"\nTotals: {total_new} new notes, {total_edit} edited notes, {total_score} score-field changes.\n")
    text = "\n".join(lines)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {a.out}: {total_new} new, {total_edit} edited, {total_score} score changes")
    else:
        print(text)


if __name__ == "__main__":
    main()
