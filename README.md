# fairsharing-mcp

An MCP (Model Context Protocol) server exposing the FAIRsharing GraphQL API as 95 tools for discovering and analyzing data standards, databases, and policies in the life sciences.

## Setup

```bash
uv sync
```

## Usage

```bash
FAIRSHARING_API_KEY=your_key uv run fairsharing-mcp
```

## Tests

```bash
python -m pytest tests/test_server.py
```
