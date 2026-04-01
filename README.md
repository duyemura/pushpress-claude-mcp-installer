# PushPress MCP Installer

One-command installer for PushPress MCP servers in Claude Desktop.

## Usage

```bash
curl -fsSL https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install.sh | bash
```

That's it. The script handles everything:
- Installs Node.js v22 if you don't have it (downloads the official macOS installer)
- Walks you through choosing which MCPs to set up
- Verifies your credentials before saving
- Backs up your Claude Desktop config before making changes

No Python, Homebrew, or Xcode needed.

## Supported MCPs

| MCP | What it does | Requires |
|-----|-------------|---------|
| GymHappy Support | Look up gyms, members, reviews, diagnose issues | GymHappy token |
| Metabase | Query PushPress data, pull metrics | Metabase API key |
| GitHub (PushPress Code) | Search and read source code (read-only) | GitHub Fine-Grained PAT |

## Getting credentials

**GymHappy:** https://app.gymhappy.co/super/mcp-token (log in first if prompted)

**Metabase:** Message #support-data in Slack — the data team will send you a key via 1Password.

**GitHub:** Create a [Fine-Grained Personal Access Token](https://github.com/settings/personal-access-tokens/new) scoped to the `pushpress` org with read-only permissions (Contents, Metadata, Pull requests). The installer walks you through it step by step.

## Notes

- No secrets are embedded in this script — credentials are entered at runtime
- Backs up your `claude_desktop_config.json` before writing
- Safe to re-run (e.g. to add more MCPs or update credentials)
- Node.js v20+ is auto-detected from system PATH, nvm, or fnm

## Requirements

- macOS
- Claude Desktop installed and opened at least once

## Legacy

The previous Python-based installer (`install-mcps.py`) is still available but deprecated. Use `install.sh` instead.

---

## For contributors

`mcps.json` is the machine-readable registry of all supported MCPs. It is read by the [pushpress-team Cowork plugin](https://github.com/duyemura/pushpress-claude-plugin) to check credential status and surface setup instructions. Keep it in sync with `install.sh` — both must agree on supported MCPs and required credentials.

### Version history

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-03-31 | Rewrote installer as bash script — no Python/Homebrew/Xcode deps, auto-installs Node.js |
| 1.1.0 | 2026-03-02 | Catalog-driven architecture, mcps.json registry |
| 1.0.0 | 2026-02-28 | Initial Python installer with GymHappy + Metabase |
