# PushPress MCP Installer

One-command installer for PushPress MCP servers in Claude Desktop.

## Usage

```bash
curl -s https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install-mcps.py -o /tmp/install-mcps.py && python3 /tmp/install-mcps.py
```

You'll see a menu to choose which MCPs to install:

```
[1] GymHappy Support
[2] Metabase
[A] All of the above
```

## Supported MCPs

| MCP | What it does | Requires |
|-----|-------------|---------|
| GymHappy Support | Look up gyms, members, reviews, diagnose issues | GymHappy token |
| Metabase | Query PushPress data, pull metrics | Metabase API key + Node v20+ |

## Getting credentials

**GymHappy:** https://app.gymhappy.co/super/mcp-token

**Metabase:** Log into Metabase → click your avatar → Account settings → API Keys → Create API key. Don't have access? Message #support-data in Slack.

## Notes

- No secrets are embedded in this script — credentials are entered at runtime
- Backs up your `claude_desktop_config.json` before writing
- Safe to re-run (e.g. to add more MCPs later)
- Metabase requires Node v20+; the installer will auto-detect nvm if needed

## Requirements

- Python 3
- Claude Desktop installed and opened at least once

---

## For contributors

`mcps.json` is the machine-readable registry of all supported MCPs. It is read by the [pushpress-team Cowork plugin](https://github.com/duyemura/pushpress-claude-plugin) to check credential status and surface setup instructions. Keep it in sync with `install-mcps.py` — both must agree on supported MCPs and required credentials.

Before committing: verify every MCP in `install-mcps.py` has a matching entry in `mcps.json`, and update this README if supported MCPs change.
