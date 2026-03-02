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


# ── MCP catalog ───────────────────────────────────────────────────────────────
#
# MCPs are defined in mcps.json, loaded at startup by load_catalog().
# To add a new MCP, edit mcps.json only — no Python changes needed.
#
# The catalog is loaded from the first available source (in priority order):
#   1. Local mcps.json next to this script  (dev/clone workflow)
#   2. Remote GitHub URL                     (curl | python3 workflow)
#   3. BUNDLED_CATALOG constant below        (offline fallback)
#
# Keep BUNDLED_CATALOG in sync with mcps.json when publishing changes.

CATALOG_URL = (
    "https://raw.githubusercontent.com/"
    "duyemura/pushpress-claude-mcp-installer/main/mcps.json"
)

BUNDLED_CATALOG = [
    {
        "name": "GymHappy Support",
        "description": "Look up gyms, members, reviews, and diagnose issues",
        "config_key": "gymhappy-support",
        "install": {
            "strategy": "mcp_remote",
            "instructions": [
                "Get your token at: https://app.gymhappy.co/super/mcp-token",
                "(Log in to GymHappy first if prompted)",
            ],
            "credential_prompt": "Paste your GymHappy token (or Enter to skip): ",
            "url_template": "https://app.gymhappy.co/mcp/support?mcp_token={token}",
            "url_encode_pipe": True,
        },
        "verify": {
            "strategy": "url_token",
            "success_codes": ["200", "201", "400", "404", "405", "406"],
        },
    },
    {
        "name": "Metabase",
        "description": "Query PushPress data and pull live metrics",
        "config_key": "metabase",
        "install": {
            "strategy": "npx_env",
            "package": "@cognitionai/metabase-mcp-server",
            "requires_node_v20": True,
            "instructions": [
                "To get a Metabase API key:\n",
                "  1. Open Slack \u2192 #support-data",
                "  2. Send this message:\n",
                '       "Hi @data I need a metabase API key for Claude Cowork.',
                '        Can you send me one?"\n',
                "     \U0001f4ac https://pushpress.slack.com/channels/support-data\n",
                "  3. The data team will create a key and send it to you via 1Password.\n",
            ],
            "env": [
                {
                    "var": "METABASE_URL",
                    "prompt_user": False,
                    "default": "https://pushpress.metabaseapp.com/",
                },
                {
                    "var": "METABASE_API_KEY",
                    "prompt_user": True,
                    "credential_prompt": "Paste your Metabase API key (or Enter to skip): ",
                },
            ],
        },
        "verify": {
            "strategy": "env_api_key",
            "base_url_var": "METABASE_URL",
            "api_key_var": "METABASE_API_KEY",
            "test_path": "/api/user/current",
            "auth_header": "x-api-key",
        },
    },
]


def load_catalog():
    """
    Load the MCP catalog from the first available source.

    Priority:
        1. Local mcps.json next to this script (dev/clone workflow).
           Only possible when the script is run as a file, not piped via stdin.
        2. Remote catalog at CATALOG_URL fetched via curl.
        3. BUNDLED_CATALOG constant (always works, even offline).

    Returns a list of MCP definition dicts.
    """
    # 1. Local file — __file__ is not defined when piped, so catch NameError
    try:
        local_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "mcps.json"
        )
        if os.path.exists(local_path):
            with open(local_path) as f:
                return json.load(f)
    except NameError:
        pass  # __file__ not defined when piped via stdin (curl | python3)

    # 2. Remote catalog
    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "5", CATALOG_URL],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass

    # 3. Offline fallback
    return BUNDLED_CATALOG


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


# ── Install strategies ────────────────────────────────────────────────────────
#
# Each strategy function takes (mcp_def, config) and returns True on success.
# mcp_def is one entry from the catalog; config is the live claude_desktop_config
# dict (mutated in place on success).
#
# To support a new install pattern, add a function here and register it in
# INSTALL_STRATEGIES — no changes to main() or mcps.json schema needed.

