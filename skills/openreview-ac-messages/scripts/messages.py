#!/usr/bin/env python3
"""Assemble, verify, approve, render and send-prepare a wave of AC messages. Nothing is sent from here:
the send itself is an MCP call (post_forum_message / send_bulk_message) guarded by hooks/send_gate.py.

Wave folder: ac_discussion/messages/wave<k>_<date>/ with, per message, <id>.md (the exact text),
<id>.claims.json (the evidence for every sentence) and <id>.factcheck.json (the independent check).
Message ids: <num>-thread (forum thread to all reviewers), <num>-email-<rid> (email to one reviewer),
<num>-chairs (forum note readable by the SAC and PCs only).

Commands, in workflow order:
  assemble   WAVE.json --wave K --date D --dump-dir DUMP      # wave folder -> WAVE.json
  verify     WAVE.json --dump-dir DUMP --pdf-text-dir PDFTXT --dossiers DIR --no-author-replies [--no-factcheck]
                                                             # truth checks + fact-check reports -> "verified" ledger events
  render     WAVE.json [--out WAVE.md]                       # review file, escaped so it can be copied from a viewer
  approve    WAVE.json --ids ID1,ID2 --user-said "<user's words>"   # AFTER the last verify, only when the user said send
  send-plan  WAVE.json --signatures SIG.json --venue VENUE --ids ID1,ID2   # exact MCP arguments -> WAVE.send_plan.json
  print-args WAVE.json --id ID [--dry-run]                   # the JSON arguments for one MCP call, from the send plan
  gate       WAVE.json --ids ID1,ID2 --transcript PATH       # run hooks/send_gate.py on each planned call (no plugin installed)
  log-sent   WAVE.json --id ID --note-id NOTE                # after a successful send: ledger "sent" + sent_log.md
  feedback   WAVE.json [--resolve ID --note "what changed"]  # the user's verdicts, comments and edits from review_server.py
  export / import / lint                                     # JSON <-> .md round trip; style lint only

SIG.json maps paper numbers to the AC's anonymous group, from get_forum_message_options:
  {"1234": "ICLR.cc/2027/Conference/Submission1234/Area_Chair_AbCd", ...}
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "openreview-ac-dossier", "scripts"))
from verify_quotes import fold_ascii  # noqa: E402

QUOTED_ANY = re.compile(r'"([^"\n]*)"')  # pair every quote, so a short quote cannot shift the pairing


class _Quoted:
    """Quoted spans of 25+ characters (shorter ones are paired but not checked)."""
    @staticmethod
    def findall(text):
        return [q for q in QUOTED_ANY.findall(text) if len(q) >= 25]

    @staticmethod
    def sub(repl, text):
        return QUOTED_ANY.sub(repl, text)


QUOTED = _Quoted()
# Questions that only new author work could answer; useless once authors can no longer reply.
NEW_WORK = re.compile(r"would (settle|change|convince|resolve|move)|which (new |additional )?(experiment|baseline|result|analysis|evidence)|what (new |additional |further )?(experiment|baseline|result|analysis|evidence)|additional experiments? (would|could)|could the authors|ask the authors to", re.I)


def lint(w, max_forum, max_title, signoff, no_author_replies=False):
    problems = []
    seen = set()
    for m in w.get("messages", []):
        mid = m.get("id", "?")
        if mid in seen:
            problems.append((mid, "duplicate id"))
        seen.add(mid)
        body = m.get("body", "")
        if m.get("type") not in ("thread", "email", "chairs") + tuple("ABCDE"):
            problems.append((mid, f"type {m.get('type')!r} is not thread, email or chairs"))
        if m.get("channel") not in ("email", "forum"):
            problems.append((mid, "channel must be email or forum"))
        if m.get("channel") == "email" and not m.get("to"):
            problems.append((mid, "email without recipients"))
        if m.get("channel") == "forum":
            if len(body) > max_forum:
                problems.append((mid, f"forum body {len(body)} chars > {max_forum}"))
            if len(m.get("subject", "")) > max_title:
                problems.append((mid, f"title > {max_title} chars"))
            if "author" in str(m.get("readers", "")).lower():
                problems.append((mid, "readers include authors"))
        bad = sorted({ch for ch in body if ord(ch) > 127})
        if bad:
            problems.append((mid, f"non-ASCII characters {bad} (fold them; quotes are compared ASCII-folded)"))
        if any(ord(ch) > 127 for ch in m.get("subject", "")):
            problems.append((mid, "non-ASCII characters in subject"))
        if "\t" in body or "\r" in body:
            problems.append((mid, "tabs or carriage returns in body (plain markdown uses spaces and \\n)"))
        if re.search(r"</?[A-Za-z][^>]*>", body):
            problems.append((mid, "HTML tag in body (plain markdown only)"))
        if "```" in body or re.search(r"^\s*\|.*\|\s*$", body, re.M):
            problems.append((mid, "code fence or table in body (keep to paragraphs and simple lists)"))
        if not body.rstrip().endswith(signoff):
            problems.append((mid, f"does not end with sign-off {signoff!r}"))
        if m.get("channel") == "email" and "**" in body:
            problems.append((mid, "markdown bold in an email (emails may arrive as plain text; bold only in forum posts)"))
        for span in re.findall(r"\*\*([^*\n]+)\*\*", body):
            if span not in m.get("to", []):
                problems.append((mid, f"bold on {span[:40]!r}: use bold sparingly, only for reviewer IDs"))
        for ln in body.splitlines():
            if re.match(r"^\s*\d+[.)]\s", ln) and "**" in ln:
                problems.append((mid, f"bold inside numbered list: {ln[:60]!r}"))
        qsrc = m.get("quotes", []) or [q for c in m.get("claims", []) for q in c.get("support", []) if isinstance(q, dict) and "text" in q]
        qtexts = [fold_ascii(q.get("text", "")) for q in qsrc]
        if no_author_replies:
            unquoted = QUOTED.sub("", body)
            for hit in NEW_WORK.finditer(unquoted):
                problems.append((mid, f"asks for new author work, but authors can no longer reply: {hit.group(0)!r}"))
        if not m.get("claims"):
            # template drafts: every quote object must be shown in the text. With a claims file, support quotes may
            # back a paraphrased sentence, so only quotes shown in the text are checked (below and in verify).
            for qt in qtexts:
                if qt and qt not in body:
                    problems.append((mid, f"quote not found in body: {qt[:60]!r}"))
        for span in QUOTED.findall(body):
            if not any(span in qt or qt in span for qt in qtexts):
                problems.append((mid, f"quoted text in body has no quote object: {span[:60]!r}"))
    return problems


MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~#])")


def md_escape(body):
    """Escape a markdown body so a markdown viewer DISPLAYS its source: copying the rendered text
    gives exactly the markdown to paste in the forum (bold markers included). Single line breaks
    become hard breaks so they survive the copy."""
    lines = body.rstrip().split("\n")
    out = []
    for i, ln in enumerate(lines):
        e = MD_SPECIAL.sub(r"\\\1", ln)
        e = re.sub(r"^(\s*)([-+])(\s)", r"\1\\\2\3", e)          # list markers shown literally
        e = re.sub(r"^(\s*\d+)([.)])(\s)", r"\1\\\2\3", e)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        out.append(e + ("  " if ln.strip() and nxt.strip() else ""))
    return "\n".join(out)


def render(w):
    L = [f"# Messages wave {w.get('wave', '?')} ({w.get('date', '')})", "",
         "Nothing here has been sent. Review each message; reply 'send <ids>' to send.", "",
         "Each message is shown escaped: in a markdown viewer you see its exact markdown source (for example `**Rv1a**`), "
         "so copying the rendered text gives exactly what to paste in the forum. The unescaped source is `<id>.md` in the wave folder.", ""]
    L.append("| id | paper | type | channel | to | subject |")
    L.append("|---|---|---|---|---|---|")
    for m in w.get("messages", []):
        L.append(f"| {m.get('id')} | {m.get('paper')} | {m.get('type')} | {m.get('channel')} | {', '.join(m.get('to', []))} | {m.get('subject', '')} |")
    L.append("")
    for m in w.get("messages", []):
        L += [f"## {m.get('id')}", "",
              f"- paper {m.get('paper')}, type {m.get('type')}, channel {m.get('channel')}, to {', '.join(m.get('to', [])) or '-'}"
              + (f", readers {m.get('readers')}" if m.get("channel") == "forum" else ""),
              f"- subject: {m.get('subject', '')}", f"- length: {len(m.get('body', ''))} chars",
              f"- editable file: {m.get('id')}.md (in the wave folder)", "",
              "---", "", md_escape(m.get("body", "")), "", "---", ""]
    return "\n".join(L) + "\n"


def wave_dir(wave_json):
    return os.path.splitext(wave_json)[0]


def export(w, wave_json):
    d = wave_dir(wave_json)
    os.makedirs(d, exist_ok=True)
    for m in w.get("messages", []):
        with open(os.path.join(d, f"{m['id']}.md"), "w", encoding="ascii", errors="strict", newline="\n") as f:
            f.write(m.get("body", "").rstrip() + "\n")
    return d


def import_bodies(w, wave_json):
    d, changed = wave_dir(wave_json), []
    for m in w.get("messages", []):
        path = os.path.join(d, f"{m['id']}.md")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            body = f.read().rstrip()
        if body != m.get("body", "").rstrip():
            m["body"] = body
            # drop quote objects whose text the edit removed (edited quotes are caught by lint)
            m["quotes"] = [q for q in m.get("quotes", []) if fold_ascii(q.get("text", "")) in body]
            if m.get("claims"):
                # claims whose sentence was edited away are dropped; edited sentences need a new claims entry (verify flags them)
                ws_body = re.sub(r"\s+", " ", body)
                m["claims"] = [c for c in m["claims"] if re.sub(r"\s+", " ", c.get("text", "")).strip() in ws_body]
            changed.append(m["id"])
    return changed


def show_feedback(w, wave_json, resolve=None, note=""):
    """Print the user's latest verdict, comment and edit per message (from review_server.py)."""
    import datetime as _dt
    import difflib
    mdir = os.path.dirname(os.path.abspath(wave_json))
    path = os.path.join(mdir, "feedback.jsonl")
    if resolve:
        if resolve not in {m["id"] for m in w.get("messages", [])}:
            raise SystemExit(f"unknown id {resolve}")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "id": resolve,
                                "status": "addressed", "comment": note or None, "by": "assistant"}) + "\n")
        print(f"marked {resolve} addressed")
        return
    latest = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                latest[e.get("id")] = e
    wdir = os.path.splitext(wave_json)[0]
    n = 0
    for m in w.get("messages", []):
        e = latest.get(m["id"])
        ed = os.path.join(wdir, f"{m['id']}.edited.md")
        if not e and not os.path.exists(ed):
            continue
        n += 1
        print(f"== {m['id']}: {(e or {}).get('status', '-')} ({(e or {}).get('by', '-')}, {(e or {}).get('ts', '')})")
        if e and e.get("comment"):
            print(f"   comment: {e['comment']}")
        if os.path.exists(ed):
            with open(ed, encoding="utf-8") as f:
                new = f.read().rstrip().splitlines()
            diff = list(difflib.unified_diff(m.get("body", "").rstrip().splitlines(), new, "verified", "edited", lineterm="", n=0))
            print(f"   edited text in {os.path.relpath(ed)}:")
            for ln in diff[2:]:
                print("     " + ln)
    if not n:
        print("no feedback yet")


