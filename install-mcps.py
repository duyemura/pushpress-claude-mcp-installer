#!/usr/bin/env python3
"""
PushPress MCP Installer for Claude Desktop
==========================================
Adds PushPress-managed MCP servers to Claude Desktop's config file so that
Claude can call internal tools (GymHappy, Metabase, etc.) directly.

STANDARD USAGE (for team members):
    curl -s https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install-mcps.py \
         -o /tmp/install-mcps.py && python3 /tmp/install-mcps.py

PREVIEW / ADMIN USAGE (for testing before publishing changes):
    python3 install-mcps.py --preview
        Dry-run mode. Walks through the full installer flow, shows exactly what
        JSON would be written to the config, then asks before applying anything.

    python3 install-mcps.py --config /tmp/test-config.json
        Sandbox mode. Reads/writes a throwaway config file instead of the real
        Claude Desktop config. Safe to run repeatedly without touching your setup.
        If the file doesn't exist yet, it is created with an empty mcpServers dict.

    python3 install-mcps.py --preview --config /tmp/test-config.json
        Both flags together: sandbox file + dry-run prompt. Safest for testing
        new MCP entries or installer flow changes end-to-end.

SECURITY NOTES:
    - --preview grants no server-side privileges. It only affects local file I/O.
    - --config accepts any writable path. Do not point it at sensitive files.
    - Neither flag changes what gets installed; they only control whether/where
      the config is written.
    - These flags are intentionally undocumented in the menu so team members
      don't accidentally use them. They appear only in --help and this docstring.
"""

import argparse
import copy
import json
import os
import shutil
import sys
import subprocess


# ── MCP registry ──────────────────────────────────────────────────────────────
# Add new MCPs here. Each entry needs:
#   name        — display name shown in the menu
#   description — one-line description shown under the name
#   config_key  — the key used in claude_desktop_config.json's "mcpServers" dict
#                 (must match exactly what the MCP server expects)

MCPS = {
    "1": {
        "name": "GymHappy Support",
        "description": "Look up gyms, members, reviews, and diagnose issues",
        "config_key": "gymhappy-support",
    },
    "2": {
        "name": "Metabase",
        "description": "Query PushPress data and pull metrics directly from Metabase",
        "config_key": "metabase",
    },
}


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="PushPress MCP Installer for Claude Desktop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Dry-run mode: collect all inputs, show the full config diff, "
            "then ask before writing anything to disk."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Use a custom config file path instead of the default Claude Desktop "
            "config. If the file doesn't exist it will be created. Useful for "
            "testing in an isolated sandbox without touching your real setup."
        ),
    )
    return parser.parse_args()


# ── Config file helpers ───────────────────────────────────────────────────────
#
# ABOUT THE CONFIG FILE
# ─────────────────────
# Claude Desktop stores all MCP server definitions in a single JSON file:
#
#   macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
#   Windows: %APPDATA%\Claude\claude_desktop_config.json  (not supported here)
#
# The relevant section looks like:
#
#   {
#     "mcpServers": {
#       "some-mcp-name": {
#         "command": "npx",
#         "args": ["-y", "mcp-remote", "https://..."],
#         "env": { "KEY": "value" }   // optional
#       },
#       ...
#     }
#   }
#
# Each top-level key under "mcpServers" is the MCP's identifier. Claude Desktop
# reads this file on startup; changes require a restart to take effect.
#
# WHAT THIS SCRIPT DOES TO THE CONFIG
# ─────────────────────────────────────
# 1. Reads the existing file (preserving ALL existing entries).
# 2. Merges in the new MCP entry/entries under "mcpServers".
#    If a key already exists, it is overwritten (useful for updating tokens).
# 3. Writes the merged result back atomically (write to .tmp, then os.replace).
# 4. Leaves a .backup copy of the original alongside the config file.
#
# The script never removes existing mcpServers entries. It only adds/updates.

DEFAULT_CONFIG_PATH = os.path.expanduser(
    "~/Library/Application Support/Claude/claude_desktop_config.json"
)


