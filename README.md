# OpenReview MCP Server 🚀

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-orange.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A powerful [Model Context Protocol](https://modelcontextprotocol.io/) server for **OpenReview**, tailored for Area Chairs (ACs), Reviewers, and Authors. This server allows LLMs to interact with OpenReview venues, manage submissions, track review progress, and communicate with participants.

## ✨ Features

### 🏛️ Area Chair Tools
- **Bidding**: View papers and submit interest before assignments are made.
- **List Assigned Submissions**: Get details of all papers you are chairing.
- **Review Status Reports**: Real-time tracking of review progress.
- **Initial Checks**: Inspect status, form schemas, and fill/submit desk-rejection initial checks for assigned papers individually or in batch (`get_initial_check_status`, `submit_initial_check`, `batch_submit_initial_checks`).
- **Role-Specific Updates**: Tools for ACs, Reviewers, and Authors to fetch recent forum activity (`get_ac_updates`, etc.).
- **Inbox Summary**: A high-density, token-efficient summary of all recent activity across assigned papers (`get_inbox_summary`).
- **Time-Based Filtering**: Use `since='1d'` or `since='2h'` to fetch only the latest updates for automated monitoring.
- **Targeted Reminders**: Send emails to reviewers with missing assignments.
- **Get Reviewer Emails**: Retrieve preferred emails (unmasked via message logs when possible).
- **Safety First**: Built-in `dry_run` and preview modes for all messaging tools.

### 📝 Reviewer Tools
- **Bidding**: Express interest in papers you'd like to review.
- **Assignment Tracking**: List all papers assigned to you for review.
- **Discussion Updates**: Stay on top of the latest comments and rebuttals in your forums.
- **Deadline Monitoring**: Never miss a venue milestone.

### ✍️ Author Tools
- **Submission Management**: List and track your own papers.
- **Feedback Retrieval**: Access visible reviews and public comments.

### 🔍 General Utilities
- **Venue Discovery**: Filter active venues or search your entire conference history.
- **Submission Search**: Keyword search across titles and abstracts.
- **Server Time Check**: Synchronize local clocks and automated agents with OpenReview server time.

### 📥 Paper & Attachment Downloads
- **Download Submission Attachments**: Download author rebuttals, supplementary materials, or any attached files for a single paper via note ID or forum URL (`download_submission_attachments`).
- **Batch Download Attachments**: Download attachments (e.g., all author rebuttals) across all assigned papers for Reviewers or ACs (`download_batch_attachments`).
- **Batch Download PDFs**: Download full paper PDFs for assigned papers (`download_batch_pdfs`).

## 🧭 Area Chair Workflow Skills (Claude Code)

`skills/` holds conference-agnostic Agent Skills for the discussion and decision phases. `.claude-plugin/plugin.json` packages them as the `openreview` plugin.

| skill | use it to |
|---|---|
| `openreview-ac-venue-setup` | ask for the current conference's AC, reviewer and author guidelines, and record the timeline, rating scale, forms and messaging rules in `venue_profile.md` |
| `openreview-ac-sync` | re-dump all forums, diff against the last dump, extract revised PDFs (with a page-limit check), and regenerate the batch dashboard: markdown `00_dashboard.md` plus a self-contained per-paper `dashboard.html` (concerns grouped by validity, importance, status or reviewer, with verified resolutions and who shares each one; then disagreements, where each reviewer stands, and the verbatim thread) |
| `openreview-ac-dossier` | build a per-paper dossier: a verbatim-quote concern ledger with severity, rebuttal mapping, reviewer follow-ups, PDF fact-checks and decision cruxes |
| `openreview-ac-messages` | plan, draft (subagents), verify and fact-check forum threads that move reviewers to agreement, short emails that bring silent reviewers in, final-recommendation waves and chairs-only notes; send them only after explicit approval, through a send gate |
| `openreview-ac-metareview` | produce a verbatim TL;DR, then audit the AC's own meta-review draft (supported, unsupported or contradicted), with minimal edits |

Design rules:
- **Verbatim first.** All reviewer, author and PDF text is stored as quote objects. `verify_quotes.py` checks that each one exists in a note signed by the claimed person, or on the stated PDF page.
- **The skills never send anything** without a dry run and the user's explicit approval.
- **Messages are drafted by subagents and fact-checked.** Each draft has a claims file that maps every factual sentence to its evidence. `messages.py verify` checks the claims mechanically:
  - quotes are verbatim, correctly attributed, and from notes the recipients can read;
  - "latest comment" quotes really are the reviewer's latest;
  - ratings and dossier verdicts are current;
  - author-reply pointers are correct.

  It also requires an independent fact-checker's pass for the exact text, and then records "verified" in `ac_discussion/messages/ledger.jsonl`.
- **Fair to the paper.** Only verified concerns are presented, partly true ones get pushback with PDF evidence, and reviewers are pointed to author replies they have not answered.
- **The send gate (`hooks/send_gate.py`, registered in `hooks/hooks.json`).** A PreToolUse hook on the OpenReview send tools. It blocks a real send unless the exact text and subject are verified and approved in the ledger, the user's own message names the id with "send", and the target is right (the paper's invitation or forum, the anonymous Area_Chair signature, the verified recipients, no Authors readers). Bulk send tools are always blocked. Without the plugin installed, run it by hand before a send: `python3 -I hooks/send_gate.py <<< '<tool-call JSON>'` (exit 0 = allowed).
- **Scripts are stdlib-only Python**, run with `python3 -I`. They need poppler (`pdftotext`, `pdfinfo`) for the PDF steps.

