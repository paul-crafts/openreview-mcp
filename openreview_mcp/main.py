import sys
from openreview_mcp.server import mcp


def main():
    """Main entry point for the MCP server."""
    try:
        # Check for SSE transport request
        if len(sys.argv) > 1 and sys.argv[1] == "sse":
            port = 8000
            if len(sys.argv) > 2:
                try:
                    port = int(sys.argv[2])
                except ValueError:
                    print(
                        f"Invalid port: {sys.argv[2]}. Using default 8000.",
                        file=sys.stderr,
                    )

            import uvicorn

            print(
                f"Starting OpenReview MCP server on port {port} using SSE...",
                file=sys.stderr,
            )
            uvicorn.run(mcp.sse_app, host="127.0.0.1", port=port)
        else:
            # Default to stdio
            mcp.run(transport="stdio")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
