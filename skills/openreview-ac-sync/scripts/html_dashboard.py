#!/usr/bin/env python3
"""Build a self-contained, per-paper HTML discussion dashboard for an AC batch.

One paper at a time:
- concerns first, grouped by validity (is the reviewer's claim true, checked against the
  paper), importance, status or reviewer; each with the AC's resolution check, the
  reviewers who share it (verbatim sentences) and all evidence;
- disagreements and decisive points (each crux: reviewers' words for vs against);
- where each reviewer stands and what to ask them (rating history, latest words,
  conditional promises, PC notes, drafted messages);
- strengths, AC-only material (confidential author notes, your notes), and the full
  discussion thread, verbatim.

Reads the dump (required) plus, when present, the per-paper dossiers, message waves,
the PDF summary and the AC-notes table of 00_dashboard.md. All forum and PDF text is
verbatim; every string is inserted with textContent, never as HTML.

Usage:
  python3 -I html_dashboard.py DUMP_DIR --out ac_discussion/dashboard.html
      [--prev OLDER_DUMP ...] [--review-suffix Official_Review] [--rating-field rating]
      [--confidence-field confidence] [--extra-fields soundness,presentation]
      [--boundary 2.5] [--workspace-dir ac_discussion] [--title "..."]

--workspace-dir (default: the folder of --out) is where it looks for <num>_dossier.json,
messages/wave*.json, messages/sent_log.md, pdf_text/00_pdf_summary.md and 00_dashboard.md.
Deep links: dashboard.html#p<num> opens a paper; add &dark or &light for the theme.
"""

import argparse
import datetime as _dt
import glob
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import dashboard as db  # noqa: E402
import dumplib as dl  # noqa: E402