### Discussion-phase playbook (one pass, any OpenReview venue)

1. **`openreview-ac-venue-setup`:** paste or link the venue's AC, reviewer and author guidelines to get `venue_profile.md`.
2. **`openreview-ac-sync`:** dump all forums, download and extract the revised PDFs, then build `00_dashboard.md` and `dashboard.html`.
3. **`openreview-ac-dossier`:** one dossier per paper, built by parallel subagents. The quotes are verified, and each concern's validity and resolution are checked against the paper.
4. **`openreview-ac-messages`, wave 1 (discussion):**
   1. red-flag scan;
   2. `plan_wave.py`;
   3. drafting subagents;
   4. mechanical verify;
   5. fact-check subagents (Haiku is fine);
   6. review with the user (escaped review file and dashboard);
   7. send the threads, then the emails, with `send-plan`, `print-args`, `gate` and `log-sent`.
5. **Follow-up:** sync every 2 or 3 days; when the deadline nears, run a `--wave-goal final` wave asking for explicit accept or reject.
6. **`openreview-ac-metareview`:** a verbatim TL;DR, then the AC writes the meta-review and the skill audits it.

To use the skills without installing the plugin, symlink them into Claude Code:
```bash
for s in skills/openreview-ac-*; do ln -s "$PWD/$s" ~/.claude/skills/; done
```

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/openreview-mcp.git
cd openreview-mcp

# Install in editable mode
pip install -e .
```

## ⚙️ Configuration

Set the following environment variables in your MCP client (e.g., Claude Desktop):

- `OPENREVIEW_USERNAME`: Your OpenReview email address.
- `OPENREVIEW_PASSWORD`: Your OpenReview password.
- `OPENREVIEW_BASEURL`: (Optional) Defaults to `https://api2.openreview.net`.

### Claude Desktop Configuration

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openreview": {
      "command": "python3",
      "args": ["-m", "openreview_mcp.main"],
      "env": {
        "OPENREVIEW_USERNAME": "your_email@example.com",
        "OPENREVIEW_PASSWORD": "your_secure_password"
      }
    }
  }
}
```

### Running as SSE Server (Network Access)

If you want to run the server over the network (e.g., for remote access or debugging), use the `sse` transport:

```bash
# Start on default port 8000
python3 -m openreview_mcp.main sse

# Start on a specific port
python3 -m openreview_mcp.main sse 8080
```

The server will be available at `http://127.0.0.1:8080/sse`.

## 📧 Messaging & Reminders

Communication in OpenReview (especially v2) can be complex due to per-submission invitations and anonymized signatures. This server simplifies this:

- **Automatic Reminders**: Use `send_reminders(venue_id="...")`. It will:
  1. Identify reviewers with missing reviews.
  2. Auto-discover the correct per-submission invitation (e.g., `Submission5/-/Message`).
  3. Auto-discover your paper-specific anonymized signature (e.g., `Area_Chair_xxxx`).
  4. Auto-discover the required `parentGroup` for that submission.
- **Bulk Messaging**: Use `send_bulk_message` for more general communication. It fallbacks to the venue's meta-invitation if no specific one is provided.
- **Dry Runs**: Both tools default to `dry_run=True`. Always check the preview output to verify recipients and signatures before sending live.

## 📖 Best Practices

- **Security**: Always use `dry_run=True` (default) when using `send_bulk_message`.
- **Anonymity**: The server automatically discovers your anonymized signature in `send_reminders`. If calling `send_bulk_message` manually for a specific paper, ensure you use your paper-specific signature group.
- **Unmasking**: Use `get_reviewer_emails` with a `venue_id` to attempt to unmask emails using historical message logs if the profile is restricted.
- **Automation**: Use `get_server_time` to calibrate timestamps when running automated monitoring (e.g., cron jobs) to avoid missing updates due to clock drift.

## 🧪 Quality Assurance

We use `pytest` for testing and `ruff` for linting and formatting.

```bash
# Run tests (requires credentials)
pytest -s tests/test_server.py

# Lint and format
ruff check .
ruff format .
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
*Built with ❤️ for the research community.*

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/paul.crafts)

### Venue notes
Venue-specific quirks (stages, edge names, which tools to use or avoid, rate-limit incidents) live in one skill per venue, for example `skills/openreview-venue-tmlr/`. When you learn something non-obvious about a venue, add it to that venue's skill; for a new venue, copy the structure of the TMLR one and register it in `.claude-plugin/plugin.json`.
