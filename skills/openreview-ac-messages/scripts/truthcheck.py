"""Truth checks, assembly and approval for AC message waves (used by messages.py).

A drafting subagent writes, per message, `<wave dir>/<id>.md` (the exact text) and
`<id>.claims.json`:
{
  "id": "1234-thread", "paper": 1234, "ref": "<dump stem>", "type": "thread", "channel": "forum",
  "to": ["Rv1a", "Rv2b"], "subject": "...",
  "sentences": [
    {"text": "<a sentence copied from the .md>", "kind": "fact",       # fact | quote | request | courtesy
     "support": [
       {"who": "Rv1a", "text": "<verbatim>"},                              # forum quote
       {"who": "Rv1a", "text": "<verbatim>", "latest": true},              # ... from Rv1a's latest visible note
       {"who": "pdf", "pdf": "1234_revised", "page": "21", "text": "<verbatim>"},
       {"rating": {"reviewer": "Rv1a", "value": 2}},                       # current rating (checked on the dump)
       {"dossier": "C4.status", "value": "Unresolved"}                     # a dossier field (checked on the dossier)
       {"author_reply": {"to": "Rv1a", "date": "2026-11-18"}}              # an author reply in Rv1a's thread that day
     ]}
  ],
  "author_replies_not_mentioned": {"Rv2b": "thanks only"}   # optional, see below
}
Unanswered author replies: if the message quotes a reviewer whom the authors answered after
that reviewer's latest comment (and the reviewer has not replied since), the message must
point the reviewer to the reply (an "author_reply" support item, or a quote from that reply),
or the claims file must say why not in "author_replies_not_mentioned" (the fact-checker
confirms the reason, for example "thanks only").
An independent fact-checker subagent writes `<id>.factcheck.json`:
{"id": "...", "sha256": "<sha256 of the exact text it checked>", "verdict": "pass" | "fail",
 "sentences": [{"text": "...", "status": "supported" | "courtesy" | "unsupported" | "misleading", "note": "..."}]}

`verify` passes a message only if: lint passes; every sentence of the text is covered by a
claims entry (except greetings and sign-off); every fact/quote entry has support; every
quoted span is a support quote; every support item checks out (verbatim and attributed;
from a note the recipients can read, so confidential comments never leak;
ratings match the dump; dossier fields match); and the fact-check report is a pass for
this exact text. Passing messages get a "verified" ledger event; `approve` adds an
"approved" event for the same text. The send-gate hook requires both.
"""

import datetime as _dt
import glob
import hashlib
import json
import os
import re
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-dossier", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-sync", "scripts"))
from verify_quotes import Sources, fold_ascii  # noqa: E402
import dashboard as db  # noqa: E402
import dumplib as dl  # noqa: E402

COURTESY = re.compile(r"^(Dear |Hello|Hi |Thank you|Thanks|Many thanks|Best|Kind regards|Your Area Chair)", re.I)
_QPAIR = re.compile(r'"([^"\n]*)"')  # pair every quote, so a short quote cannot shift the pairing


class _QSpan:
    @staticmethod
    def findall(text):
        return [q for q in _QPAIR.findall(text) if len(q) >= 12]


QSPAN = _QSpan()


def norm(text):
    return (text or "").replace("\r\n", "\n").strip()


def sha(text):
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def ws(s):
    """Whitespace-normalized, ASCII-folded, markdown bold removed (claims are written without it)."""
    return re.sub(r"\s+", " ", fold_ascii(s or "").replace("**", "")).strip()


def units(body):
    """Sentences of the text, split outside double quotes; list markers removed."""
    out = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if para.startswith("- "):
            para = para[2:]
        if not para:
            continue
        buf, inq = "", False
        for i, ch in enumerate(para):
            buf += ch
            if ch == '"':
                inq = not inq
            at_end = i + 1 == len(para) or para[i + 1] in " \n"
            closes_quote = ch == '"' and not inq and i > 0 and para[i - 1] in ".?!"
            if at_end and ((not inq and ch in ".?!") or closes_quote):
                if buf.strip():
                    out.append(buf.strip())
                buf = ""
        if buf.strip():
            out.append(buf.strip())
    return out


