# GymHappy MCP Installer

One-command installer for the GymHappy MCP server in Claude Desktop.

## Usage

```bash
curl -s https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install-gymhappy-mcp.py | python3
```

## What it does

1. Prompts you for your GymHappy token (get it at https://app.gymhappy.co/super/mcp-token)
2. Backs up your existing `claude_desktop_config.json`
3. Adds the `gymhappy-support` MCP server entry
4. Saves the updated config

No secrets are embedded in this script — your token is entered at runtime and written only to your local config file.

## Requirements

- Python 3
- Claude Desktop installed
