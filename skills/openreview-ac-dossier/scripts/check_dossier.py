#!/usr/bin/env python3
"""Structural consistency checks for dossier JSON files (complements verify_quotes.py).

verify_quotes.py proves each quote is real; this script checks that each verdict is
backed by the right kind of evidence:
- every raising reviewer has at least one verbatim quote in the concern;
- Critical/Major concerns have a validity verdict other than "not checked", and a
  resolution check other than "not verified" when a fix is claimed;
- validity evidence comes from the ORIGINAL submission or an author concession, not
  only from revision-only text (that belongs to resolution_check);
- resolution "n/a" is not used when the authors claim a revision or a new experiment;
- "promise only" quotes the promise; "verified in revision" quotes the revised PDF;
  "rebuttal only" quotes the forum.

Exit status 1 if any problem is found. Usage:
  python3 -I check_dossier.py ac_discussion/*_dossier.json
"""

import json
import sys

VALID = {"verified true", "partly true", "verified false", "could not verify", "not checked"}
RES = {"verified in revision", "rebuttal only", "not in revision", "promise only", "not verified", "n/a"}


def check(d, notes):
    out = []
    for c in d.get("concerns", []):
        cid, sev = c.get("id", "?"), c.get("severity")
        major = sev in ("Critical", "Major")
        quoted = {q.get("who") for q in c.get("reviewer_quotes", [])}
        for r in c.get("raised_by", []):
            if r not in quoted:
                out.append(f"{cid}: raised by {r} but no verbatim quote from {r}")
        v, r = c.get("validity"), c.get("resolution_check")
        if v is None or r is None:
            out.append(f"{cid}: missing validity/resolution_check (old 'factcheck' schema?)")
            continue
        vv, rv = v.get("verdict"), r.get("verdict")
        if vv not in VALID:
            out.append(f"{cid}: unknown validity verdict {vv!r}")
        if rv not in RES:
            out.append(f"{cid}: unknown resolution verdict {rv!r}")
        ve, re_ = v.get("evidence", []), r.get("evidence", [])
        if major and vv == "not checked":
            out.append(f"{cid} ({sev}): validity not checked")
        if major and rv == "not verified":
            out.append(f"{cid} ({sev}): claimed resolution not verified")
        if vv in ("verified true", "partly true", "verified false"):
            has_orig = any(q.get("who") == "pdf" and str(q.get("pdf", "")).endswith("_original") for q in ve)
            has_auth = any(q.get("who") == "Authors" for q in ve)
            if not ve:
                out.append(f"{cid}: validity '{vv}' without evidence")
            elif not (has_orig or has_auth):
                out.append(f"{cid}: validity '{vv}' rests only on revision-era evidence (cite the original or an author concession)")
            elif not has_orig:
                notes.append(f"{cid}: validity '{vv}' rests on author quotes only; confirm they are concessions about the original, not new rebuttal results")
        if rv == "n/a" and c.get("response_type") in ("revision made", "new experiment"):
            out.append(f"{cid}: resolution 'n/a' but the authors claim '{c.get('response_type')}' (check the revision)")
        if rv == "promise only" and not re_:
            out.append(f"{cid}: 'promise only' without the promise quote")
        if rv == "verified in revision" and not any(q.get("who") == "pdf" and str(q.get("pdf", "")).endswith("_revised") for q in re_):
            out.append(f"{cid}: 'verified in revision' without a revised-PDF quote")
        if rv == "rebuttal only" and not any(q.get("who") != "pdf" for q in re_):
            out.append(f"{cid}: 'rebuttal only' without a forum quote")
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    total = 0
    for fn in sys.argv[1:]:
        with open(fn, encoding="utf-8") as f:
            d = json.load(f)
        notes = []
        probs = check(d, notes)
        total += len(probs)
        for p in probs:
            print(f"CHECK {d.get('number', fn)} {p}")
        for p in notes:
            print(f"NOTE  {d.get('number', fn)} {p}")
    print(f"{len(sys.argv) - 1} dossiers, {total} problems (NOTE lines are for review, not failures)")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
