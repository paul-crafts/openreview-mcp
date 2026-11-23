#!/usr/bin/env python3
"""Print one forum dump as a readable, chronological thread (verbatim content).

Each note shows: id, signer, type, reply-to signer, readers class, date (UTC),
then every content field verbatim. Use it to read a paper's discussion; copy
quotes from this output (it does not alter the text).

Usage:
  python3 -I print_forum.py forum_dump_2026-11-20/paper_1234_....json [--only-signer Reviewer_Rv2b]
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-sync", "scripts"))
import dumplib as dl  # noqa: E402

SKIP_FIELDS = {"pdf", "supplementary_material", "_bibtex", "venueid", "paperhash"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump_file")
    ap.add_argument("--only-signer")
    a = ap.parse_args()
    with open(a.dump_file, encoding="utf-8") as f:
        data = json.load(f)
    sub = data["submission"]
    by_id = {r["id"]: r for r in data.get("replies", [])}
    print(f"# FORUM {sub['id']}  ref={os.path.splitext(os.path.basename(a.dump_file))[0]}")
    for r in sorted(data.get("replies", []), key=lambda r: r.get("cdate") or 0):
        sig = dl.signer(r)
        if a.only_signer and sig != a.only_signer:
            continue
        parent = by_id.get(r.get("replyto"))
        to = "paper" if r.get("replyto") in (None, sub["id"]) or not parent else dl.signer(parent)
        rd = dl.readers_short(r)
        if "Reviewers" in rd:
            vis = "all reviewers+"
        elif any(x.startswith("Reviewer_") for x in rd):
            vis = "only " + ",".join(dl.short_id(x) for x in rd if x.startswith("Reviewer_")) + " (+chairs; CONFIDENTIAL to other reviewers)"
        else:
            vis = "NOT-reviewers (CONFIDENTIAL)"
        if "everyone" in dl.readers_short(r):
            vis = "public"
        print("\n" + "=" * 100)
        print(f"[{r['id']}] {sig} | {dl.inv_suffix(r)} | reply-to: {to} | readers: {vis} | {dl.ts(r.get('cdate'))} UTC")
        for k, v in r.get("content", {}).items():
            if k in SKIP_FIELDS:
                continue
            val = dl.value(v)
            if isinstance(val, (list, dict)):
                val = json.dumps(val, ensure_ascii=False)
            print(f"--- {k}:\n{val}")


if __name__ == "__main__":
    main()