def _install_mcp_remote(mcp_def, config):
    """
    Install an MCP whose credential is a token embedded in a URL query param,
    launched via ``npx -y mcp-remote <url>``.

    Required mcp_def["install"] fields:
        instructions      — list of instruction strings printed before the prompt
        credential_prompt — the text used to prompt the user for their token
        url_template      — URL string containing the literal placeholder {token}
        url_encode_pipe   — bool; if True, "|" in the token is encoded as "%7C"
    """
    inst = mcp_def["install"]
    config_key = mcp_def["config_key"]

    print(f"\n── {mcp_def['name']} ──────────────────────────")
    for line in inst.get("instructions", []):
        print(line)
    print()

    token = prompt(inst["credential_prompt"])
    if not token:
        print(f"⚠️  Skipping {mcp_def['name']} — no token provided.")
        return False

    # Encode pipe characters before embedding in the URL query string.
    # GymHappy tokens are in the format "{id}|{secret}"; bare pipes can cause
    # parsing issues on some reverse proxies.
    if inst.get("url_encode_pipe"):
        token = token.replace("|", "%7C")

    url = inst["url_template"].replace("{token}", token)

    config.setdefault("mcpServers", {})[config_key] = {
        "command": "npx",
        "args": ["-y", "mcp-remote", url],
    }
    print(f"✅  {mcp_def['name']} added.")
    return True


def _install_npx_env(mcp_def, config):
    """
    Install an MCP whose credentials live in environment variables,
    launched via ``npx <package>``.

    Required mcp_def["install"] fields:
        package           — npm package name (e.g. "@vendor/mcp-server")
        instructions      — list of instruction strings printed before the prompt
        requires_node_v20 — bool; if True, validates that Node v20+ is present
        env               — list of env var descriptors:
                              { var, prompt_user, credential_prompt?, default? }
                            Variables with prompt_user=True are collected
                            interactively; others use their "default" value.
    """
    inst = mcp_def["install"]
    config_key = mcp_def["config_key"]

    print(f"\n── {mcp_def['name']} ──────────────────────────")
    for line in inst.get("instructions", []):
        print(line)
    print()

    # Collect env vars — prompt for user-supplied ones, apply defaults for rest
    env = {}
    for ev in inst.get("env", []):
        if ev.get("prompt_user"):
            val = prompt(ev["credential_prompt"])
            if not val:
                print(
                    f"⚠️  Skipping {mcp_def['name']} — "
                    f"no value provided for {ev['var']}."
                )
                print("    Run this installer again once you have your key.")
                return False
            env[ev["var"]] = val
        elif "default" in ev:
            env[ev["var"]] = ev["default"]

    if inst.get("requires_node_v20"):
        npx_path, bin_dir = find_node_v20()
        if npx_path is None:
            print(
                f"\n❌  {mcp_def['name']} requires Node.js v20 or higher, "
                "but none was found."
            )
            print("    Install Node v20+ via https://nodejs.org or nvm, then re-run.")
            return False
    else:
        npx_path, bin_dir = "npx", None

    if bin_dir:
        # Inject PATH so Claude Desktop (which launches with a stripped environment)
        # can find the nvm-managed npx and any globally-installed node_modules.
        system_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = f"{bin_dir}:{system_path}"
        print(f"ℹ️   Using Node from nvm: {npx_path}")

    config.setdefault("mcpServers", {})[config_key] = {
        "command": npx_path,
        "args": [inst["package"]],
        "env": env,
    }
    print(f"✅  {mcp_def['name']} added.")
    return True


INSTALL_STRATEGIES = {
    "mcp_remote": _install_mcp_remote,
    "npx_env":    _install_npx_env,
}


def install_mcp(mcp_def, config):
    """Dispatch to the correct install strategy for this MCP definition."""
    strategy = mcp_def["install"]["strategy"]
    fn = INSTALL_STRATEGIES.get(strategy)
    if fn is None:
        print(f"❌  Unknown install strategy '{strategy}' for {mcp_def['name']}.")
        return False
    return fn(mcp_def, config)


# ── Verify strategies ─────────────────────────────────────────────────────────
#
# Each strategy function takes (mcp_def, config) and returns (ok: bool, msg: str).
# A quick network call checks whether the stored credentials are actually working.
# Errors (timeouts, missing curl, etc.) return ok=False with a descriptive message.
#
# To support a new verification pattern, add a function here and register it in
# VERIFY_STRATEGIES — no changes to main() needed.

