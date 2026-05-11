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

## 📖 Best Practices

- **Security**: Always use `dry_run=True` (default) when using `send_bulk_message`.
- **Anonymity**: Use the appropriate `signature` (e.g., `Area_Chair_xxxx`) when messaging reviewers in double-blind venues.
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