SKIP_FIELDS = {"pdf", "supplementary_material", "_bibtex", "venueid", "paperhash", "venue", "authors", "authorids"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def who_label(sig):
    if sig.startswith("Reviewer_"):
        return sig[len("Reviewer_"):]
    if sig.startswith("Area_Chair"):
        return "AC"
    if sig == "Program_Chairs":
        return "PCs"
    return sig


def thread(dump_dir, ref):
    data = load_json(os.path.join(dump_dir, f"{ref}.json"))
    sub = data["submission"]
    by_id = {r["id"]: r for r in data.get("replies", [])}
    out = []
    for r in sorted(data.get("replies", []), key=lambda r: r.get("cdate") or 0):
        kind = dl.inv_suffix(r)
        if kind in ("Submission", "Edit", "Initial_Check") or r["id"] == sub["id"]:
            continue
        parent = by_id.get(r.get("replyto"))
        fields, scores = [], {}
        for k, v in r.get("content", {}).items():
            if k in SKIP_FIELDS:
                continue
            val = dl.value(v)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                scores[k] = val
            elif isinstance(val, str) and val.strip() and k != "title":
                fields.append([k, val])
            elif isinstance(val, list) and val and all(isinstance(x, str) for x in val):
                fields.append([k, ", ".join(val)])
        out.append({
            "id": r["id"], "date": dl.ts(r.get("cdate")), "who": who_label(dl.signer(r)), "kind": kind,
            "to": who_label(dl.signer(parent)) if parent and parent["id"] != sub["id"] else "paper",
            "visible": dl.is_reviewer_visible(r), "title": dl.value(r.get("content", {}).get("title")) or "",
            "fields": fields, "scores": scores,
        })
    return out


def _norm_sha(text):
    import hashlib
    return hashlib.sha256((text or "").replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def messages_by_paper(ws):
    """Drafts per paper, with their truth-check status from the ledger and fact-check reports."""
    out, sent = {}, set()
    log = os.path.join(ws, "messages", "sent_log.md")
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            sent = set(re.findall(r"\b[0-9]+-[A-Za-z][A-Za-z0-9_-]*", f.read()))
    ledger = []
    lp = os.path.join(ws, "messages", "ledger.jsonl")
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            ledger = [json.loads(x) for x in f if x.strip()]
    for w in sorted(x for x in glob.glob(os.path.join(ws, "messages", "wave*.json")) if not x.endswith(".send_plan.json")):
        wave = load_json(w)
        wdir = os.path.splitext(w)[0]
        for m in wave.get("messages", []):
            body, mid = m.get("body", ""), m.get("id")
            h = _norm_sha(body)
            ev = [e for e in ledger if e.get("sha256") == h]
            fc_path = os.path.join(wdir, f"{mid}.factcheck.json")
            fc = load_json(fc_path) if os.path.exists(fc_path) else None
            issues = []
            if mid in sent:
                status = "sent"
            elif any(e.get("event") == "approved" for e in ev):
                status = "approved"
            elif any(e.get("event") == "verified" for e in ev):
                status = "verified"
            elif fc is None:
                status = "not fact-checked"
            elif fc.get("sha256") != h:
                status = "fact-check outdated"
            elif fc.get("verdict") != "pass":
                status = "fact-check failed"
            else:
                status = "fact-checked, verify pending"
            if fc and fc.get("sha256") == h:
                issues = [{"text": x.get("text", ""), "status": x.get("status"), "note": x.get("note", "")}
                          for x in fc.get("sentences", []) if x.get("status") not in ("supported", "courtesy")]
            out.setdefault(m.get("paper"), []).append({
                "id": mid, "wave": wave.get("wave"), "wave_file": os.path.basename(w), "type": m.get("type"),
                "channel": m.get("channel"), "to": m.get("to", []), "subject": m.get("subject", ""),
                "body": body, "status": status, "issues": issues,
                "n_claims": len(m.get("claims", []) or []),
            })
    return out


def feedback_state(ws):
    """Latest feedback per message id from messages/feedback.jsonl (written by review_server.py)."""
    out = {}
    p = os.path.join(ws, "messages", "feedback.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if e.get("id"):
                    prev = out.get(e["id"], {})
                    out[e["id"]] = {**prev, **{k: v for k, v in e.items() if v is not None}}
    return out


def replies_since_sent(ws, dump_dir, papers):
    """Reviewer notes posted after the AC's last sent message on each paper (action items to read)."""
    lp = os.path.join(ws, "messages", "ledger.jsonl")
    last = {}
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if e.get("event") == "sent" and e.get("id"):
                    num = e["id"].split("-")[0]
                    last[num] = max(last.get(num, ""), e.get("ts", ""))
    out = []
    for P in papers:
        t = last.get(str(P["number"]))
        if not t or P.get("excluded"):
            continue
        since = _dt.datetime.fromisoformat(t).timestamp() * 1000
        data = load_json(os.path.join(dump_dir, f"{P['ref']}.json"))
        for r in data.get("replies", []):
            sig = dl.signer(r)
            if (r.get("cdate") or 0) > since and sig.startswith("Reviewer_"):
                out.append({"paper": P["number"], "id": r["id"], "who": dl.short_id(sig), "date": dl.ts(r.get("cdate")),
                            "visible": dl.is_reviewer_visible(r),
                            "text": re.sub(r"\s+", " ", " ".join(dl.note_strings(r)))[:600]})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def pdf_summary(ws):
    path = os.path.join(ws, "pdf_text", "00_pdf_summary.md")
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) >= 5 and cells[0].isdigit():
                    out[int(cells[0])] = {"pages": cells[1], "main": cells[3], "over": cells[4].replace("*", "")}
    return out


def notes_rows(ws):
    """Per-paper rows of the first table in the AC-notes part of 00_dashboard.md: {num: {header: cell}}."""
    path = os.path.join(ws, "00_dashboard.md")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read().split(db.BEGIN, 1)[0]
    rows = [ln for ln in text.splitlines() if ln.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", ln)]
    if not rows:
        return {}
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, out = cells[0], {}
    for r in cells[1:]:
        if r and r[0].isdigit():
            out[int(r[0])] = {h: c for h, c in zip(head[1:], r[1:])}
    return out


def parser():
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
    ap.add_argument("--scale", help="unused; kept for compatibility")
    ap.add_argument("--workspace-dir")
    ap.add_argument("--title", default="AC discussion dashboard")
    return ap


def build_page(a, token=None):
    """The dashboard HTML. With a token, the action items are editable through review_server.py."""
    ws = a.workspace_dir or os.path.dirname(os.path.abspath(a.out))
    papers = db.collect(a)
    msgs, pdfs, notes = messages_by_paper(ws), pdf_summary(ws), notes_rows(ws)
    fb = feedback_state(ws)
    for P in papers:
        dpath = os.path.join(ws, f"{P['number']}_dossier.json")
        P["dossier"] = load_json(dpath) if os.path.exists(dpath) else None
        md = os.path.join(ws, f"{P['number']}_dossier.md")
        P["dossier_md"] = os.path.basename(md) if os.path.exists(md) else None
        P["messages"] = msgs.get(P["number"], [])
        for x in P["messages"]:
            x["feedback"] = fb.get(x["id"])
        P["pdf"] = pdfs.get(P["number"])
        P["notes"] = notes.get(P["number"])
        P["thread"] = [] if P["excluded"] else thread(a.dump_dir, P["ref"])
    payload = {
        "title": a.title, "dump": a.dump_dir, "boundary": a.boundary,
        "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "papers": papers, "replies": replies_since_sent(ws, a.dump_dir, papers),
        "interactive": bool(token), "token": token or "",
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", html.escape(a.title)).replace("__DATA__", data)


def main():
    a = parser().parse_args()
    page = build_page(a)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {a.out}")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
html{background:#f9f9f7}
@media (prefers-color-scheme:dark){html:not([data-theme="light"]){background:#0d0d0d}}
html[data-theme="dark"]{background:#0d0d0d}html[data-theme="light"]{background:#f9f9f7}
.viz-root{color-scheme:light;
  --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--wash:rgba(11,11,11,.045);
  --for:#2a78d6;--against:#e34948;--good:#0ca30c;--serious:#ec835a;--critical:#d03b3b;--warn:#fab219}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--wash:rgba(255,255,255,.05);
  --for:#3987e5;--against:#e66767}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--wash:rgba(255,255,255,.05);
  --for:#3987e5;--against:#e66767}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;grid-template-columns:270px 1fr;min-height:100vh}
nav{border-right:1px solid var(--grid);padding:18px 12px;position:sticky;top:0;height:100vh;overflow-y:auto}
nav h1{font-size:15px;margin:0 6px 2px}nav .sub{margin:0 6px 12px}
nav button.item{display:block;width:100%;text-align:left;font:inherit;color:var(--ink);background:transparent;border:0;border-radius:8px;padding:8px 8px;cursor:pointer;margin-bottom:2px}
nav button.item:hover{background:var(--wash)}nav button.item[aria-current="true"]{background:var(--surface);box-shadow:0 0 0 1px var(--ring)}
nav .t{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:13px}
nav .m{font-size:12px;color:var(--ink2);display:flex;gap:8px;align-items:center;margin-top:2px}
nav .group{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:14px 8px 4px}
main{padding:22px 30px 60px;max-width:1280px}
h2{font-size:19px;margin:0 0 4px}h3{font-size:15px;margin:26px 0 10px}h4{font-size:13.5px;margin:12px 0 6px}
.sub{color:var(--ink2);font-size:12.5px}.muted{color:var(--muted)}
a{color:var(--for)}
.btn{font:inherit;font-size:12.5px;color:var(--ink2);background:transparent;border:1px solid var(--axis);border-radius:6px;padding:4px 9px;cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chip{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--axis);border-radius:14px;padding:2px 10px;font-size:12.5px;white-space:nowrap}
.sw{display:inline-block;width:10px;height:10px;border-radius:50%;flex:none}.sw.ring{background:transparent!important;border:2px solid currentColor}
.card{background:var(--surface);border-radius:10px;box-shadow:0 0 0 1px var(--ring);padding:12px 14px;margin-bottom:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:1100px){.two{grid-template-columns:1fr}}
.side-h{display:flex;gap:8px;align-items:center;font-weight:600;font-size:13px;margin-bottom:4px}
.bar{width:4px;align-self:stretch;border-radius:2px}
blockquote{margin:6px 0;padding:3px 10px;border-left:3px solid var(--axis);white-space:pre-wrap}
blockquote.for{border-left-color:var(--for)}blockquote.against{border-left-color:var(--against)}
blockquote .who{color:var(--ink2);font-weight:600;margin-right:6px}
.flip{margin-top:8px;font-size:13px}.flip b{color:var(--ink2);font-weight:600}
.revs{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
.rev h4{margin:0;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.rev .big{font-size:20px;font-weight:600}
.status{display:inline-flex;gap:5px;align-items:center;font-size:12.5px;white-space:nowrap}
.lbl{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-top:10px}
details{margin:4px 0}details>summary{cursor:pointer}
details.item{border-bottom:1px solid var(--grid);padding:6px 0}
details.item>summary{list-style:none;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
details.item>summary::-webkit-details-marker{display:none}
details.item>summary::before{content:"▸";color:var(--muted)}details.item[open]>summary::before{content:"▾"}
button.copy{margin:6px 0;font:inherit;font-size:12px;padding:3px 10px;border-radius:6px;border:0;box-shadow:0 0 0 1px var(--ring);background:var(--surface);color:var(--ink);cursor:pointer}
button.copy:hover{box-shadow:0 0 0 1px var(--muted)}
pre.msg{white-space:pre-wrap;background:var(--page);border-radius:8px;padding:10px 12px;box-shadow:0 0 0 1px var(--ring);font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
.post{border-left:3px solid var(--axis);padding:4px 0 4px 12px;margin:10px 0}
.post.reviewer{border-left-color:var(--for)}.post.authors{border-left-color:var(--muted)}.post.ac{border-left-color:var(--warn)}
.post .h{font-size:12.5px;color:var(--ink2)}.post .h b{color:var(--ink)}
.post .f{white-space:pre-wrap;margin-top:4px}.post .fk{font-size:11.5px;color:var(--muted);margin-top:6px}
.filters{display:flex;gap:12px;flex-wrap:wrap;color:var(--ink2);font-size:13px;margin-bottom:6px}
table.grid{border-collapse:collapse;width:100%;font-size:13px}
table.grid th{text-align:left;color:var(--ink2);font-weight:500;border-bottom:1px solid var(--axis);padding:6px 8px;white-space:nowrap}
table.grid td{border-bottom:1px solid var(--grid);padding:6px 8px;vertical-align:top}
table.grid .mark{text-align:center;color:var(--for);width:44px}
table.grid tr.paper{cursor:pointer}table.grid tr.paper:hover,table.grid tr.paper:focus{background:var(--wash);outline:none}
table.grid tr.detail>td{background:var(--surface);padding:6px 14px 14px}
.scroll{overflow-x:auto}
.filters select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--axis);border-radius:6px;padding:3px 6px}
.acts h3{display:flex;gap:8px;align-items:baseline}.acts h3 .n{color:var(--muted);font-weight:400;font-size:13px}
.draft{background:var(--surface);border-radius:10px;box-shadow:0 0 0 1px var(--ring);padding:12px 14px;margin-bottom:14px}
.draft.mine-ok{box-shadow:0 0 0 2px var(--good)}.draft.mine-chg{box-shadow:0 0 0 2px var(--warn)}
.draft .hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.draft .hd b{font-size:14px}
.draft textarea{width:100%;font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);background:var(--page);border:0;border-radius:8px;box-shadow:0 0 0 1px var(--ring);padding:10px 12px;resize:vertical}
.draft textarea.body{min-height:220px}.draft textarea.cmt{min-height:54px;font-family:inherit;font-size:13px}
.draft .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.btn.ok{border-color:var(--good);color:var(--good)}.btn.chg{border-color:var(--warn);color:var(--ink)}.btn.pri{border-color:var(--for);color:var(--for)}
.sendline{font:12.5px ui-monospace,Menlo,monospace;background:var(--page);box-shadow:0 0 0 1px var(--ring);border-radius:8px;padding:8px 10px;user-select:all;word-break:break-word}
.note{font-size:12.5px;color:var(--ink2)}.saved{color:var(--good);font-size:12.5px}
table.kv{border-collapse:collapse;font-size:13px}table.kv td{padding:3px 10px 3px 0;vertical-align:top;border-bottom:1px solid var(--grid)}
table.kv td:first-child{color:var(--ink2);white-space:nowrap}
</style>
</head>
<body>
<div class="viz-root">
  <nav aria-label="Papers"><h1 id="title"></h1><div class="sub" id="meta"></div><div id="list"></div>
    <div style="margin:16px 6px"><button class="btn" id="theme" type="button">Toggle dark mode</button></div></nav>
  <main id="main"></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s);
function el(tag, attrs, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs||{})){
    if (v===null||v===undefined||v===false) continue;
    if (k==='class') e.className=v; else if (k==='style') e.style.cssText=v; else e.setAttribute(k, v===true?'':v);
  }
  for (const k of kids.flat(Infinity)){ if (k===null||k===undefined||k===false) continue; e.append(k instanceof Node ? k : document.createTextNode(String(k))); }
  return e;
}
function put(node, ...kids){ for (const k of kids.flat(Infinity)){ if (k===null||k===undefined||k===false) continue; node.append(k instanceof Node ? k : document.createTextNode(String(k))); } return node; }
const fmt = x => x===null||x===undefined ? '-' : (Number.isInteger(x) ? String(x) : Number(x).toFixed(2));
const REVIEWERISH = w => w && !['Authors','pdf','AC','PC','PCs','SAC'].includes(w);
const LEAN = {'Reject':['var(--against)',false],'Lean reject':['var(--against)',true],'Borderline':['var(--muted)',false],'Lean accept':['var(--for)',true],'Accept':['var(--for)',false]};
const ORDER = ['Borderline','Lean accept','Lean reject','Accept','Reject'];
const lean = P => P.dossier && P.dossier.decision ? P.dossier.decision.lean : null;
function leanChip(l){
  if (!l) return el('span',{class:'muted'},'no dossier');
  const [c, ring] = LEAN[l] || ['var(--muted)', true];
  return el('span',{class:'status'}, el('span',{class:'sw'+(ring?' ring':''), style:(ring?'color:':'background:')+c}), l);
}
function sev(s){
  const c = {Critical:'var(--critical)',Major:'var(--serious)',Minor:'var(--muted)'}[s] || 'var(--muted)';
  return el('span',{class:'status'}, el('span',{style:'color:'+c,'aria-hidden':'true'}, {Critical:'▲',Major:'■',Minor:'●'}[s]||'●'), s||'-');
}
const VORD = ['verified true','partly true','could not verify','not checked','verified false'];
const SORD = ['Critical','Major','Minor'];
const STORD = ['Unresolved','Disputed','Partially','Not addressed','Resolved'];
const OLDV = {'reviewer correct':'verified true','reviewer mistaken':'verified false','partly':'partly true','unverifiable':'could not verify'};
const OLDR = {'revision verified':'verified in revision','revision missing':'not in revision'};
function vOf(c){ if (c.validity) return c.validity; const f = c.factcheck||{}; return {verdict: OLDV[f.verdict]||'not checked', evidence: OLDV[f.verdict] ? (f.evidence||[]) : []}; }
function rOf(c){ if (c.resolution_check) return c.resolution_check; const f = c.factcheck||{}; return {verdict: OLDR[f.verdict]||'not verified', evidence: OLDR[f.verdict] ? (f.evidence||[]) : []}; }
function iconChip(map, v){ const [i,col] = map[v] || ['·','var(--muted)']; return el('span',{class:'status'}, el('span',{style:'color:'+col,'aria-hidden':'true'}, i), v); }
const valChip = v => iconChip({'verified true':['✓','var(--against)'],'partly true':['≈','var(--serious)'],'could not verify':['?','var(--muted)'],'not checked':['–','var(--muted)'],'verified false':['✗','var(--for)']}, v);
const resChip = v => iconChip({'verified in revision':['✓','var(--good)'],'rebuttal only':['⚠','var(--warn)'],'not in revision':['✗','var(--critical)'],'promise only':['⚠','var(--warn)'],'not verified':['–','var(--muted)'],'n/a':['·','var(--muted)']}, v);
const statusChip = c => el('span',{}, c.status||'-', c.status_source ? el('span',{class:'muted'}, ' ('+(c.status_source==='AC inferred'?'AC inferred':'reviewer')+')') : null);
const MSG_STATUS = {'sent':['✓','var(--good)'],'approved':['✓','var(--for)'],'verified':['✓','var(--good)'],
  'fact-checked, verify pending':['·','var(--muted)'],'fact-check outdated':['!','var(--warn)'],'fact-check failed':['✗','var(--critical)'],
  'not fact-checked':['–','var(--muted)']};
const msgStatus = st => iconChip(MSG_STATUS, st);
function msgBlock(x, label){
  return el('details',{}, el('summary',{}, label+' · ', msgStatus(x.status)),
    el('div',{class:'sub'}, x.channel+' to '+(x.to||[]).join(', ')+(x.subject?' · '+x.subject:'')+' · '+x.n_claims+' evidence-backed sentences'),
    (x.issues||[]).length ? [el('div',{class:'lbl'}, 'Fact-check issues'), x.issues.map(i => el('blockquote',{class:'against'}, el('span',{class:'who'}, i.status), i.text, i.note ? ' - '+i.note : ''))] : null,
    copyBtn(x.body),
    el('pre',{class:'msg'}, x.body));
}
// The raw markdown is shown as-is (bold markers included), so selecting and copying it gives exactly what to paste in the forum.
function copyBtn(src){
  const b = el('button',{type:'button',class:'copy'}, 'Copy markdown');
  b.addEventListener('click', () => {
    const done = () => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy markdown'; }, 1500); };
    const fallback = () => { const t = el('textarea',{style:'position:fixed;opacity:0'}); t.value = typeof src==='function' ? src() : src; document.body.append(t); t.select(); try { document.execCommand('copy'); done(); } finally { t.remove(); } };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(typeof src==='function' ? src() : src).then(done, fallback); else fallback();
  });
  return b;
}
function q(qt, side){
  const kind = /_original$/.test(qt.pdf||'') ? 'Original submission' : (/_revised$/.test(qt.pdf||'') ? 'Revised PDF' : 'PDF '+(qt.pdf||''));
  const src = qt.who==='pdf' ? kind+(qt.page?' p.'+qt.page:'') : qt.who;
  return el('blockquote',{class:side||''}, el('span',{class:'who'}, src), qt.text);
}
const qs = (list, side) => (list||[]).map(x => q(x, side));
function reviewerChip(r){
  const traj = (r.trajectory||[]).map(fmt).join('→');
  return el('span',{class:'chip'}, el('b',{}, r.id), traj, el('span',{class:'muted'}, 'c'+fmt(r.confidence)),
    r.engaged ? null : el('span',{class:'muted'}, 'silent'), r.pc_flags.length ? el('span',{class:'muted'}, 'PC note') : null);
}

/* ---------- action items ---------- */
const FB_LABEL = {'satisfied':'You: satisfied','needs-changes':'You: needs changes','comment':'You: commented','addressed':'Addressed - please re-check'};
function allDrafts(){ return D.papers.flatMap(P => (P.messages||[]).map(x => Object.assign({paper:P.number, ptitle:P.title}, x))); }
function mine(x){ return (x.feedback||{}).status || null; }
function bucket(x){
  if (x.status==='sent') return 'sent';
  const f = mine(x);
  if (f==='satisfied') return 'ready';
  if (f==='needs-changes' || f==='comment') return 'changes';
  return 'review';   // includes 'addressed': the assistant changed it, you re-check
}
async function saveFeedback(x, status, comment, text){
  const r = await fetch('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json','X-Token':D.token},
    body: JSON.stringify({id:x.id, status, comment, text})});
  if (!r.ok) throw new Error(await r.text());
  const e = await r.json(); x.feedback = Object.assign({}, x.feedback||{}, e); return e;
}
function draftCard(x){
  const f = x.feedback || {};
  const card = el('div',{class:'draft'+(mine(x)==='satisfied'?' mine-ok':(mine(x)==='needs-changes'?' mine-chg':''))});
  put(card, el('div',{class:'hd'}, el('b',{}, x.id), el('span',{class:'sub'}, (x.channel==='forum' ? 'forum thread to ' : 'email to ')+(x.to||[]).join(', ')),
    msgStatus(x.status), f.status ? el('span',{class:'chip'}, FB_LABEL[f.status]||f.status) : null,
    el('a',{href:'#p'+x.paper}, 'paper '+x.paper)));
  put(card, el('div',{class:'sub'}, x.subject));
  if ((x.issues||[]).length) put(card, el('div',{class:'lbl'}, 'Fact-check issues'), x.issues.map(i => el('blockquote',{class:'against'}, el('span',{class:'who'}, i.status), i.text, i.note ? ' - '+i.note : '')));
  if (f.status==='addressed' && f.comment) put(card, el('div',{class:'note'}, 'Assistant: '+f.comment));
  const current = f.text || x.body;
  if (!D.interactive){
    put(card, copyBtn(current), el('pre',{class:'msg'}, current));
    if (x.status!=='sent') put(card, el('div',{class:'note'}, 'To comment, edit or mark it satisfied, open this page through review_server.py.'));
    return card;
  }
  const body = el('textarea',{class:'body','aria-label':'Message text'}); body.value = current;
  const cmt = el('textarea',{class:'cmt',placeholder:'Comment for the assistant (optional)','aria-label':'Comment'}); cmt.value = (f.status!=='addressed' && f.comment) || '';
  const msg = el('span',{class:'saved'});
  const send = async (status) => {
    const edited = body.value !== x.body ? body.value : null;
    try { await saveFeedback(x, status, cmt.value || null, edited); msg.textContent = 'Saved'; draw(); }
    catch(e){ msg.textContent = 'Not saved: '+e.message; msg.style.color = 'var(--critical)'; }
  };
  const ok = el('button',{class:'btn ok',type:'button'}, 'Satisfied'); ok.addEventListener('click', () => send('satisfied'));
  const chg = el('button',{class:'btn chg',type:'button'}, 'Needs changes'); chg.addEventListener('click', () => send('needs-changes'));
  const sv = el('button',{class:'btn',type:'button'}, 'Save comment / edit'); sv.addEventListener('click', () => send(mine(x)==='satisfied' && body.value===x.body ? 'satisfied' : 'comment'));
  const rs = el('button',{class:'btn',type:'button'}, 'Reset to verified text'); rs.addEventListener('click', () => { body.value = x.body; });
  put(card, x.status==='sent' ? el('pre',{class:'msg'}, x.body) : body);
  if (x.status!=='sent') put(card, cmt, el('div',{class:'row'}, ok, chg, sv, rs, copyBtn(() => body.value), msg),
    body.value!==x.body ? el('div',{class:'note'}, 'Edited: the assistant re-verifies and fact-checks edits before they can be sent.') : null);
  return card;
}
let draw = () => {};
function actionsView(){
  const m = el('div',{class:'acts'});
  draw = () => {
    m.replaceChildren();
    const ds = allDrafts(), by = {review:[], changes:[], ready:[], sent:[]};
    ds.forEach(x => by[bucket(x)].push(x));
    put(m, el('h2',{}, 'Action items'), el('div',{class:'sub'}, D.interactive ? 'Edits and comments are saved to the workspace for the assistant. Sending still needs your "send <ids>" in the chat.'
      : 'Read-only copy. Run review_server.py to comment, edit and mark drafts satisfied.'));
    const sec = (title, list, extra) => { if (!list.length && !extra) return; put(m, el('h3',{}, title, el('span',{class:'n'}, String(list.length))), extra||null, list.map(draftCard)); };
    sec('To review', by.review);
    sec('Needs changes (your comments)', by.changes);
    sec('Ready to send', by.ready, by.ready.length ? [el('div',{class:'note'}, 'Paste this line in the chat to send them:'),
      el('div',{class:'sendline'}, 'send '+by.ready.map(x => x.id).join(' '))] : null);
    const rp = D.replies||[];
    if (rp.length){
      put(m, el('h3',{}, 'New reviewer replies since your messages', el('span',{class:'n'}, String(rp.length))));
      put(m, rp.map(r => el('div',{class:'post reviewer'}, el('div',{class:'h'}, el('b',{}, r.who), ' on ', el('a',{href:'#p'+r.paper}, 'paper '+r.paper), ' · '+r.date+(r.visible?'':' · not visible to all reviewers')), el('div',{class:'f'}, r.text))));
    }
    if (by.sent.length) put(m, el('details',{}, el('summary',{}, 'Sent ('+by.sent.length+')'), by.sent.map(x => el('div',{class:'sub'}, x.id+' · '+(x.to||[]).join(', ')+' · '+x.subject))));
    if (!ds.length && !rp.length) put(m, el('div',{class:'note'}, 'Nothing to do right now.'));
  };
  draw();
  return m;
}
function nOpen(){ return allDrafts().filter(x => x.status!=='sent' && mine(x)!=='satisfied').length + (D.replies||[]).length; }

/* ---------- sidebar ---------- */
function rank(P){ if (P.excluded) return 99; const l = lean(P); return l ? ORDER.indexOf(l) : 50; }
const papers = [...D.papers].sort((a,b) => rank(a)-rank(b) || a.number-b.number);
let current = null;
function renderList(){
  const list = $('#list'); list.replaceChildren();
  const ab = el('button',{class:'item',type:'button','aria-current':String(current==='actions')},
    el('div',{class:'t'}, el('b',{}, 'Action items')), el('div',{class:'m'}, nOpen()+' open'));
  ab.addEventListener('click', () => select('actions'));
  list.append(ab);
  let group = null;
  for (const P of papers){
    const g = P.excluded ? 'Excluded' : (lean(P) || 'No dossier');
    if (g!==group){ list.append(el('div',{class:'group'}, g)); group = g; }
    const b = el('button',{class:'item',type:'button','aria-current':String(current===P.number)},
      el('div',{class:'t'}, P.number+' · '+P.title),
      el('div',{class:'m'}, P.excluded ? 'excluded' : (P.ratings||[]).map(fmt).join('/'), P.excluded ? null : leanChip(lean(P))));
    b.addEventListener('click', () => select(P.number));
    list.append(b);
  }
}

/* ---------- paper view ---------- */
function paperView(P){
  const m = el('div',{});
  const d = P.dossier || {};
  const revs = P.reviews || [];
  const links = [el('a',{href:P.url,target:'_blank',rel:'noopener'},'OpenReview forum')];
  if (P.dossier_md) links.push(' · ', el('a',{href:P.dossier_md},'dossier (markdown)'));
  put(m, el('h2',{}, P.number+' · '+P.title),
    el('div',{class:'sub'}, links, P.pdf ? ' · PDF '+P.pdf.pages+' pages, est. main text '+P.pdf.main+(P.pdf.over==='check'?' (check page limit)':'') : ''),
    el('div',{class:'chips'}, el('span',{class:'chip'}, 'Lean: ', leanChip(lean(P))), revs.map(reviewerChip),
      el('span',{class:'chip muted'}, 'mean '+fmt(P.mean)+(D.boundary!==null?' · boundary '+fmt(D.boundary):''))));
  if (P.excluded){ put(m, el('p',{class:'muted'}, 'Excluded: '+P.venueid)); return m; }

  /* 1. concerns, grouped (validity / importance / status / reviewer) */
  const concerns = d.concerns || [];
  const reviewerIds = revs.map(r => r.id);
  put(m, el('h3',{}, 'Concerns'));
  if (!concerns.length) put(m, el('p',{class:'muted'}, P.dossier ? 'No concerns in the dossier.' : 'No dossier yet (run the dossier skill).'));
  else {
    const modeSel = el('select',{'aria-label':'Group concerns by'}, ['validity','importance','status','reviewer'].map(v => el('option',{value:v}, v)));
    const holder = el('div',{});
    put(m, el('div',{class:'filters'}, el('label',{}, 'Group by ', modeSel)),
      el('div',{class:'sub',style:'margin:2px 0 8px'}, 'Validity: is the reviewer\'s claim true, checked against the paper. Resolution check: did I verify the authors\' claimed fix in the revised PDF. Reviewer columns: ● = raised it (hover for their words; click a row for everything).'),
      holder);
    const within = (a,b) => (SORD.indexOf(a.severity)-SORD.indexOf(b.severity)) || (VORD.indexOf(vOf(a).verdict)-VORD.indexOf(vOf(b).verdict)) || (STORD.indexOf(a.status)-STORD.indexOf(b.status));
    const draw = () => {
      const mode = modeSel.value, groups = [];
      const add = (g, c) => { let x = groups.find(y => y.g===g); if (!x){ x = {g, items:[]}; groups.push(x); } x.items.push(c); };
      for (const c of concerns){
        if (mode==='validity') add(vOf(c).verdict, c);
        else if (mode==='importance') add(c.severity||'-', c);
        else if (mode==='status') add(c.status||'-', c);
        else for (const r of (c.raised_by||[])) add('Reviewer '+r, c);
      }
      const order = mode==='validity' ? VORD : mode==='importance' ? SORD : mode==='status' ? STORD : reviewerIds.map(r => 'Reviewer '+r);
      groups.sort((a,b) => ((order.indexOf(a.g)+1)||99) - ((order.indexOf(b.g)+1)||99));
      holder.replaceChildren();
      for (const grp of groups){
        const tb = el('tbody',{});
        for (const c of [...grp.items].sort(within)){
          const v = vOf(c), r = rOf(c), byWho = {};
          for (const q of (c.reviewer_quotes||[])) (byWho[q.who] = byWho[q.who] || []).push(q.text);
          const row = el('tr',{class:'paper',tabindex:'0'},
            el('td',{}, c.id), el('td',{}, c.label), el('td',{}, sev(c.severity)), el('td',{}, statusChip(c)),
            el('td',{}, resChip(r.verdict)), el('td',{}, valChip(v.verdict)),
            reviewerIds.map(id => el('td',{class:'mark', title:(byWho[id]||[]).join(' / ') || null}, (c.raised_by||[]).includes(id) ? '●' : '')));
          const det = el('tr',{class:'detail',hidden:true}, el('td',{colspan:String(6+reviewerIds.length)},
            el('div',{class:'lbl',style:'margin-top:0'}, 'What the reviewers wrote ('+(c.raised_by||[]).join(', ')+')'), qs(c.reviewer_quotes,'against'),
            el('div',{class:'lbl'}, 'Authors ('+(c.response_type||'-')+')'), (c.author_response||[]).length ? qs(c.author_response) : el('div',{class:'muted'},'no response'),
            el('div',{class:'lbl'}, 'Reviewer follow-ups'), (c.followups||[]).length ? qs(c.followups) : el('div',{class:'muted'},'no reply'),
            el('div',{class:'lbl'}, 'Validity: '+v.verdict), (v.evidence||[]).length ? qs(v.evidence) : el('div',{class:'muted'},'no evidence quoted'),
            el('div',{class:'lbl'}, 'Resolution check: '+r.verdict), (r.evidence||[]).length ? qs(r.evidence) : el('div',{class:'muted'},'no evidence quoted')));
          const tog = () => { det.hidden = !det.hidden; };
          row.addEventListener('click', tog);
          row.addEventListener('keydown', e => { if (e.key==='Enter'||e.key===' '){ e.preventDefault(); tog(); } });
          put(tb, row, det);
        }
        put(holder, el('h4',{}, grp.g+' ('+grp.items.length+')'), el('div',{class:'scroll'}, el('table',{class:'grid'},
          el('thead',{}, el('tr',{}, ['ID','Concern','Importance','Status','Resolution check','Validity'].map(h => el('th',{}, h)), reviewerIds.map(id => el('th',{class:'mark'}, id)))), tb)));
      }
    };
    modeSel.addEventListener('change', draw);
    draw();
  }

  /* 2. disagreements and decisive points */
  const cruxes = (d.decision && d.decision.cruxes) || [];
  if (cruxes.length || (d.disagreements||[]).length){
    put(m, el('h3',{}, 'Disagreements and decisive points'));
    cruxes.forEach((c,i) => {
      const pro = [...new Set((c['for']||[]).map(x=>x.who).filter(REVIEWERISH))], con = [...new Set((c.against||[]).map(x=>x.who).filter(REVIEWERISH))];
      put(m, el('div',{class:'card'},
        el('h4',{style:'margin-top:0'}, 'Crux '+(i+1)+': '+c.label),
        el('div',{class:'two'},
          el('div',{}, el('div',{class:'side-h'}, el('span',{class:'bar',style:'background:var(--for)'}), 'For acceptance', pro.length ? el('span',{class:'muted'}, '('+pro.join(', ')+')') : null), qs(c['for'],'for')),
          el('div',{}, el('div',{class:'side-h'}, el('span',{class:'bar',style:'background:var(--against)'}), 'Against', con.length ? el('span',{class:'muted'}, '('+con.join(', ')+')') : null), qs(c.against,'against'))),
        c.would_flip ? el('div',{class:'flip'}, el('b',{},'Would flip: '), c.would_flip) : null));
    });
    if ((d.disagreements||[]).length) put(m, el('div',{class:'card'}, el('div',{class:'lbl',style:'margin-top:0'}, 'Where reviewers judge the same aspect differently'),
      d.disagreements.map(g => el('details',{class:'item'}, el('summary',{}, g.label), (g.sides||[]).map(s => [el('div',{class:'sub'}, s.position), qs(s.quotes)])))));
  }

  /* 2. reviewers */
  put(m, el('h3',{}, 'Where each reviewer stands, and what to ask'));
  const grid = el('div',{class:'revs'});
  for (const r of revs){
    const mine = P.thread.filter(t => t.who===r.id && t.kind!=='Official_Review');
    const last = mine[mine.length-1];
    const lastText = last ? (last.fields.find(f=>f[0]==='comment')||last.fields[0]||['',''])[1] : '';
    const cond = (d.quality||[]).filter(t => t.reviewer===r.id && t.tag==='conditional promise');
    const condText = cond.flatMap(t => (t.quotes||[]).map(x => x.text));
    const promises = (P.promises||[]).filter(x => x.reviewer===r.id && !condText.some(c => c.includes(x.text) || x.text.includes(c)));
    const flags = (d.quality||[]).filter(t => t.reviewer===r.id && t.tag!=='conditional promise');
    const raised = concerns.filter(c => (c.raised_by||[]).includes(r.id) && c.severity!=='Minor');
    const asks = (d.open_questions||[]).filter(o => o.to===r.id);
    const msgs = (P.messages||[]).filter(x => (x.to||[]).includes(r.id));
    put(grid, el('div',{class:'card rev'},
      el('h4',{}, el('span',{class:'big'}, (r.trajectory||[]).map(fmt).join(' → ')), el('span',{}, 'Reviewer '+r.id), el('span',{class:'muted'}, 'confidence '+fmt(r.confidence)),
        r.engaged ? el('span',{class:'status'}, el('span',{style:'color:var(--good)'},'✓'), 'engaged after rebuttal')
                  : el('span',{class:'status'}, el('span',{style:'color:var(--serious)'},'!'), 'silent after rebuttal'),
        r.pc_flags.length ? el('span',{class:'status'}, el('span',{style:'color:var(--warn)'},'⚑'), r.pc_flags.join('; ')) : null),
      el('div',{class:'lbl'}, last ? 'Latest words ('+last.date+')' : 'Latest words'),
      last ? el('blockquote',{}, lastText.length>600 ? lastText.slice(0,600)+' ...' : lastText) : el('div',{class:'muted'}, 'No comment after the rebuttal.'),
      promises.length ? [el('div',{class:'lbl'}, 'Score statements'), promises.map(x => el('blockquote',{}, x.text))] : null,
      cond.length ? [el('div',{class:'lbl'}, 'Conditional promise'), cond.map(t => [el('div',{class:'sub'}, 'condition met: '+(t.met||'?')), qs(t.quotes)])] : null,
      raised.length ? [el('div',{class:'lbl'}, 'Their Critical/Major concerns'), el('div',{class:'chips'}, raised.map(c => el('span',{class:'chip',title:c.label}, c.id+' '+c.label+' · '+c.status+' · '+vOf(c).verdict)))] : null,
      flags.length ? [el('div',{class:'lbl'}, 'Review-quality flags'), flags.map(t => el('details',{}, el('summary',{class:'sub'}, t.tag), qs(t.quotes)))] : null,
      asks.length ? [el('div',{class:'lbl'}, 'Ask them'), asks.map(o => el('details',{}, el('summary',{}, o.label), qs(o.quotes)))] : null,
      msgs.length ? [el('div',{class:'lbl'}, 'Messages'), msgs.map(x => msgBlock(x, x.id+' ('+x.channel+')'))] : null));
  }
  put(m, grid);
  const panelMsgs = (P.messages||[]).filter(x => x.channel==='forum');
  if (panelMsgs.length) put(m, el('div',{class:'card'}, el('div',{class:'lbl',style:'margin-top:0'}, 'Forum messages to all reviewers'),
    panelMsgs.map(x => msgBlock(x, x.id))));

  /* 3. strengths */
  if ((d.strengths||[]).length){
    put(m, el('h3',{}, 'Strengths'), el('div',{class:'card'}, d.strengths.map(st => el('details',{class:'item'},
      el('summary',{}, el('span',{}, st.label), el('span',{class:'muted'}, [...new Set((st.quotes||[]).map(x=>x.who))].join(', '))), qs(st.quotes,'for')))));
  }

  /* 4. AC only */
  const conf = (d.confidential||[]), dumpConf = (P.confidential||[]);
  if (conf.length || dumpConf.length || P.notes){
    put(m, el('h3',{}, 'For you only'));
    const card = el('div',{class:'card'});
    if (P.notes) put(card, el('div',{class:'lbl',style:'margin-top:0'}, 'Your notes (00_dashboard.md)'),
      el('table',{class:'kv'}, el('tbody',{}, Object.entries(P.notes).map(([k,v]) => el('tr',{}, el('td',{}, k), el('td',{}, v))))));
    if (conf.length) put(card, el('div',{class:'lbl'}, 'Confidential author notes (not visible to reviewers)'), conf.map(c => [el('div',{class:'sub'}, c.label||''), qs(c.quotes)]));
    else if (dumpConf.length) put(card, el('div',{class:'lbl'}, 'Confidential author notes (not visible to reviewers)'), dumpConf.map(x => el('blockquote',{}, el('span',{class:'who'}, x.date+' · '+x.title), x.prefix)));
    put(m, card);
  }

  /* 5. discussion thread */
  put(m, el('h3',{}, 'Discussion thread (verbatim)'));
  const box = el('div',{});
  const f = { reviews: el('input',{type:'checkbox'}), comments: el('input',{type:'checkbox',checked:true}), hidden: el('input',{type:'checkbox',checked:true}) };
  const draw = () => {
    box.replaceChildren();
    for (const t of P.thread){
      const isReview = t.kind==='Official_Review';
      if (isReview && !f.reviews.checked) continue;
      if (!isReview && !f.comments.checked) continue;
      if (!t.visible && !f.hidden.checked) continue;
      const cls = REVIEWERISH(t.who) ? 'reviewer' : (t.who==='Authors' ? 'authors' : (t.who==='AC' ? 'ac' : ''));
      const scores = Object.entries(t.scores||{}).map(([k,v]) => k+' '+v).join(' · ');
      const body = t.fields.map(([k,v]) => (t.fields.length>1 || isReview) ? [el('div',{class:'fk'}, k), el('div',{class:'f'}, v)] : el('div',{class:'f'}, v));
      const long = t.fields.reduce((s,[,v]) => s+v.length, 0) > 900;
      const head = el('div',{class:'h'}, el('b',{}, t.who), ' · '+t.kind.replace(/_/g,' ')+(t.to!=='paper'?' → '+t.to:'')+' · '+t.date+(t.visible?'':' · not visible to reviewers'), t.title ? [' · ', el('i',{}, t.title)] : null);
      put(box, el('div',{class:'post '+cls}, head, scores ? el('div',{class:'sub'}, scores) : null,
        long ? el('details',{}, el('summary',{class:'sub'}, 'show text'), body) : body));
    }
  };
  Object.values(f).forEach(x => x.addEventListener('change', draw));
  put(m, el('div',{class:'filters'}, el('label',{}, f.reviews, ' reviews'), el('label',{}, f.comments, ' comments'), el('label',{}, f.hidden, ' notes not visible to reviewers')), box);
  draw();
  return m;
}

function select(num){
  current = num;
  if (num==='actions') $('#main').replaceChildren(actionsView());
  else { draw = () => {}; $('#main').replaceChildren(paperView(D.papers.find(p => p.number===num))); }
  renderList();
  const theme = document.documentElement.dataset.theme;
  history.replaceState(null, '', '#'+(num==='actions'?'actions':'p'+num)+(theme?'&'+theme:''));
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', () => {
  const t = decodeURIComponent(location.hash.slice(1)).split('&')[0];
  if (t==='actions' && current!=='actions') select('actions');
  const mm = t.match(/^p(\d+)$/); if (mm && current!==Number(mm[1])) select(Number(mm[1]));
});

$('#title').textContent = D.title;
$('#meta').textContent = D.dump+' · '+D.generated;
$('#theme').addEventListener('click', () => {
  const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  r.dataset.theme = dark ? 'light' : 'dark';
  if (current!==null) history.replaceState(null, '', '#'+(current==='actions'?'actions':'p'+current)+'&'+r.dataset.theme);
});
let start = nOpen() || allDrafts().length ? 'actions' : (papers.find(p => !p.excluded) || papers[0]).number;
for (const t of decodeURIComponent(location.hash.slice(1)).split('&')){
  if (t==='dark'||t==='light') document.documentElement.dataset.theme = t;
  if (t==='actions') start = 'actions';
  const mm = t.match(/^p(\d+)$/); if (mm){ const P = D.papers.find(p => p.number===Number(mm[1])); if (P) start = P.number; }
}
select(start);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