def _verify_url_token(mcp_def, config):
    """
    Verify an mcp_remote MCP by hitting the URL stored in its config args.

    HTTP 401 means the token was rejected. Any code in success_codes means
    the server accepted the request (the MCP protocol uses various 4xx codes
    for non-auth responses, so we can't require 200).

    Required mcp_def["verify"] fields:
        success_codes — list of HTTP status code strings treated as success
    """
    config_key = mcp_def["config_key"]
    entry = config.get("mcpServers", {}).get(config_key, {})
    args = entry.get("args", [])
    url = next((a for a in args if a.startswith("http")), None)
    if not url:
        return False, "no URL found in config"

    success_codes = set(mcp_def["verify"].get("success_codes", ["200"]))
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "5", url],
            capture_output=True, text=True, timeout=10,
        )
        code = result.stdout.strip()
        if code == "401":
            return False, "token rejected (401)"
        if code in success_codes:
            return True, f"connected (HTTP {code})"
        return False, f"unexpected status {code}"
    except Exception as e:
        return False, f"curl error: {e}"


def _verify_env_api_key(mcp_def, config):
    """
    Verify an npx_env MCP by hitting its API with the stored credentials.

    HTTP 200 = working. HTTP 401 = bad key. Anything else is flagged as
    unexpected so it surfaces in the menu rather than silently passing.

    Required mcp_def["verify"] fields:
        base_url_var — name of the env var holding the service base URL
        api_key_var  — name of the env var holding the API key
        test_path    — URL path to GET (appended to base_url, e.g. "/api/user/current")
        auth_header  — HTTP header name for the key (e.g. "x-api-key")
    """
    config_key = mcp_def["config_key"]
    v = mcp_def["verify"]
    entry = config.get("mcpServers", {}).get(config_key, {})
    env = entry.get("env", {})

    base_url = env.get(v["base_url_var"], "").rstrip("/")
    api_key = env.get(v["api_key_var"], "")
    if not base_url or not api_key:
        return False, f"missing {v['base_url_var']} or {v['api_key_var']} in config"

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "5",
             "-H", f"{v['auth_header']}: {api_key}",
             f"{base_url}{v['test_path']}"],
            capture_output=True, text=True, timeout=10,
        )
        code = result.stdout.strip()
        if code == "200":
            return True, "connected"
        if code == "401":
            return False, "API key rejected (401)"
        return False, f"unexpected status {code}"
    except Exception as e:
        return False, f"curl error: {e}"


VERIFY_STRATEGIES = {
    "url_token":   _verify_url_token,
    "env_api_key": _verify_env_api_key,
}


def verify_mcp(mcp_def, config):
    """Dispatch to the correct verify strategy for this MCP definition."""
    strategy = mcp_def["verify"]["strategy"]
    fn = VERIFY_STRATEGIES.get(strategy)
    if fn is None:
        return False, f"unknown verify strategy: {strategy}"
    return fn(mcp_def, config)


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

    # Load catalog — local mcps.json > remote GitHub > bundled fallback.
    # Build the numbered menu dict from whatever catalog we got.
    catalog = load_catalog()
    MCPS = {str(i + 1): mcp for i, mcp in enumerate(catalog)}

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

    # ── Verify installed MCPs ─────────────────────────────────────────────────
    #
    # For each MCP that appears to be installed (key exists in mcpServers),
    # run a quick live credential check. Cache the results so we don't hit
    # the network on every menu redraw.
    #
    # An MCP is considered "installed" if its config_key appears in mcpServers,
    # regardless of whether it was installed by this script or set up manually.
    mcp_status = {}
    any_installed = any(mcp["config_key"] in installed_keys for mcp in MCPS.values())
    if any_installed:
        print("Checking installed MCPs...", end="", flush=True)
    for key, mcp_def in MCPS.items():
        if mcp_def["config_key"] in installed_keys:
            ok, msg = verify_mcp(mcp_def, config)
            mcp_status[key] = (True, ok, msg)
        else:
            mcp_status[key] = (False, False, "not installed")
    if any_installed:
        print(" done.\n")

    # ── Menu ──────────────────────────────────────────────────────────────────
    while True:
        print("Which MCPs would you like to install?\n")
        for key, mcp_def in MCPS.items():
            is_installed, is_working, status_msg = mcp_status[key]
            if is_installed:
                if is_working:
                    status = "✅ working  (select to update credentials)"
                else:
                    status = f"⚠️  installed but not working: {status_msg}  (select to fix)"
            else:
                status = "⬜ not installed"
            print(f"  [{key}] {mcp_def['name']} — {status}")
            print(f"       {mcp_def['description']}\n")
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
        if install_mcp(MCPS[key], config):
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
