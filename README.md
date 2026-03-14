# Intervals MCP Server

MCP server for Intervals.icu over `stdio`.

Provides athlete, activity, wellness, event, and fitness tools.

## Setup with uv

```bash
# Install dependencies (creates pyproject.toml and uv.lock)
uv sync

# Run the server
uv run python main.py
```

## Setup with pip

Set `INTERVALS_API_KEY` and run `python main.py`.
