#!/usr/bin/env python3
"""Local review server for the AC dashboard: the same page as html_dashboard.py, plus editable
action items. On each draft the user can mark Satisfied / Needs changes, leave a comment, or edit
the text. The server writes nothing to OpenReview and has no OpenReview access.

What it writes (and nothing else):
  <ac_dir>/messages/feedback.jsonl                append-only: {"ts", "id", "status", "comment", "edited"}
  <ac_dir>/messages/<wave dir>/<id>.edited.md     the user's edited text (the verified <id>.md is never touched)

The assistant reads them with `messages.py feedback`, applies edits through the normal
verify + fact-check pipeline, and marks items "addressed". Sending still needs the user's
"send <ids>" in the chat; a click here can never authorize a send.

Security: binds to 127.0.0.1 only; every request needs the random token printed at start-up
(in the URL for the page, in an X-Token header for the API); the Host header must be local.

Usage (from the workspace):
  python3 -I review_server.py --ac-dir ac_discussion --dump-dir forum_dump_<date> \
      [--boundary 2.5] [--title "ICLR 2027 AC batch"] [--port 8765]
then open the printed http://127.0.0.1:<port>/?t=<token>
"""

import argparse
import datetime as _dt
import glob
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import html_dashboard as hd  # noqa: E402

STATUSES = ("satisfied", "needs-changes", "comment")
MAX_BODY = 200_000


def wave_messages(ac_dir):
    """{id: (wave dir, verified body)} for every draft in every wave."""
    out = {}
    for w in sorted(glob.glob(os.path.join(ac_dir, "messages", "wave*.json"))):
        if w.endswith(".send_plan.json"):
            continue
        with open(w, encoding="utf-8") as f:
            wave = json.load(f)
        for m in wave.get("messages", []):
            out[m["id"]] = (os.path.splitext(w)[0], m.get("body", ""))
    return out


def make_handler(args, token):
    host_ok = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}

    class H(BaseHTTPRequestHandler):
        def _deny(self, code, msg):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())

        def _local(self):
            return self.headers.get("Host", "") in host_ok

        def log_message(self, fmt, *a):  # quiet: no request bodies in logs
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def do_GET(self):
            u = urlparse(self.path)
            if not self._local():
                return self._deny(403, "forbidden host")
            if u.path == "/":
                if not hmac.compare_digest(parse_qs(u.query).get("t", [""])[0], token):
                    return self._deny(403, "missing or wrong token: open the URL printed at start-up")
                ns = hd.parser().parse_args([args.dump_dir, "--out", os.path.join(args.ac_dir, "dashboard.html"),
                                             "--workspace-dir", args.ac_dir, "--title", args.title,
                                             *(["--boundary", str(args.boundary)] if args.boundary is not None else []),
                                             "--review-suffix", args.review_suffix, "--rating-field", args.rating_field])
                page = hd.build_page(ns, token=token).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(page)
                return
            return self._deny(404, "not found")

        def do_POST(self):
            if not self._local() or not hmac.compare_digest(self.headers.get("X-Token", ""), token):
                return self._deny(403, "forbidden")
            if urlparse(self.path).path != "/api/feedback":
                return self._deny(404, "not found")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._deny(413, "body too large or empty")
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._deny(400, "bad json")
            msgs = wave_messages(args.ac_dir)
            mid, status = req.get("id"), req.get("status")
            if mid not in msgs:
                return self._deny(400, "unknown message id")
            if status not in STATUSES:
                return self._deny(400, "status must be satisfied, needs-changes or comment")
            comment = req.get("comment")
            text = req.get("text")
            if comment is not None and (not isinstance(comment, str) or len(comment) > 20_000):
                return self._deny(400, "bad comment")
            if text is not None and (not isinstance(text, str) or len(text) > 50_000):
                return self._deny(400, "bad text")
            wdir, body = msgs[mid]
            edited_path = os.path.join(wdir, f"{mid}.edited.md")
            edited = bool(text) and text.strip() != body.strip()
            if edited:
                with open(edited_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text.rstrip() + "\n")
            elif os.path.exists(edited_path) and text is None:
                os.remove(edited_path)  # back to the verified text
            entry = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "id": mid, "status": status,
                     "comment": comment or None, "edited": edited, "by": "user"}
            with open(os.path.join(args.ac_dir, "messages", "feedback.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out = dict(entry, text=text if edited else None)
            data = json.dumps(out).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ac-dir", required=True, help="the workspace's ac_discussion folder")
    ap.add_argument("--dump-dir", required=True, help="the forum dump to show")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--boundary", type=float, default=None)
    ap.add_argument("--title", default="AC discussion dashboard")
    ap.add_argument("--review-suffix", default="Official_Review")
    ap.add_argument("--rating-field", default="rating")
    args = ap.parse_args()
    token = secrets.token_urlsafe(18)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, token))
    print(f"Review server for {os.path.abspath(args.ac_dir)}")
    print(f"Open: http://127.0.0.1:{args.port}/?t={token}#actions")
    print("Stop with Ctrl-C. Nothing here sends anything to OpenReview.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
