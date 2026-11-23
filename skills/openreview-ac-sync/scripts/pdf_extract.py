#!/usr/bin/env python3
"""Extract text from paper PDFs, check the main-text page limit, and diff revisions.

Needs poppler's `pdftotext` and `pdfinfo` on PATH. Files are matched by the
`paper_<number>_...pdf` naming used by download_batch_pdfs.

Outputs (in --out-dir):
  <num>_revised.txt / <num>_original.txt   pdftotext output; pages separated by \\f
  <num>_diff.txt                           line diff original -> revised (whitespace-normalized)
  00_pdf_summary.md                        pages, references page, limit check, diff size

Usage:
  python3 -I pdf_extract.py --pdf-dir papers_2026-11-20 --out-dir ac_discussion/pdf_text
      [--orig-dir papers] [--page-limit 10]
"""

import argparse
import difflib
import glob
import os
import re
import shutil
import subprocess

HEADING_RE = re.compile(r"^([A-Z]|\d+)(\.\d+)*\.?\s+[A-Z][^.]{2,80}$")


def by_number(folder):
    out = {}
    for p in glob.glob(os.path.join(folder, "*.pdf")):
        m = re.match(r"paper_(\d+)_", os.path.basename(p))
        if m:
            out[int(m.group(1))] = p
    return out


def to_text(pdf, txt):
    subprocess.run([shutil.which("pdftotext"), pdf, txt], check=True, capture_output=True)
    with open(txt, encoding="utf-8", errors="replace") as f:
        return f.read()


def n_pages(pdf):
    r = subprocess.run([shutil.which("pdfinfo"), pdf], check=True, capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    return int(m.group(1)) if m else None


MAIN_END_PREFIX = (
    "AIUSESTATEMENT", "ETHICSSTATEMENT", "REPRODUCIBILITYSTATEMENT", "LLMUSAGE",
    "USEOFLARGELANGUAGEMODELS", "ACKNOWLEDG", "AUTHORCONTRIBUTIONS",
)
MAIN_END_EXACT = ("REFERENCES", "BIBLIOGRAPHY")


def main_text_end(text):
    """(heading, 1-based page, fraction of that page above it) of the first heading
    that usually follows the main text (statements, acknowledgments, references).

    Small-caps headings come out of pdftotext as 'R EFERENCES', so lines are
    compared with spaces, digits and dots removed.
    """
    for i, page in enumerate(text.split("\f"), start=1):
        offset = 0
        for ln in page.splitlines(keepends=True):
            squashed = re.sub(r"[\s\d.:]+", "", ln).upper()
            if squashed in MAIN_END_EXACT or (len(squashed) <= 40 and squashed.startswith(MAIN_END_PREFIX)):
                return ln.strip(), i, round(offset / max(len(page), 1), 2)
            offset += len(ln)
    return None, None, None


def norm_lines(text):
    lines = []
    for ln in text.replace("\f", "\n").splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln and not ln.isdigit():
            lines.append(ln)
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True, help="current (revised) PDFs")
    ap.add_argument("--orig-dir", help="original submission PDFs, for the diff")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--page-limit", type=int, default=None)
    a = ap.parse_args()
    if not shutil.which("pdftotext") or not shutil.which("pdfinfo"):
        raise SystemExit("pdftotext/pdfinfo not found (brew install poppler)")
    os.makedirs(a.out_dir, exist_ok=True)

    rev, orig = by_number(a.pdf_dir), by_number(a.orig_dir) if a.orig_dir else {}
    rows = [
        "# PDF summary",
        "",
        f"Revised: `{a.pdf_dir}`; original: `{a.orig_dir or '-'}`; page limit: {a.page_limit or '-'}",
        "",
        "Main-text length is estimated from the first heading that usually follows the main text "
        "(AI-use/ethics/reproducibility statements, acknowledgments, references): if it starts in the top 20% "
        "of a page, the main text ends on the page before. A heuristic: open flagged PDFs and check by eye.",
        "",
        "| # | pages | main text ends at (page, pos) | est. main pages | over limit? | diff lines +/- | new or changed headings (revised) |",
        "|---|---|---|---|---|---|---|",
    ]
    for num in sorted(rev):
        rtxt = to_text(rev[num], os.path.join(a.out_dir, f"{num}_revised.txt"))
        pages = n_pages(rev[num])
        head, rp, pos = main_text_end(rtxt)
        main_pages = None if rp is None else (rp - 1 if pos < 0.2 else rp)
        over = "-" if a.page_limit is None or main_pages is None else ("**check**" if main_pages > a.page_limit else "no")
        plus = minus = 0
        heads = []
        if num in orig:
            otxt = to_text(orig[num], os.path.join(a.out_dir, f"{num}_original.txt"))
            diff = list(difflib.unified_diff(norm_lines(otxt), norm_lines(rtxt), "original", "revised", n=0, lineterm=""))
            with open(os.path.join(a.out_dir, f"{num}_diff.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(diff) + "\n")
            plus = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
            minus = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
            heads = [d[1:] for d in diff if d.startswith("+") and not d.startswith("+++") and HEADING_RE.match(d[1:])]
        rows.append(
            f"| {num} | {pages} | {head or '?'} (p{rp or '?'}, {pos if pos is not None else '-'}) | {main_pages or '?'} | {over} "
            f"| +{plus} / -{minus} | {'; '.join(heads[:8]) + (' ...' if len(heads) > 8 else '') or '-'} |"
        )
    with open(os.path.join(a.out_dir, "00_pdf_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    print(f"wrote {len(rev)} papers to {a.out_dir}")


if __name__ == "__main__":
    main()
