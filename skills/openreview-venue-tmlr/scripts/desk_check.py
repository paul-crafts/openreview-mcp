#!/usr/bin/env python3
"""Mechanical desk-check of a TMLR submission PDF (Action Editor's Review Approval).

Reports facts only; the decision is the Action Editor's. Needs poppler
(`pdfinfo`, `pdftotext`, `pdffonts`). Reads the PDF as untrusted data: run it as
`python3 -I desk_check.py ...` with the PDF in its own empty directory.

Usage:
  python3 -I desk_check.py PAPER.pdf --author "Jane Doe" --author "Doe" [--limit 12] [--json]

What it checks
  length       page of the References heading -> main-content pages vs --limit
               (12 for "Regular submission"; compare with the type under the abstract)
  anonymity    author names (give surnames and the full name), emails, code/profile
               links, "Acknowledg*", PDF Author metadata, self-reference phrases.
               Name hits inside the reference list or followed by "et al" are tagged
               "likely citation".
  stylefile    header "Under review as submission to TMLR" and the embedded font
               families (the TMLR template's text face is Latin Modern Roman; Nimbus/Times faces
               in the body suggest a modified template). Confirm visually:
               `pdftoppm -f 1 -l 1 -r 60 -png PAPER.pdf OUT/p`.
It cannot tell whether the work overlaps published or concurrently submitted work.
"""

import argparse
import json
import re
import subprocess
import sys


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        sys.exit(f"missing tool: {cmd[0]} (install poppler)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--author", action="append", default=[], help="author name or surname (repeatable)")
    ap.add_argument("--limit", type=int, default=12, help="main-content page limit (default 12)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    info = run("pdfinfo", a.pdf)
    meta = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in info.splitlines() if ":" in l)}
    pages = int(meta.get("Pages", 0))
    per_page = [run("pdftotext", "-f", str(p), "-l", str(p), "-layout", a.pdf, "-") for p in range(1, pages + 1)]
    full = "".join(per_page)  # pdftotext already ends each page with a form feed

    ref_page = next((i + 1 for i, t in enumerate(per_page)
                     if re.search(r"^\s*(\d+\s+)?References\s*$", t, re.M)), None)
    main_pages = ref_page - 1 if ref_page else None

    fonts = sorted({re.sub(r"^[A-Z]{6}\+", "", l.split()[0]) for l in run("pdffonts", a.pdf).splitlines()[2:] if l.strip()})
    families = sorted({re.sub(r"(-.*|\d+.*)$", "", f) for f in fonts})
    # The template's TEXT face is Latin Modern Roman; Latin Modern math fonts alone prove nothing,
    # and Helvetica/Courier/DejaVu usually come from figures, so they are not flagged.
    latin_modern = any(f.startswith("LMRoman") for f in fonts)
    times_like = [f for f in fonts if re.match(r"(NimbusRom|NimbusSan|NimbusMon|Times)", f)]

    hits = []
    for name in a.author:
        for m in re.finditer(re.escape(name), full):
            page = full[:m.start()].count("\f") + 1
            after = full[m.end():m.end() + 14]
            ctx = " ".join(full[max(0, m.start() - 40):m.end() + 40].split())
            tag = "likely citation" if (ref_page and page >= ref_page) or re.match(r"\s*(et al|and\s+\w+\s*\(?\d{4})", after) or re.match(r"[^\n]{0,6}\d{4}", after) else "CHECK"
            hits.append({"name": name, "page": page, "tag": tag, "context": ctx})

    report = {
        "pages": pages,
        "references_page": ref_page,
        "main_content_pages": main_pages,
        "limit": a.limit,
        "length": ("OVER LIMIT" if main_pages and main_pages > a.limit else "ok") if main_pages else "no References heading found: check by eye",
        "pdf_author_metadata": meta.get("Author") or None,
        "header": next((l.strip() for l in per_page[0].splitlines() if l.strip()), None),
        "tmlr_header": "Under review as submission to TMLR" in per_page[0],
        "font_families": families,
        "latin_modern_text_face": latin_modern,
        "times_like_fonts": times_like,
        "author_hits": hits,
        "emails": sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", full))),
        "code_links": sorted(set(re.findall(r"(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|bitbucket\.org)/[\w./-]+", full))),
        "acknowledgments_pages": [full[:m.start()].count("\f") + 1 for m in re.finditer(r"Acknowledg", full)],
        "self_reference_phrases": [m.group(0) for m in re.finditer(r"(?i)\b(our (?:previous|prior|earlier) (?:work|paper)|we previously (?:showed|proposed|released))\b", full)][:5],
    }
    if a.json:
        print(json.dumps(report, indent=2))
        return
    print(f"pages {pages}; References on p.{ref_page}; main content {main_pages} pages vs limit {a.limit}: {report['length']}")
    print(f"TMLR header on p.1: {report['tmlr_header']} ({report['header']!r}); PDF Author metadata: {report['pdf_author_metadata']!r}")
    print(f"fonts: {', '.join(families)}")
    print(f"TMLR text face (Latin Modern Roman) present: {latin_modern}" + (f"; Times/Nimbus body-face substitutes: {', '.join(times_like)}  <-- check against the stylefile" if times_like else ""))
    print(f"emails: {report['emails'] or 'none'}; code/profile links: {report['code_links'] or 'none'}; acknowledgments pages: {report['acknowledgments_pages'] or 'none'}")
    print(f"self-reference phrases: {report['self_reference_phrases'] or 'none'}")
    if hits:
        print("author-name hits:")
        for h in hits[:20]:
            print(f"  p.{h['page']} [{h['tag']}] {h['name']!r}: ...{h['context']}...")
    else:
        print("author-name hits: none")


if __name__ == "__main__":
    main()