SEND_ORDER = {"thread": 0, "chairs": 1, "email": 2}


def send_plan(w, ids, sigs, venue):
    """Exact MCP arguments per approved message: forum threads and chairs notes first, emails last."""
    by = {m["id"]: m for m in w.get("messages", [])}
    missing = [i for i in ids if i not in by]
    if missing:
        raise SystemExit(f"unknown ids: {missing}")
    plan = []
    for i in ids:
        m = by[i]
        n = str(m["paper"])
        if n not in sigs or "Area_Chair" not in sigs[n] or sigs[n].startswith("~"):
            raise SystemExit(f"{i}: no anonymous Area_Chair signature for paper {n} in --signatures")
        if m["channel"] == "forum":
            readers = "sac_only" if m.get("type") == "chairs" or m.get("readers") in (["Program_Chairs", "Senior_Area_Chairs"], "sac_only") else "reviewers"
            plan.append({"id": i, "tool": "post_forum_message", "order": SEND_ORDER.get(m.get("type"), 0),
                         "args": {"venue_id": venue, "submission_id_or_number": int(n), "comment": m["body"],
                                  "title": m.get("subject", ""), "readers": readers, "signature": sigs[n]}})
        else:
            plan.append({"id": i, "tool": "send_bulk_message", "order": 2,
                         "args": {"venue_id": venue, "recipients": [f"{venue}/Submission{n}/Reviewer_{r}" for r in m["to"]],
                                  "subject": m.get("subject", ""), "message": m["body"],
                                  "invitation": f"{venue}/Submission{n}/-/Message",
                                  "parent_group": f"{venue}/Submission{n}/Reviewers", "signature": sigs[n]}})
    plan.sort(key=lambda x: x["order"])
    return plan


