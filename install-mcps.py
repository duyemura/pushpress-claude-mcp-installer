#!/usr/bin/env python3
"""
PushPress MCP Installer for Claude Desktop
Run from Terminal to add PushPress tools to Claude Desktop.

Usage:
    curl -s https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install-mcps.py \
         -o /tmp/install-mcps.py && python3 /tmp/install-mcps.py
"""

import json, os, shutil, sys, subprocess, glob

# ── MCP registry ──────────────────────────────────────────────────────────────

MCPS = {
    "1": {
        "name": "GymHappy Support",
        "description": "Look up gyms, members, reviews, and diagnose issues",
    },
    "2": {
        "name": "Metabase",
        "description": "Query PushPress data and pull metrics directly from Metabase",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def prompt(msg):
    try:
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

def get_config_path():
    return os.path.expanduser(
        "~/Library/Application Support/Claude/claude_desktop_config.json"
    )

def load_config(path):
    if not os.path.exists(path):
        print(f"\n❌  Claude Desktop config not found at:\n    {path}")
        print("    Make sure Claude Desktop is installed and has been opened at least once.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)

def save_config(path, config):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)

def backup_config(path):
    backup = path + ".backup"
    shutil.copy2(path, backup)
    print(f"✅  Backed up config → {backup}")

# ── Node v20+ detection ───────────────────────────────────────────────────────

def find_node_v20():
    """
    Returns (npx_path, bin_dir) for a node v20+ install, or (None, None) if not found.
    Checks system node first, then nvm.
    """
    # Try system node
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        )
        version_str = result.stdout.strip().lstrip("v")
        major = int(version_str.split(".")[0])
        if major >= 20:
            # System node is fine — no need for explicit path
            return "npx", None
    except Exception:
        pass

    # Try nvm versions
    nvm_dir = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm_dir):
        candidates = []
        for entry in os.listdir(nvm_dir):
            try:
                major = int(entry.lstrip("v").split(".")[0])
                if major >= 20:
                    candidates.append((major, entry))
            except ValueError:
                pass
        if candidates:
            candidates.sort(reverse=True)
            best = candidates[0][1]
            bin_dir = os.path.join(nvm_dir, best, "bin")
            npx_path = os.path.join(bin_dir, "npx")
            if os.path.exists(npx_path):
                return npx_path, bin_dir

    return None, None

# ── Individual MCP installers ─────────────────────────────────────────────────

def install_gymhappy(config):
    print("\n── GymHappy Support ──────────────────")
    print("\nGet your token at: https://app.gymhappy.co/super/mcp-token")
    print("(Log in to GymHappy first if prompted)\n")

    token = prompt("Paste your GymHappy token (or Enter to skip): ")
    if not token:
        print("⚠️  Skipping GymHappy — no token provided.")
        return False

    # Encode | as %7C — required by CloudFront (strips Authorization headers,
    # so token goes in query param instead)
    token = token.replace("|", "%7C")

    config.setdefault("mcpServers", {})["gymhappy-support"] = {
        "command": "npx",
        "args": [
            "-y", "mcp-remote",
            f"https://app.gymhappy.co/mcp/support?mcp_token={token}"
        ]
    }
    print("✅  GymHappy added.")
    return True

def install_metabase(config):
    print("\n── Metabase ──────────────────────────")
    print("\nTo get a Metabase API key:\n")
    print("  1. Open Slack → #support-data")
    print("  2. Send this message:\n")
    print('       "Hi @data I need a metabase API key for Claude Cowork.')
    print('        Can you send me one?"\n')
    print("     💬 https://pushpress.slack.com/channels/support-data\n")
    print("  3. The data team will create a key and send it to you via 1Password.\n")

    api_key = prompt("Paste your Metabase API key (or Enter to skip): ")
    if not api_key:
        print("⚠️  Skipping Metabase — no API key provided.")
        print("    Run this installer again once you have your key.")
        return False

    # Metabase MCP requires Node v20+
    npx_path, bin_dir = find_node_v20()
    if npx_path is None:
        print("\n❌  Metabase MCP requires Node.js v20 or higher, but none was found.")
        print("    Install Node v20+ via https://nodejs.org or nvm, then re-run.")
        return False

    entry = {
        "command": npx_path,
        "args": ["@cognitionai/metabase-mcp-server"],
        "env": {
            "METABASE_URL": "https://pushpress.metabaseapp.com/",
            "METABASE_API_KEY": api_key,
        }
    }

    # If using nvm, inject PATH so Claude Desktop can find node modules
    if bin_dir:
        system_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        entry["env"]["PATH"] = f"{bin_dir}:{system_path}"
        print(f"ℹ️   Using Node from nvm: {npx_path}")

    config.setdefault("mcpServers", {})["metabase"] = entry
    print("✅  Metabase added.")
    return True

INSTALLERS = {
    "1": install_gymhappy,
    "2": install_metabase,
}

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nPushPress MCP Installer")
    print("=" * 40)
    print("Adds PushPress tools to Claude Desktop.\n")

    print("Which MCPs would you like to install?\n")
    for key, mcp in MCPS.items():
        print(f"  [{key}] {mcp['name']}")
        print(f"       {mcp['description']}\n")
    print("  [A] All of the above")
    print("  [Q] Quit\n")

    choice = prompt("Your choice: ").upper()
    if not choice or choice == "Q":
        print("Bye!")
        sys.exit(0)

    if choice == "A":
        selected = list(MCPS.keys())
    else:
        selected = [c.strip() for c in choice.split(",") if c.strip() in MCPS]
        if not selected:
            print("❌  Invalid choice. Run the installer again and pick from the menu.")
            sys.exit(1)

    config_path = get_config_path()
    config = load_config(config_path)
    backup_config(config_path)

    installed = []
    for key in selected:
        if INSTALLERS[key](config):
            installed.append(MCPS[key]["name"])

    if installed:
        save_config(config_path, config)
        print(f"\n{'─' * 40}")
        print(f"✅  Installed: {', '.join(installed)}")
        print("\n👉  Restart Claude Desktop for changes to take effect.")
    else:
        print("\nNothing was installed.")

if __name__ == "__main__":
    main()
