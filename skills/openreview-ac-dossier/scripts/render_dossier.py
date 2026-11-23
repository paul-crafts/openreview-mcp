#!/usr/bin/env python3
"""Render a dossier JSON (or a meta-review TL;DR JSON) to markdown.

The JSON schema is in ../references/dossier_schema.md. Every quote is rendered
verbatim as a blockquote with its source label; the only other text is labels
and one-word fields that come from the JSON itself.

Usage:
  python3 -I render_dossier.py ac_discussion/1234_dossier.json [--out ac_discussion/1234_dossier.md]
"""

import argparse
import json
import os

SEV = {"Critical": 0, "Major": 1, "Minor": 2}
STATUS = {"Unresolved": 0, "Disputed": 1, "Partially": 2, "Not addressed": 3, "Resolved": 4}
VALIDITY = ["verified true", "partly true", "could not verify", "not checked", "verified false"]
OLD_VAL = {"reviewer correct": "verified true", "reviewer mistaken": "verified false", "partly": "partly true",
           "unverifiable": "could not verify"}
OLD_RES = {"revision verified": "verified in revision", "revision missing": "not in revision"}


def normalize(c):
    """Return (validity, resolution_check) dicts; accepts the older single `factcheck` field."""
    v, r = c.get("validity"), c.get("resolution_check")
    if v is None or r is None:
        fc = c.get("factcheck") or {}
        verdict, ev = fc.get("verdict"), fc.get("evidence", [])
        v = v or {"verdict": OLD_VAL.get(verdict, "not checked"), "evidence": ev if verdict in OLD_VAL else []}
        r = r or ({"verdict": OLD_RES[verdict], "evidence": ev} if verdict in OLD_RES else {"verdict": "not verified", "evidence": []})
    return v, r


def status_text(c):
    return c.get("status", "?") + (" (AC inferred)" if c.get("status_source") == "AC inferred" else " (reviewer)" if c.get("status_source") == "reviewer" else "")


def src(q):
    if q.get("who") == "pdf":
        page = f" p.{q['page']}" if q.get("page") else ""
        return f"PDF {q.get('pdf', '')}{page}"
    return q.get("who", "?")


def bq(q):
    text = q.get("text", "").strip("\n")
    lines = text.split("\n")
    out = [f"> **{src(q)}**: \"{lines[0]}"]
    out += [f"> {ln}" for ln in lines[1:]]
    out[-1] += '"'
    return "\n".join(out) + "\n"


def quotes(qs, title=None):
    if not qs:
        return ""
    head = f"{title}\n\n" if title else ""
    return head + "\n".join(bq(q) for q in qs) + "\n"


def render_tldr(t, lines):
    lines.append(f"## Meta-review TL;DR (lean: **{t.get('lean', '?')}**)\n")
    lines.append("_Verbatim quotes only. Write your own meta-review from this; then ask for the refine pass._\n")
    for item in t.get("lines", []):
        lines.append(f"### {item.get('label', '')}\n")
        lines.append(quotes(item.get("quotes", [])))