def plan_entry(plan_path, mid):
    with open(plan_path, encoding="utf-8") as f:
        for x in json.load(f):
            if x["id"] == mid:
                return x
    raise SystemExit(f"{mid} is not in {plan_path}; run send-plan first")


def run_gate(plan_path, ids, transcript, cwd):
    import subprocess
    gate = os.path.join(HERE, "..", "..", "..", "hooks", "send_gate.py")
    ok = True
    for i in ids:
        x = plan_entry(plan_path, i)
        inp = json.dumps({"tool_name": f"mcp__openreview__{x['tool']}", "tool_input": dict(x["args"], dry_run=False),
                          "cwd": cwd, "transcript_path": transcript})
        env = dict(os.environ, OPENREVIEW_AC_LEDGER=os.path.join(cwd, "ledger.jsonl"))
        r = subprocess.run([sys.executable, "-I", gate], input=inp, capture_output=True, text=True, env=env)
        print(f"{i:22} gate {'PASS' if r.returncode == 0 else 'BLOCK'} {r.stderr.strip()[:200]}")
        ok = ok and r.returncode == 0
    return ok


def log_sent(w, wave_json, mid, note_id):
    import datetime as _dt
    import truthcheck as tc
    m = next((x for x in w.get("messages", []) if x["id"] == mid), None)
    if m is None:
        raise SystemExit(f"unknown id {mid}")
    tc.append_ledger(tc.ledger_path(wave_json), {"event": "sent", "id": mid, "sha256": tc.sha(m.get("body", "")), "note_id": note_id})
    log = os.path.join(os.path.dirname(os.path.abspath(wave_json)), "sent_log.md")
    if not os.path.exists(log):
        with open(log, "w", encoding="utf-8") as f:
            f.write("# Sent log\n\n| time (UTC) | id | channel | paper | note/message id | to |\n|---|---|---|---|---|---|\n")
    who = "PCs, SACs, ACs" + ("" if m.get("type") == "chairs" else ", Reviewers") if m["channel"] == "forum" else ", ".join(m.get("to", []))
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"| {now} | {mid} | {m['channel']} | {m.get('paper')} | {note_id} | {who} |\n")
    print(f"logged {mid} ({note_id})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["assemble", "export", "import", "lint", "verify", "approve", "render",
                                    "send-plan", "print-args", "gate", "log-sent", "feedback"])
    ap.add_argument("wave_json")
    ap.add_argument("--out")
    ap.add_argument("--max-forum-chars", type=int, default=5000)
    ap.add_argument("--max-title-chars", type=int, default=500)
    ap.add_argument("--signoff", default="Your Area Chair")
    ap.add_argument("--no-author-replies", action="store_true",
                    help="the phase no longer allows author replies: flag questions only new author work could answer")
    ap.add_argument("--dump-dir", help="verify/assemble: forum dump folder")
    ap.add_argument("--pdf-text-dir", help="verify: pdf_text folder")
    ap.add_argument("--dossiers", help="verify: folder with <num>_dossier.json")
    ap.add_argument("--review-suffix", default="Official_Review")
    ap.add_argument("--rating-field", default="rating")
    ap.add_argument("--wave", type=int, default=1, help="assemble: wave number")
    ap.add_argument("--date", default="", help="assemble: wave date")
    ap.add_argument("--ids", default="", help="approve: comma-separated message ids the user approved")
    ap.add_argument("--user-said", default="", help="approve: the user's approval message, verbatim")
    ap.add_argument("--from-dir", help="assemble/verify: read drafts from this folder instead of <wave stem>/")
    ap.add_argument("--only", help="assemble: only messages of this paper number")
    ap.add_argument("--no-factcheck", action="store_true", help="verify: mechanical checks only (drafting self-check); never records")
    ap.add_argument("--signatures", help="send-plan: JSON {paper number: anonymous AC group}")
    ap.add_argument("--venue", help="send-plan: venue id, e.g. ICLR.cc/2027/Conference")
    ap.add_argument("--id", help="print-args / log-sent: one message id")
    ap.add_argument("--dry-run", action="store_true", help="print-args: set dry_run true")
    ap.add_argument("--transcript", help="gate: this session's transcript .jsonl (the gate looks for the user's 'send <id>')")
    ap.add_argument("--note-id", help="log-sent: the note or message id OpenReview returned")
    ap.add_argument("--resolve", help="feedback: mark this id addressed (after applying the user's comment or edit)")
    ap.add_argument("--note", default="", help="feedback --resolve: one line for the user on what changed")
    a = ap.parse_args()
    import truthcheck as tc
    if a.cmd == "assemble":
        n = tc.assemble(a.wave_json, a.wave, a.date, a.dump_dir, from_dir=a.from_dir, only=a.only)
        print(f"assembled {n} drafts into {a.wave_json}")
        return
    with open(a.wave_json, encoding="utf-8") as f:
        w = json.load(f)
    if a.cmd == "verify":
        ok = tc.verify(w, a.wave_json, a, lambda wave: lint(wave, a.max_forum_chars, a.max_title_chars, a.signoff, a.no_author_replies))
        sys.exit(0 if ok else 1)
    if a.cmd == "approve":
        if not a.ids or not a.user_said:
            raise SystemExit("approve needs --ids and --user-said (the user's own words)")
        ok = tc.approve(w, a.wave_json, [x.strip() for x in a.ids.split(",") if x.strip()], a.user_said)
        sys.exit(0 if ok else 1)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    plan_path = os.path.splitext(a.wave_json)[0] + ".send_plan.json"
    if a.cmd == "send-plan":
        if not (a.signatures and a.venue and ids):
            raise SystemExit("send-plan needs --signatures, --venue and --ids")
        with open(a.signatures, encoding="utf-8") as f:
            sigs = {str(k): v for k, v in json.load(f).items()}
        plan = send_plan(w, ids, sigs, a.venue)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1, ensure_ascii=False)
        for x in plan:
            t = x["args"]
            print(f"{x['id']:22} {x['tool']:18} {t.get('readers') or t['recipients'][0].split('/')[-1]:12} "
                  f"{t['signature'].split('/')[-1]}")
        print(f"wrote {plan_path}: threads and chairs notes first, emails last")
        return
    if a.cmd == "print-args":
        x = plan_entry(plan_path, a.id)
        args = dict(x["args"], dry_run=bool(a.dry_run))
        print(json.dumps({"tool": f"mcp__openreview__{x['tool']}", "arguments": args}, indent=1, ensure_ascii=False))
        return
    if a.cmd == "gate":
        if not (a.transcript and ids):
            raise SystemExit("gate needs --ids and --transcript")
        ok = run_gate(plan_path, ids, a.transcript, os.path.dirname(os.path.abspath(a.wave_json)))
        sys.exit(0 if ok else 2)
    if a.cmd == "log-sent":
        if not (a.id and a.note_id):
            raise SystemExit("log-sent needs --id and --note-id")
        log_sent(w, a.wave_json, a.id, a.note_id)
        return
    if a.cmd == "feedback":
        show_feedback(w, a.wave_json, a.resolve, a.note)
        return
    if a.cmd == "export":
        print(f"wrote {len(w.get('messages', []))} drafts to {export(w, a.wave_json)}/")
        return
    if a.cmd == "import":
        changed = import_bodies(w, a.wave_json)
        with open(a.wave_json, "w", encoding="utf-8") as f:
            json.dump(w, f, indent=1, ensure_ascii=False)
        print(f"imported {len(changed)} edited drafts: {', '.join(changed) or '-'} (re-run verify and lint)")
        return
    if a.cmd == "lint":
        probs = lint(w, a.max_forum_chars, a.max_title_chars, a.signoff, a.no_author_replies)
        for mid, p in probs:
            print(f"LINT {mid}: {p}")
        print(f"{len(w.get('messages', []))} messages, {len(probs)} problems")
        sys.exit(1 if probs else 0)
    out = a.out or os.path.splitext(a.wave_json)[0] + ".md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(w))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