def get_config_path(custom_path=None):
    """
    Return the config file path to use.

    If --config PATH was passed, use that (creating the file with an empty
    skeleton if it doesn't exist yet). Otherwise use the default Claude Desktop
    path and fail loudly if it doesn't exist — Claude Desktop must be installed.
    """
    if custom_path:
        if not os.path.exists(custom_path):
            # Create a minimal valid config so the rest of the script works
            # without needing Claude Desktop to be present.
            skeleton = {"mcpServers": {}}
            os.makedirs(os.path.dirname(os.path.abspath(custom_path)), exist_ok=True)
            with open(custom_path, "w") as f:
                json.dump(skeleton, f, indent=2)
                f.write("\n")
            print(f"ℹ️   Created new sandbox config at: {custom_path}")
        return custom_path

    # Default path — Claude Desktop must have been opened at least once
    if not os.path.exists(DEFAULT_CONFIG_PATH):
        print(f"\n❌  Claude Desktop config not found at:\n    {DEFAULT_CONFIG_PATH}")
        print("    Make sure Claude Desktop is installed and has been opened at least once.")
        sys.exit(1)
    return DEFAULT_CONFIG_PATH


def load_config(path):
    """
    Read and parse the config JSON.

    Returns a dict. If the file is empty or malformed, prints a helpful error
    and exits rather than silently corrupting the config on write.
    """
    with open(path) as f:
        raw = f.read().strip()

    if not raw:
        # Empty file — treat as blank config rather than crashing
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n❌  Could not parse config file: {path}")
        print(f"    JSON error: {e}")
        print("    Fix the JSON manually, then re-run the installer.")
        sys.exit(1)


def backup_config(path):
    """
    Copy the current config to <path>.backup before making any changes.

    This is a last-resort recovery option. It's overwritten on each run, so it
    only reflects the state immediately before the most recent install.
    """
    backup = path + ".backup"
    shutil.copy2(path, backup)
    print(f"✅  Backed up config → {backup}")