def render(d):
    L = [f"# {d.get('number', '')}: {d.get('title', '')}\n"]
    meta = [f"ref `{d.get('ref', '')}`", f"depth {d.get('depth', '-')}", f"source `{d.get('dump_dir', '-')}`"]
    L.append(" | ".join(meta) + "\n")

    if "tldr" in d:
        render_tldr(d["tldr"], L)

    if d.get("scores"):
        L.append("## Scores and engagement\n")
        cols = list(d["scores"][0].keys())
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "---|" * len(cols))
        for row in d["scores"]:
            L.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
        L.append("")

    cons = d.get("concerns", [])
    if cons:
        reviewers = []
        for row in d.get("scores", []):
            r = str(row.get("reviewer", ""))
            if r and r not in reviewers:
                reviewers.append(r)
        for c in cons:
            for r in c.get("raised_by", []):
                if r not in reviewers:
                    reviewers.append(r)

        def key(c):
            v, _ = normalize(c)
            vi = VALIDITY.index(v["verdict"]) if v["verdict"] in VALIDITY else 9
            return (vi, SEV.get(c.get("severity"), 9), STATUS.get(c.get("status"), 9))

        cons = sorted(cons, key=key)
        L.append("## Concerns\n")
        L.append("Grouped by validity (is the reviewer's claim true, as checked against the paper), "
                 "then importance and status. *Resolution check* says whether the AC verified the claimed fix.\n")
        for val in VALIDITY + ["other"]:
            group = [c for c in cons if (normalize(c)[0]["verdict"] if normalize(c)[0]["verdict"] in VALIDITY else "other") == val]
            if not group:
                continue
            L.append(f"### Validity: {val} ({len(group)})\n")
            L.append("| ID | concern | importance | status | resolution check | raised by |")
            L.append("|---|---|---|---|---|---|")
            for c in group:
                _, r = normalize(c)
                L.append(f"| {c.get('id')} | {c.get('label')} | {c.get('severity')} | {status_text(c)} "
                         f"| {r['verdict']} | {', '.join(c.get('raised_by', []))} |")
            L.append("")
        L.append("### Who shares which concern\n")
        L.append("| ID | concern | " + " | ".join(reviewers) + " |")
        L.append("|---|---|" + "---|" * len(reviewers))
        for c in cons:
            marks = ["x" if r in c.get("raised_by", []) else "" for r in reviewers]
            L.append(f"| {c.get('id')} | {c.get('label')} | " + " | ".join(marks) + " |")
        L.append("")
        L.append("### Concern details\n")
        for c in cons:
            v, r = normalize(c)
            L.append(f"#### {c.get('id')}: {c.get('label')}\n")
            L.append(f"Importance **{c.get('severity')}** | status **{status_text(c)}** | validity **{v['verdict']}** "
                     f"| resolution check **{r['verdict']}** | raised by {', '.join(c.get('raised_by', []))}\n")
            L.append(quotes(c.get("reviewer_quotes", []), "What the reviewers wrote:"))
            L.append(quotes(c.get("author_response", []), f"Authors ({c.get('response_type', '-')}):") if c.get("author_response")
                     else "Authors: no response\n")
            fu = c.get("followups", [])
            L.append(quotes(fu, "Reviewer follow-ups:") if fu else "Reviewer follow-ups: no reply\n")
            if v.get("evidence"):
                L.append(quotes(v["evidence"], f"Validity evidence ({v['verdict']}):"))
            if r.get("evidence"):
                L.append(quotes(r["evidence"], f"Resolution evidence ({r['verdict']}):"))

    dec = d.get("decision")
    if dec or d.get("disagreements"):
        L.append(f"## Disagreements and decisive points (lean: **{(dec or {}).get('lean', '?')}**)\n")
        for i, c in enumerate((dec or {}).get("cruxes", []), 1):
            L.append(f"### Crux {i}: {c.get('label', '')}\n")
            L.append(quotes(c.get("for", []), "For acceptance:"))
            L.append(quotes(c.get("against", []), "Against:"))
            if c.get("would_flip"):
                L.append(f"Would flip: {c['would_flip']}\n")
        for i, g in enumerate(d.get("disagreements", []), 1):
            L.append(f"### D{i}: {g.get('label', '')}\n")
            for side in g.get("sides", []):
                L.append(quotes(side.get("quotes", []), f"{side.get('position', '')}:"))

    if d.get("strengths"):
        L.append("## Strengths\n")
        for i, s in enumerate(d["strengths"], 1):
            who = ", ".join(sorted({q.get("who") for q in s.get("quotes", [])}))
            L.append(f"### S{i}: {s.get('label', '')} ({who})\n")
            L.append(quotes(s.get("quotes", [])))

    if d.get("quality"):
        L.append("## Review-quality tags\n")
        for t in d["quality"]:
            met = f" (condition met: {t['met']})" if t.get("met") and t["met"] != "n/a" else ""
            L.append(f"### {t.get('reviewer', '')}: {t.get('tag', '')}{met}\n")
            L.append(quotes(t.get("quotes", [])))

    if d.get("confidential"):
        L.append("## Confidential author notes (not visible to reviewers)\n")
        for c in d["confidential"]:
            L.append(quotes(c.get("quotes", []), f"{c.get('label', '')}"))

    if d.get("open_questions"):
        L.append("## Open questions for reviewers\n")
        for q in d["open_questions"]:
            L.append(f"### To {q.get('to', '')}: {q.get('label', '')}\n")
            L.append(quotes(q.get("quotes", [])))

    return "\n".join(L).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_file")
    ap.add_argument("--out")
    a = ap.parse_args()
    with open(a.json_file, encoding="utf-8") as f:
        d = json.load(f)
    out = a.out or os.path.splitext(a.json_file)[0] + ".md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(d))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
