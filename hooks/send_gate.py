#!/usr/bin/env python3
"""PreToolUse gate for OpenReview send tools (openreview plugin).

Blocks a real send (dry_run=False) of send_bulk_message / post_forum_message unless:
  0. the exact text was not already sent;
  1. the exact text (and its subject or title) is recorded in the workspace ledger
     (ac_discussion/messages/ledger.jsonl, or $OPENREVIEW_AC_LEDGER) with a "verified"
     event (all truth checks passed) followed by an "approved" event for the same hash;
  2. a genuine user message in this session's transcript names that message id together
     with the word "send" (the approval must come from the user, not from Claude);
  3. the call targets the right place: the paper's own invitation/forum, the paper's
     anonymous Area_Chair signature (never a real profile), the recorded reviewers, and
     no Authors readers.
Bulk / templated send tools (batch_post_forum_messages, send_reminders) are always
blocked for real sends. Dry runs and every other tool pass through.

Exit 2 with a reason on stderr blocks the call (Claude sees the reason).
"""

import hashlib
import json
import os
import re
import sys

SENDERS = ("send_bulk_message", "post_forum_message")
BULK = ("batch_post_forum_messages", "send_reminders")


def norm(text):
    return (text or "").replace("\r\n", "\n").strip()


def sha(text):
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def block(reason):
    sys.stderr.write("openreview send gate: " + reason + "\n")
    sys.exit(2)


def find_ledger(cwd):
    env = os.environ.get("OPENREVIEW_AC_LEDGER")
    if env and os.path.exists(env):
        return env
    d = os.path.abspath(cwd or os.getcwd())
    while True:
        cand = os.path.join(d, "ac_discussion", "messages", "ledger.jsonl")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def ledger_entries(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    return out


def genuine_user_texts(transcript_path):
    """Text the user actually typed (not tool results, agent reports or system notices)."""
    texts = []
    if not transcript_path or not os.path.exists(transcript_path):
        return texts
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
                continue
            msg = o.get("message") or {}
            content = msg.get("content")
            parts = [content] if isinstance(content, str) else [
                c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text"]
            for t in parts:
                if not t or any(tag in t for tag in ("<agent-message", "<task-notification", "[SYSTEM NOTIFICATION",
                                                       "tool_use_id", "<local-command", "<command-name>")):
                    continue
                texts.append(t)
    return texts


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    short = tool.split("__")[-1]
    if short not in SENDERS + BULK:
        sys.exit(0)
    ti = data.get("tool_input") or {}
    dry = ti.get("dry_run", True)
    if dry is True or str(dry).lower() == "true":
        sys.exit(0)  # previews are always fine
    if short in BULK:
        block(f"{short} is not allowed for real sends: send each approved message with send_bulk_message or "
              "post_forum_message (openreview-ac-messages rules).")

    text = ti.get("message") if short == "send_bulk_message" else ti.get("comment")
    if not norm(text):
        block("empty message text.")
    h = sha(text)
    ledger = find_ledger(data.get("cwd"))
    if not ledger:
        block("no ledger found (ac_discussion/messages/ledger.jsonl). Run `messages.py verify` and, after the user "
              "says send, `messages.py approve` first.")
    entries = [e for e in ledger_entries(ledger) if e.get("sha256") == h]
    verified = [e for e in entries if e.get("event") == "verified"]
    if not verified:
        block("this exact text was never verified (or it changed after verification). Re-run `messages.py verify` "
              "on the wave and show the user the final text.")
    if any(e.get("event") == "sent" for e in entries):
        block("this exact text was already sent (ledger has a 'sent' event). Do not send it twice.")
    v = verified[-1]
    approved = [e for e in entries if e.get("event") == "approved" and e.get("ts", "") >= v.get("ts", "")]
    if not approved:
        block(f"message {v.get('id')} is verified but not approved. Only after the user explicitly says "
              f"'send {v.get('id')}', run `messages.py approve --ids {v.get('id')}`.")
    mid, paper = str(v.get("id")), str(v.get("paper"))

    users = genuine_user_texts(data.get("transcript_path"))
    if not any(re.search(r"\bsend\b", t, re.I) and mid in t for t in users):
        block(f"no message from the user in this session says 'send' together with the id {mid}. Ask the user to "
              f"approve it explicitly by id.")

    sent_subject = norm(ti.get("subject") if short == "send_bulk_message" else ti.get("title"))
    if v.get("subject") is not None and sent_subject != norm(v.get("subject")):
        block(f"the {'subject' if short == 'send_bulk_message' else 'title'} differs from the verified one for {mid}: "
              f"{v.get('subject')!r}. Send the verified subject, or re-run `messages.py verify` after changing it.")

    if short == "send_bulk_message":
        sig = str(ti.get("signature") or "")
        inv = str(ti.get("invitation") or "")
        parent = str(ti.get("parent_group") or "")
        recips = [str(r) for r in (ti.get("recipients") or [])]
        if "Area_Chair" not in sig or sig.startswith("~"):
            block("signature must be the paper's anonymous Area_Chair group (never a real profile or the default).")
        if f"Submission{paper}/-/Message" not in inv:
            block(f"invitation must be the paper's own message invitation (Submission{paper}/-/Message).")
        if not parent.endswith(f"Submission{paper}/Reviewers"):
            block(f"parent_group must be Submission{paper}/Reviewers.")
        allowed = {f"Submission{paper}/Reviewer_{r}" for r in v.get("to", [])}
        if not recips or any(not any(r.endswith(a) for a in allowed) for r in recips):
            block(f"recipients must be exactly the verified reviewers of paper {paper}: {sorted(v.get('to', []))}.")
    else:
        if str(ti.get("submission_id_or_number", "")) not in (paper,) and paper not in str(ti.get("submission_id_or_number", "")):
            block(f"post_forum_message must target paper {paper}.")
        readers = ti.get("readers")
        if readers and "author" in json.dumps(readers).lower():
            block("forum messages in this phase must not include Authors as readers.")
        sig = str(ti.get("signature") or "")
        if sig and ("Area_Chair" not in sig or sig.startswith("~")):
            block("signature must be the paper's anonymous Area_Chair group.")
    sys.exit(0)


if __name__ == "__main__":
    main()