def save_config(path, config):
    """
    Write the updated config atomically.

    Uses a write-to-tmp + os.replace pattern so that if the process is
    interrupted mid-write, the original file remains intact.

    The JSON is formatted with 2-space indentation to stay consistent with
    what Claude Desktop itself writes.
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")          # trailing newline — good practice for text files
    os.replace(tmp, path)     # atomic on POSIX; replaces original only on success


def get_installed_keys(config):
    """Return the set of mcpServers keys already present in the config."""
    return set(config.get("mcpServers", {}).keys())


def diff_mcp_servers(before, after):
    """
    Return a human-readable summary of what changed in mcpServers.

    Used in --preview mode to show the operator exactly what will be written
    before they confirm. Returns a list of strings (one per changed/added key).
    """
    before_servers = before.get("mcpServers", {})
    after_servers = after.get("mcpServers", {})
    lines = []
    for key, value in after_servers.items():
        if key not in before_servers:
            lines.append(f"  + ADD    \"{key}\"")
        elif before_servers[key] != value:
            lines.append(f"  ~ UPDATE \"{key}\"")
        # else: unchanged — don't mention it
    return lines


# ── Node v20+ detection ───────────────────────────────────────────────────────
#
# The Metabase MCP server (@cognitionai/metabase-mcp-server) is an npm package
# that requires Node.js v20+. On many macOS machines, the system node is older
# (or absent entirely), but nvm-managed versions may be available.
#
# Claude Desktop launches MCP processes with a minimal PATH that often doesn't
# include ~/.nvm/... paths, so we need to use the full absolute path to npx
# when a system node isn't available.

def find_node_v20():
    """
    Locate a Node.js v20+ installation.

    Returns:
        (npx_path, bin_dir) where:
          - npx_path is "npx" (for system node) or an absolute path (for nvm)
          - bin_dir is None (system node) or the full path to the node bin/
            directory (nvm) — used to inject PATH into the MCP env block

    Returns (None, None) if no v20+ node is found.
    """
    # 1. Try system node first (simplest case)
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        )
        version_str = result.stdout.strip().lstrip("v")
        major = int(version_str.split(".")[0])
        if major >= 20:
            return "npx", None
    except Exception:
        pass

    # 2. Fall back to nvm — scan for the highest available v20+ version
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
            candidates.sort(reverse=True)  # highest major version first
            best = candidates[0][1]
            bin_dir = os.path.join(nvm_dir, best, "bin")
            npx_path = os.path.join(bin_dir, "npx")
            if os.path.exists(npx_path):
                return npx_path, bin_dir

    return None, None


# ── Helpers ───────────────────────────────────────────────────────────────────

def prompt(msg):
    """
    Read a line from the user, stripping whitespace. Exits cleanly on Ctrl-C.

    When the script is piped (e.g. ``curl ... | python3``), stdin carries the
    script source — not the keyboard. Calling ``input()`` would get EOF
    immediately and the script would exit before the user can type anything.

    We work around this by opening ``/dev/tty`` directly when stdin is not a
    real terminal. ``/dev/tty`` always refers to the controlling terminal of
    the current process, regardless of how stdin is wired up, so the prompt
    will wait for keyboard input even when the script arrives via a pipe.
    """
    try:
        if not sys.stdin.isatty():
            # stdin is a pipe (e.g. curl ... | python3) — read directly from
            # the terminal so the user can still type their answer.
            with open("/dev/tty") as tty:
                sys.stdout.write(msg)
                sys.stdout.flush()
                line = tty.readline()
                if not line:          # EOF on /dev/tty (no controlling terminal)
                    print()
                    sys.exit(0)
                return line.strip()
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


# ── Individual MCP installers ─────────────────────────────────────────────────

def install_gymhappy(config):
    """
    Add or update the GymHappy Support MCP entry in config["mcpServers"].

    Config entry written:
        "gymhappy-support": {
            "command": "npx",
            "args": ["-y", "mcp-remote", "https://app.gymhappy.co/mcp/support?mcp_token=TOKEN"]
        }

    Token encoding note:
        GymHappy tokens are Laravel Sanctum tokens in the format "{id}|{secret}".
        The pipe character (|) must be percent-encoded as %7C because CloudFront
        strips Authorization headers, so the token is passed as a query parameter
        instead — and bare pipes in query strings cause parsing issues on some
        reverse proxies.
    """
    print("\n── GymHappy Support ──────────────────")
    print("\nGet your token at: https://app.gymhappy.co/super/mcp-token")
    print("(Log in to GymHappy first if prompted)\n")

    token = prompt("Paste your GymHappy token (or Enter to skip): ")
    if not token:
        print("⚠️  Skipping GymHappy — no token provided.")
        return False

    # Encode | → %7C before embedding in the URL query string
    token_encoded = token.replace("|", "%7C")

    config.setdefault("mcpServers", {})["gymhappy-support"] = {
        "command": "npx",
        "args": [
            "-y",
            "mcp-remote",
            f"https://app.gymhappy.co/mcp/support?mcp_token={token_encoded}",
        ],
    }
    print("✅  GymHappy added.")
    return True


def install_metabase(config):
    """
    Add or update the Metabase MCP entry in config["mcpServers"].

    Config entry written (system node):
        "metabase": {
            "command": "npx",
            "args": ["@cognitionai/metabase-mcp-server"],
            "env": {
                "METABASE_URL": "https://pushpress.metabaseapp.com/",
                "METABASE_API_KEY": "<key>"
            }
        }

    Config entry written (nvm node — adds explicit PATH so Claude Desktop
    can resolve the npx binary and node_modules even with a minimal $PATH):
        "metabase": {
            "command": "/Users/you/.nvm/versions/node/v22.x.x/bin/npx",
            "args": ["@cognitionai/metabase-mcp-server"],
            "env": {
                "METABASE_URL": "https://pushpress.metabaseapp.com/",
                "METABASE_API_KEY": "<key>",
                "PATH": "/Users/you/.nvm/.../bin:/usr/local/bin:/usr/bin:/bin:..."
            }
        }

    METABASE_URL is hardcoded to the PushPress instance — team members should
    never need to change this.
    """
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
        },
    }

    if bin_dir:
        # Inject PATH so Claude Desktop (which launches with a stripped environment)
        # can find the nvm-managed npx and any globally-installed node_modules.
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


# ── Preview mode helpers ──────────────────────────────────────────────────────

def print_preview_banner():
    print("\n" + "━" * 50)
    print("  ⚙️   PREVIEW MODE  (--preview)")
    print("━" * 50)
    print("  No changes will be written until you confirm.")
    print("  Config file will NOT be modified until you say yes.\n")


def print_current_config(config, config_path):
    """Print the current mcpServers block so the operator can see before/after."""
    servers = config.get("mcpServers", {})
    print(f"\nCurrent config: {config_path}")
    if not servers:
        print("  mcpServers: (empty — no MCPs installed yet)")
    else:
        print(f"  mcpServers ({len(servers)} installed):")
        for key in servers:
            print(f"    • {key}")
    print()


def print_diff_and_confirm(before, after, config_path):
    """
    Show what would change, then ask the operator whether to apply.
    Returns True if they confirm, False if they decline.
    """
    diff = diff_mcp_servers(before, after)

    print("\n" + "─" * 50)
    print("  Config diff — what would be written:")
    print(f"  Target: {config_path}\n")

    if not diff:
        print("  (no changes detected)")
    else:
        for line in diff:
            print(f"  {line}")

    # Show the full proposed mcpServers block so there are no surprises
    after_servers = after.get("mcpServers", {})
    if after_servers:
        print("\n  Full mcpServers block after change:")
        print("  " + json.dumps({"mcpServers": after_servers}, indent=2).replace("\n", "\n  "))

    print("\n" + "─" * 50)
    answer = prompt("  Apply these changes? [y/N]: ")
    return answer.lower() == "y"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # ── Header ────────────────────────────────────────────────────────────────
    print("\nPushPress MCP Installer")
    print("=" * 40)
    if args.preview:
        print_preview_banner()
    else:
        print("Adds PushPress tools to Claude Desktop.\n")

    # ── Load config ───────────────────────────────────────────────────────────
    #
    # We load the config before showing the menu so we can:
    #   1. Display which MCPs are already installed next to each menu option
    #   2. Take a snapshot (config_before) for the diff in --preview mode
    #
    config_path = get_config_path(args.config)
    config = load_config(config_path)
    config_before = copy.deepcopy(config)   # snapshot for diff/preview
    installed_keys = get_installed_keys(config)

    if args.preview:
        print_current_config(config, config_path)

    # ── Menu ──────────────────────────────────────────────────────────────────
    while True:
        print("Which MCPs would you like to install?\n")
        for key, mcp in MCPS.items():
            if mcp["config_key"] in installed_keys:
                status = "✅ installed  (select to update credentials)"
            else:
                status = "⬜ not installed"
            print(f"  [{key}] {mcp['name']} — {status}")
            print(f"       {mcp['description']}\n")
        print("  [A] All PushPress MCPs")
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
                print("❌  Invalid choice. Pick from the menu above.\n")
                continue

        break

    # ── Run installers ────────────────────────────────────────────────────────
    installed_names = []
    for key in selected:
        if INSTALLERS[key](config):
            installed_names.append(MCPS[key]["name"])

    if not installed_names:
        print("\nNothing was installed.")
        sys.exit(0)

    # ── Write config ──────────────────────────────────────────────────────────
    #
    # In --preview mode: show the full diff and ask before writing.
    # In normal mode: back up and write immediately.

    if args.preview:
        confirmed = print_diff_and_confirm(config_before, config, config_path)
        if not confirmed:
            print("\n⚠️   Aborted — config was NOT changed.")
            print("    Re-run without --preview to apply directly, or run again to adjust.")
            sys.exit(0)

    backup_config(config_path)
    save_config(config_path, config)

    print(f"\n{'─' * 40}")
    print(f"✅  Installed: {', '.join(installed_names)}")
    if args.config:
        print(f"    Written to sandbox config: {config_path}")
    print("\n👉  Restart Claude Desktop for changes to take effect.")


if __name__ == "__main__":
    main()
