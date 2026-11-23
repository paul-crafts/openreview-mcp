#!/usr/bin/env python3
"""Verify every quote object in one or more JSON files (dossiers, TL;DRs, message waves).

A quote object is any JSON dict with "who" and "text" keys:
  {"who": "Rv2b", "text": "..."}                 forum quote by Reviewer_Rv2b
  {"who": "Authors", "text": "..."}              forum quote by the authors
  {"who": "PC", "text": "..."}                   Program_Chairs ("AC" = the paper's Area_Chair_*)
  {"who": "pdf", "pdf": "1234_revised", "page": "5", "text": "..."}   PDF excerpt
  {"who": "Rv2b", "text": "...", "latest": true}  must be in Rv2b's most recent note (by creation date)

The paper is taken from the nearest enclosing "ref" key (the dump filename stem).

Checks:
  forum: `text` is an exact substring of a content string of a note SIGNED by `who`
         (stricter than verify_quotes_in_batch, which only checks the whole forum).
  pdf:   `text` is a substring of <pdf-text-dir>/<pdf>.txt after collapsing whitespace,
         joining line-break hyphenation and expanding ligatures; if `page` is given,
         the excerpt must be on that page (1-based, pages split on form feeds).
  --fold-ascii also maps typographic punctuation to ASCII on both sides (for
         message drafts, which must be plain ASCII).

Exit status 1 if any quote fails. Usage:
  python3 -I verify_quotes.py --dump-dir forum_dump_2026-11-20 [--pdf-text-dir ac_discussion/pdf_text]
      [--fold-ascii] FILE.json [FILE.json ...]
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-sync", "scripts"))
import dumplib as dl  # noqa: E402

ASCII_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", " ": " ", " ": " ", " ": " ",
    "×": "x", "≤": "<=", "≥": ">=", "→": "->", "←": "<-", "±": "+/-",
}
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def fold_ascii(s):
    return "".join(ASCII_FOLD.get(ch, ch) for ch in s)


def norm_pdf(s, keep_hyphen=False):
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    # Join words split across lines; a line-final hyphen may be real ("in-domain") or not.
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1-\2" if keep_hyphen else r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


def signer_matches(sig, who):
    if who in ("Authors", "authors"):
        return sig == "Authors"
    if who in ("PC", "Program_Chairs"):
        return sig == "Program_Chairs"
    if who in ("AC", "Area_Chair"):
        return sig.startswith("Area_Chair")
    if who in ("SAC", "Senior_Area_Chair"):
        return sig.startswith("Senior_Area_Chair")
    return sig in (who, f"Reviewer_{who}")


def walk(obj, ref, path, out):
    if isinstance(obj, dict):
        ref = obj.get("ref", ref)
        if "who" in obj and "text" in obj and isinstance(obj["text"], str):
            out.append((ref, path, obj))
        for k, v in obj.items():
            walk(v, ref, f"{path}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, ref, f"{path}[{i}]", out)


def visible_to(readers, audience):
    """Can `audience` ("reviewers" = every reviewer of the paper, or one reviewer id) read a note?"""
    if "everyone" in readers or "Reviewers" in readers:
        return True
    return audience != "reviewers" and f"Reviewer_{audience}" in readers


class Sources:
    def __init__(self, dump_dir, pdf_dir, fold):
        self.dump_dir, self.pdf_dir, self.fold = dump_dir, pdf_dir, fold
        self.forums, self.pdfs = {}, {}

    def f(self, s):
        return fold_ascii(s) if self.fold else s

    def forum(self, ref):
        if ref not in self.forums:
            path = os.path.join(self.dump_dir, ref if ref.endswith(".json") else f"{ref}.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            notes = [data["submission"]] + data.get("replies", [])
            self.forums[ref] = [(dl.signer(n), [self.f(s) for s in dl.note_strings(n)], n.get("cdate") or n.get("tcdate") or 0,
                                 dl.readers_short(n)) for n in notes]
        return self.forums[ref]

    def pdf(self, stem):
        if stem not in self.pdfs:
            path = os.path.join(self.pdf_dir, stem if stem.endswith(".txt") else f"{stem}.txt")
            with open(path, encoding="utf-8", errors="replace") as fh:
                pages = fh.read().split("\f")
            self.pdfs[stem] = [
                (self.f(norm_pdf(p)), self.f(norm_pdf(p, keep_hyphen=True))) for p in pages
            ]
        return self.pdfs[stem]

    def check(self, ref, q, audience=None):
        """audience: None (no visibility check), "reviewers" (all of the paper's reviewers) or a
        reviewer id (one reviewer). With an audience, a forum quote must come from a note those
        readers can see, and "latest" means the signer's latest note those readers can see."""
        who, text = q["who"], self.f(q["text"])
        if not text.strip():
            return "empty quote"
        if who == "pdf":
            if not self.pdf_dir or not q.get("pdf"):
                return "pdf quote needs --pdf-text-dir and a 'pdf' stem"
            pages = self.pdf(q["pdf"])
            t = norm_pdf(text)
            t0 = t.replace("-", "")  # pdftotext itself drops line-break hyphens ("in-domain" -> "indomain")

            def on(p, ph):
                return t in p or t in ph or t0 in p.replace("-", "")

            hits = [i + 1 for i, (p, ph) in enumerate(pages) if on(p, ph)]
            if not hits and on(" ".join(p for p, _ in pages), " ".join(ph for _, ph in pages)):
                hits = ["(spans a page break)"]
            if not hits:
                return f"not found in {q['pdf']}.txt"
            if q.get("page") and str(q["page"]) not in [str(h) for h in hits] and hits != ["(spans a page break)"]:
                return f"found on page(s) {hits}, not on page {q['page']}"
            return None
        if not ref:
            return "no enclosing 'ref'"
        notes = self.forum(ref)
        signers_with_text = [sig for sig, strings, _, _ in notes if any(text in s for s in strings)]
        if any(signer_matches(sig, who) for sig in signers_with_text):
            own = [(c, strings, rd) for sig, strings, c, rd in notes if signer_matches(sig, who)]
            if audience:
                own = [x for x in own if visible_to(x[2], audience)]
                if not any(any(text in s for s in strings) for _, strings, _ in own):
                    return f"text is by {who}, but only in a note the recipients cannot read (confidential)"
            if q.get("latest"):
                c_last, strings_last, _ = max(own, key=lambda x: x[0])
                if not any(text in s for s in strings_last):
                    return f"text is by {who}, but not in their latest {'visible ' if audience else ''}note ({dl.ts(c_last)})"
            return None
        if signers_with_text:
            return f"text found, but signed by {sorted(set(signers_with_text))}, not {who}"
        return "not found in forum"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--pdf-text-dir")
    ap.add_argument("--fold-ascii", action="store_true")
    a = ap.parse_args()

    src = Sources(a.dump_dir, a.pdf_text_dir, a.fold_ascii)
    total = failed = 0
    for fn in a.files:
        with open(fn, encoding="utf-8") as fh:
            data = json.load(fh)
        quotes = []
        walk(data, None, "$", quotes)
        for ref, path, q in quotes:
            total += 1
            try:
                err = src.check(ref, q)
            except FileNotFoundError as e:
                err = f"source missing: {e.filename}"
            if err:
                failed += 1
                t = q["text"]
                print(f"FAIL {fn} {path} [{q['who']}] {err}\n     \"{t[:160]}{'...' if len(t) > 160 else ''}\"")
    print(f"{total - failed}/{total} quotes verified ({failed} failed)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