def ledger_path(wave_json):
    return os.path.join(os.path.dirname(os.path.abspath(wave_json)), "ledger.jsonl")


def ledger_events(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except json.JSONDecodeError:
                        pass
    return out


def sent_hashes(path):
    return {(e.get("id"), e.get("sha256")) for e in ledger_events(path) if e.get("event") == "sent"}


def append_ledger(path, entry):
    entry = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def assemble(wave_json, wave_no, date, dump_dir, from_dir=None, only=None):
    d = from_dir or os.path.splitext(wave_json)[0]
    msgs = []
    pattern = f"{only}-*.claims.json" if only else "*.claims.json"
    for cf in sorted(glob.glob(os.path.join(d, pattern))):
        with open(cf, encoding="utf-8") as f:
            c = json.load(f)
        mid = c["id"]
        with open(os.path.join(d, f"{mid}.md"), encoding="utf-8") as f:
            body = f.read().rstrip()
        quotes = []
        for s in c.get("sentences", []):
            for q in s.get("support", []):
                if isinstance(q, dict) and "who" in q and "text" in q and q not in quotes:
                    quotes.append(q)
        msgs.append({"id": mid, "ref": c.get("ref"), "paper": c.get("paper"), "type": c.get("type"),
                     "channel": c.get("channel"), "to": c.get("to", []),
                     **({"readers": c.get("readers", "reviewers")} if c.get("channel") == "forum" else {}),
                     "subject": c.get("subject", ""), "body": body, "claims": c.get("sentences", []), "quotes": quotes,
                     **({"author_replies_not_mentioned": c["author_replies_not_mentioned"]} if c.get("author_replies_not_mentioned") else {})})
    wave = {"wave": wave_no, "date": date, "dump_dir": dump_dir, "messages": msgs}
    with open(wave_json, "w", encoding="utf-8") as f:
        json.dump(wave, f, indent=1, ensure_ascii=False)
    return len(msgs)


def resolve_dossier(dossier, path):
    parts = path.split(".")
    obj = next((c for c in dossier.get("concerns", []) if c.get("id") == parts[0]), None)
    if obj is not None:
        parts = parts[1:]
    else:
        obj = dossier
    for p in parts:
        if isinstance(obj, dict) and p in obj:
            obj = obj[p]
        else:
            return KeyError(path)
    return obj


_DUMPS = {}


def _forum(dump_dir, ref):
    key = (dump_dir, ref)
    if key not in _DUMPS:
        with open(os.path.join(dump_dir, f"{ref}.json"), encoding="utf-8") as fh:
            _DUMPS[key] = json.load(fh)
    return _DUMPS[key]


def author_replies_in_thread(dump_dir, ref, rid):
    """Author notes that all reviewers can read and that sit under one of reviewer rid's notes."""
    data = _forum(dump_dir, ref)
    by_id = {n["id"]: n for n in data.get("replies", [])}
    out = []
    for n in data.get("replies", []):
        if dl.signer(n) != "Authors" or not dl.visible_to_all_reviewers(n):
            continue
        p, hops = by_id.get(n.get("replyto")), 0
        while p is not None and not dl.signer(p).startswith("Reviewer_") and hops < 50:
            p, hops = by_id.get(p.get("replyto")), hops + 1
        if p is not None and dl.short_id(dl.signer(p)) == rid:
            out.append(n)
    return out


def unanswered_problems(m, claims, dump_dir, cf_extra):
    """Reviewers quoted in the message whom the authors answered after their latest comment."""
    ref = m.get("ref")
    try:
        ua = dl.unanswered_author_replies(_forum(dump_dir, ref))
    except FileNotFoundError:
        return []
    sup = [q for c in claims for q in c.get("support", []) if isinstance(q, dict)]
    quoted = set()  # reviewers whose words are shown in the text (not only used as evidence)
    for c in claims:
        spans = [ws(x) for x in QSPAN.findall(fold_ascii(c.get("text", "")))]
        for q in c.get("support", []):
            if isinstance(q, dict) and q.get("who") not in (None, "Authors", "pdf", "AC", "PC") and \
                    any(ws(q.get("text", "")) in sp or sp in ws(q.get("text", "")) for sp in spans):
                quoted.add(q["who"])
    mentioned = {q["author_reply"].get("to") for q in sup if "author_reply" in q}
    probs = []
    for rid in sorted(quoted):
        notes = ua.get(rid)
        if not notes or rid in mentioned or rid in cf_extra:
            continue
        texts = [s for n in notes for s in dl.note_strings(n)]
        if any(q.get("who") == "Authors" and any(fold_ascii(q["text"]) in fold_ascii(t) for t in texts) for q in sup):
            continue
        days = sorted({dl.ts(n.get("cdate"))[:10] for n in notes})
        probs.append(f"{rid} is quoted, but the authors answered {rid} on {', '.join(days)} after {rid}'s latest comment: "
                     f"point {rid} to the reply (author_reply support) or give the reason in author_replies_not_mentioned")
    return probs


def verify(w, wave_json, a, lint_fn):
    srcs = Sources(a.dump_dir, a.pdf_text_dir, fold=True)
    status = {P["number"]: P for P in db.collect(SimpleNamespace(
        dump_dir=a.dump_dir, prev=[], extra_fields="", comment_suffixes="Official_Comment,Rebuttal,Author_Comment,Comment",
        review_suffix=a.review_suffix, rating_field=a.rating_field, confidence_field="confidence", boundary=None))}
    dossiers = {}
    for f in glob.glob(os.path.join(a.dossiers, "*_dossier.json")):
        with open(f, encoding="utf-8") as fh:
            dd = json.load(fh)
        dossiers[dd.get("number")] = dd
    wd = getattr(a, "from_dir", None) or os.path.splitext(wave_json)[0]
    lint_problems = {}
    for mid, p in lint_fn(w):
        lint_problems.setdefault(mid, []).append(p)
    led, n_ok = ledger_path(wave_json), 0
    sent = sent_hashes(led)
    for m in w.get("messages", []):
        mid, body, ref = m["id"], m.get("body", ""), m.get("ref")
        if (mid, sha(body)) in sent:
            n_ok += 1
            print(f"SENT {mid} (already sent; not re-checked against the new dump)")
            continue
        probs = list(lint_problems.get(mid, []))
        claims = m.get("claims")
        if not claims:
            probs.append("no claims file: every factual sentence needs evidence")
            claims = []
        ctexts = [ws(c.get("text", "")) for c in claims]
        for u in units(body):
            if COURTESY.match(u) or u.strip() == a.signoff:
                continue
            if not any(ws(u) in ct for ct in ctexts):
                probs.append(f"sentence without a claims entry: {u[:90]!r}")
        bws = ws(body)
        for c in claims:
            t, kind, sup = c.get("text", ""), c.get("kind"), c.get("support", [])
            if ws(t) not in bws:
                probs.append(f"claims entry not found in the text: {t[:80]!r}")
            if kind in ("fact", "quote") and not sup:
                probs.append(f"{kind} without support: {t[:80]!r}")
            qtexts = [ws(q.get("text", "")) for q in sup if isinstance(q, dict) and "who" in q]
            for span in QSPAN.findall(fold_ascii(t)):
                if not any(ws(span) == qt or ws(span) in qt for qt in qtexts):
                    probs.append(f"quoted text without a matching support quote: {span[:70]!r}")
            for q in sup:
                if not isinstance(q, dict):
                    probs.append(f"bad support item in {t[:50]!r}")
                elif "who" in q and "text" in q:
                    to = m.get("to", [])
                    audience = to[0] if m.get("channel") == "email" and len(to) == 1 else "reviewers"
                    try:
                        err = srcs.check(ref, q, audience=audience)
                    except FileNotFoundError as e:
                        err = f"source missing: {e.filename}"
                    if err:
                        probs.append(f"support quote fails ({err}): {q['text'][:70]!r}")
                elif "rating" in q:
                    rv = q["rating"]
                    P = status.get(m.get("paper"), {})
                    cur = next((r.get("rating") for r in P.get("reviews", []) if r.get("id") == rv.get("reviewer")), None)
                    if cur is None or float(cur) != float(rv.get("value", -1)):
                        probs.append(f"rating claim wrong: {rv} (forum says {cur})")
                elif "dossier" in q:
                    got = resolve_dossier(dossiers.get(m.get("paper"), {}), q["dossier"])
                    want = q.get("value")
                    ok = (isinstance(got, list) and isinstance(want, list) and set(want) <= set(got)) or got == want
                    if not ok:
                        probs.append(f"dossier claim wrong: {q['dossier']} = {want!r} (dossier has {got!r})")
                elif "author_reply" in q:
                    ar = q["author_reply"]
                    days = {dl.ts(n.get("cdate"))[:10] for n in author_replies_in_thread(a.dump_dir, ref, ar.get("to"))}
                    if ar.get("date") not in days:
                        probs.append(f"author_reply claim wrong: no author reply in {ar.get('to')}'s thread on {ar.get('date')} (found {sorted(days)})")
                else:
                    probs.append(f"unknown support item: {q}")
        probs += unanswered_problems(m, claims, a.dump_dir, cf_extra=m.get("author_replies_not_mentioned") or {})
        fc_path = os.path.join(wd, f"{mid}.factcheck.json")
        if getattr(a, "no_factcheck", False):
            pass
        elif not os.path.exists(fc_path):
            probs.append("no independent fact-check report")
        else:
            with open(fc_path, encoding="utf-8") as fh:
                fc = json.load(fh)
            if fc.get("sha256") != sha(body):
                probs.append("fact-check report is for a different version of the text (re-run the fact-checker)")
            elif fc.get("verdict") != "pass" or any(s.get("status") not in ("supported", "courtesy") for s in fc.get("sentences", [])):
                bad = [s.get("text", "")[:60] for s in fc.get("sentences", []) if s.get("status") not in ("supported", "courtesy")]
                probs.append(f"fact-checker did not pass it: {bad[:3]}")
        if probs:
            print(f"FAIL {mid}")
            for p in probs:
                print(f"     - {p}")
        elif getattr(a, "no_factcheck", False):
            n_ok += 1
            print(f"PASS {mid} (mechanical checks only; not recorded)")
        else:
            n_ok += 1
            append_ledger(led, {"event": "verified", "id": mid, "paper": m.get("paper"), "channel": m.get("channel"),
                                "to": m.get("to", []), "subject": m.get("subject", ""), "sha256": sha(body)})
            print(f"PASS {mid} (verified, recorded in {os.path.basename(led)})")
    print(f"{n_ok}/{len(w.get('messages', []))} messages verified")
    return n_ok == len(w.get("messages", []))


def approve(w, wave_json, ids, user_said):
    led = ledger_path(wave_json)
    entries = []
    if os.path.exists(led):
        with open(led, encoding="utf-8") as f:
            entries = [json.loads(x) for x in f if x.strip()]
    ok = True
    for mid in ids:
        m = next((x for x in w.get("messages", []) if x["id"] == mid), None)
        if m is None:
            print(f"SKIP {mid}: not in this wave")
            ok = False
            continue
        h = sha(m.get("body", ""))
        if not any(e.get("event") == "verified" and e.get("sha256") == h for e in entries):
            print(f"SKIP {mid}: the current text is not verified; run verify first")
            ok = False
            continue
        append_ledger(led, {"event": "approved", "id": mid, "sha256": h, "user_said": user_said})
        print(f"APPROVED {mid}")
    return ok
