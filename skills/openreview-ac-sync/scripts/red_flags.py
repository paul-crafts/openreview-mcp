#!/usr/bin/env python3
"""Quick "elephant in the room" scan of an AC batch: things that can change a paper's fate,
or call for action by the AC, SAC or PCs, that a reviewer discussion can miss.

Per paper, from the forum dump and the extracted PDF text, it lists:
  1. anonymity: links that may identify the authors (code or model hubs, personal pages,
     cloud drives), emails, acknowledgments and self-references, in the revised or original
     PDF and in author comments; real-profile signatures or profile ids in forum notes;
  2. notes by Program Chairs and Senior ACs, and confidential author notes to the AC;
  3. ethics flags and AI-use fields in reviews; reviewers who say they used an LLM on the paper;
  4. author comments that point to an appendix, section, table or figure label that the
     revised PDF does not contain (a claimed revision that is not there);
  5. review counts, and the main-text page estimate from 00_pdf_summary.md if present.

Everything is a lead to check by eye, not a verdict. Output: a markdown report.
Usage:
  python3 -I red_flags.py --dump-dir forum_dump_<date> --pdf-text-dir ac_discussion/pdf_text \
      --out ac_discussion/red_flags_<date>.md [--review-suffix Official_Review]
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import dumplib as dl  # noqa: E402

URL = re.compile(r"https?://[^\s)\]}>\"',]+", re.I)
SAFE_HOSTS = ("openreview.net", "arxiv.org", "doi.org", "dl.acm.org", "aclanthology.org", "proceedings.",
              "papers.nips.cc", "neurips.cc", "iclr.cc", "icml.cc", "jmlr.org", "pmlr", "anonymous.4open.science",
              "creativecommons.org", "wikipedia.org", "w3.org", "python.org", "pytorch.org", "tensorflow.org",
              "ieee", "springer", "sciencedirect", "nature.com", "acm.org", "aaai.org", "semanticscholar")
RISKY_HOSTS = ("github.com", "gitlab", "huggingface.co", "hf.co", "drive.google", "dropbox", "github.io",
               "sites.google", "bitbucket", "wandb.ai", "kaggle.com", "colab.research", "youtube", "twitter.com", "x.com")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SELF_REF = re.compile(r"\b(our (previous|prior|earlier) (work|paper|study)|in our previous|we previously (showed|proposed|introduced))\b", re.I)
ACK = re.compile(r"^\s*A\s?CKNOWLEDG", re.M)
PROFILE = re.compile(r"~[A-Z][A-Za-z\-]+(_[A-Z][A-Za-z\-]+)+\d*")
LLM_USE = re.compile(r"[^.\n]*\b(I|we)\b[^.\n]*\b(used|asked|uploaded|ran|fed|prompted|consulted)\b[^.\n]*"
                     r"\b(ChatGPT|GPT-?\d|Claude|Gemini|LLM|language model|Copilot|Perplexity|NotebookLM)\b[^.\n]*", re.I)
LABEL = re.compile(r"\b(Appendix|App\.|Section|Sec\.|Table|Tab\.|Figure|Fig\.)\s*~?\s*([A-Z](?:\.\d+)+|[A-Z]\b|\d+(?:\.\d+)*)")


def hosts(text):
    out = []
    for u in URL.findall(text):
        h = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].lower()
        if any(r in u.lower() for r in RISKY_HOSTS) and not any(s in h for s in SAFE_HOSTS):
            out.append(u.rstrip(".;"))
    return out


def label_present(kind, lab, pdf):
    k = kind.rstrip(".").lower()
    if k in ("table", "tab"):
        return re.search(rf"\bTable\s*{re.escape(lab)}\b", pdf) is not None
    if k in ("figure", "fig"):
        return re.search(rf"\b(Figure|Fig\.)\s*{re.escape(lab)}\b", pdf) is not None
    # appendix / section: a heading "D.10 Title" or a reference "Appendix D.10" / "Section 4.3"
    return (re.search(rf"(^|\n)\s*{re.escape(lab)}\s+[A-Z]", pdf) is not None
            or re.search(rf"\b(Appendix|Section|Sec\.|App\.)\s*{re.escape(lab)}\b", pdf) is not None)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--pdf-text-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review-suffix", default="Official_Review")
    a = ap.parse_args()

    pages = {}
    for ln in read(os.path.join(a.pdf_text_dir, "00_pdf_summary.md")).splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|[^|]*\|\s*(\d+)\s*\|\s*(\w+)\s*\|", ln)
        if m:
            pages[int(m.group(1))] = (int(m.group(3)), m.group(4))

    L = ["# Red-flag scan", "", f"Dump `{a.dump_dir}`, PDF text `{a.pdf_text_dir}`. Leads to check by eye, not verdicts.", ""]
    papers = sorted(dl.load_dump_dir(a.dump_dir).values(), key=lambda p: dl.number_of(p) or 0)
    for P in papers:
        notes = P.get("replies", [])
        n = dl.number_of(P)
        title = dl.title_of(P)
        if dl.is_excluded(P):
            L += [f"## {n} {title[:70]}", "- excluded (withdrawn or desk rejected): skipped", ""]
            continue
        rev, orig = read(os.path.join(a.pdf_text_dir, f"{n}_revised.txt")), read(os.path.join(a.pdf_text_dir, f"{n}_original.txt"))
        items = []
        # 1. anonymity
        for name, txt in (("revised PDF", rev), ("original PDF", orig)):
            for u in sorted(set(hosts(txt))):
                items.append(f"anonymity: {name} links to `{u}`")
            for e in sorted(set(EMAIL.findall(txt)))[:5]:
                items.append(f"anonymity: {name} contains email `{e}`")
            if ACK.search(txt):
                items.append(f"anonymity: {name} has an Acknowledgments heading")
            for m in sorted({m.group(0) for m in SELF_REF.finditer(txt)})[:3]:
                items.append(f"anonymity: {name} self-reference \"{m}\"")
        for note in notes:
            sig, rd = dl.signer(note), dl.readers_short(note)
            full = (note.get("signatures") or [""])[0]
            if full.startswith("~"):
                items.append(f"anonymity: note {note['id']} ({dl.inv_suffix(note)}, {dl.ts(note.get('cdate'))}) is signed with a real profile `{full}`")
            text = "\n".join(dl.note_strings(note))
            for pid in sorted({m.group(0) for m in PROFILE.finditer(text)}):
                items.append(f"anonymity: note {note['id']} by {sig} ({dl.ts(note.get('cdate'))}, readers {','.join(rd)}) mentions profile id `{pid}`")
            if sig == "Authors":
                for u in sorted(set(hosts(text))):
                    items.append(f"anonymity: author note {note['id']} ({dl.ts(note.get('cdate'))}) links to `{u}`")
        # 2. chairs' notes and confidential author notes
        for note in notes:
            sig, rd = dl.signer(note), dl.readers_short(note)
            first = re.sub(r"\s+", " ", " ".join(dl.note_strings(note)))[:160]
            if sig in ("Program_Chairs",) or sig.startswith("Senior_Area_Chair"):
                items.append(f"chairs: {sig} {dl.inv_suffix(note)} {dl.ts(note.get('cdate'))}: \"{first}\"")
            if sig == "Authors" and not dl.visible_to_all_reviewers(note) and dl.inv_suffix(note) != "Submission":
                items.append(f"confidential author note {dl.ts(note.get('cdate'))} (readers {','.join(rd)}): \"{first}\"")
        # 3. ethics, AI use, reviewers' LLM use
        reviews = [x for x in notes if dl.inv_suffix(x) == a.review_suffix]
        for r in reviews:
            rid = dl.short_id(dl.signer(r))
            for k, v in (r.get("content") or {}).items():
                val = dl.value(v)
                if "ethic" in k.lower() and val not in (None, "", "No", "No.", ["No"], "N/A"):
                    items.append(f"ethics: {rid} `{k}` = {str(val)[:120]!r}")
        for note in notes:
            sig = dl.signer(note)
            if sig.startswith("Reviewer_"):
                text = " ".join(dl.note_strings(note))
                for m in LLM_USE.finditer(text):
                    items.append(f"reviewer LLM use: {dl.short_id(sig)} ({dl.inv_suffix(note)}, {dl.ts(note.get('cdate'))}): \"{m.group(0).strip()[:200]}\"")
        # 4. author claims pointing to labels missing from the revised PDF
        if rev:
            seen = set()
            for note in notes:
                if dl.signer(note) != "Authors" or dl.inv_suffix(note) == "Submission":
                    continue
                text = " ".join(dl.note_strings(note))
                for m in LABEL.finditer(text):
                    kind, lab = m.group(1), m.group(2)
                    key = (kind.rstrip(".").lower()[:3], lab)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not label_present(kind, lab, rev):
                        ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 60): m.end() + 60])
                        items.append(f"missing in revised PDF: authors cite `{kind} {lab}` ({dl.ts(note.get('cdate'))}): \"...{ctx}...\"")
        # 5. counts and pages
        info = f"{len(reviews)} reviews"
        if n in pages:
            info += f"; est. main-text pages {pages[n][0]} (over limit: {pages[n][1]})"
        L += [f"## {n} {title[:70]}", f"- {info}"]
        L += [f"- {x}" for x in items] or ["- no leads"]
        L.append("")
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
