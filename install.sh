#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# PushPress MCP Installer for Claude Desktop
# ─────────────────────────────────────────────────────────────────────
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/install.sh | bash
#
# What it does:
#   1. Ensures Node.js v20+ is installed (auto-installs if needed)
#   2. Walks you through adding PushPress MCP servers to Claude Desktop
#   3. Verifies your credentials work before saving
#
# No Python, Homebrew, or Xcode required.
# ─────────────────────────────────────────────────────────────────────

set -eo pipefail

CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
NPX_CMD=""
NODE_BIN=""

# ── Prompt helper (works when piped via curl | bash) ─────────────────

ask() {
    local msg="$1" reply=""
    if [ -t 0 ]; then
        read -rp "$msg" reply
    else
        printf "%s" "$msg" >/dev/tty
        IFS= read -r reply </dev/tty
    fi
    printf '%s' "$reply"
}

# ── Node.js v20+ detection ──────────────────────────────────────────

find_node() {
    # 1. System node
    if command -v node &>/dev/null; then
        local ver
        ver=$(node -v 2>/dev/null | sed 's/v//' | cut -d. -f1)
        if [ "${ver:-0}" -ge 20 ] 2>/dev/null; then
            NPX_CMD="npx"
            return 0
        fi
    fi

    # 2. nvm
    local nvm_dir="$HOME/.nvm/versions/node"
    if [ -d "$nvm_dir" ]; then
        local best="" best_major=0
        for d in "$nvm_dir"/v*; do
            [ -d "$d" ] || continue
            local m
            m=$(basename "$d" | sed 's/v//' | cut -d. -f1)
            if [ "${m:-0}" -ge 20 ] 2>/dev/null && [ "$m" -gt "$best_major" ]; then
                best="$d"
                best_major="$m"
            fi
        done
        if [ -n "$best" ] && [ -x "$best/bin/npx" ]; then
            NPX_CMD="$best/bin/npx"
            NODE_BIN="$best/bin"
            export PATH="$best/bin:$PATH"
            return 0
        fi
    fi

    # 3. fnm
    local fnm_dir="$HOME/Library/Application Support/fnm/node-versions"
    if [ -d "$fnm_dir" ]; then
        local best="" best_major=0
        for d in "$fnm_dir"/v*/installation; do
            [ -d "$d" ] || continue
            local m
            m=$(echo "$d" | grep -oE 'v[0-9]+' | head -1 | sed 's/v//')
            if [ "${m:-0}" -ge 20 ] 2>/dev/null && [ "$m" -gt "$best_major" ]; then
                best="$d"
                best_major="$m"
            fi
        done
        if [ -n "$best" ] && [ -x "$best/bin/npx" ]; then
            NPX_CMD="$best/bin/npx"
            NODE_BIN="$best/bin"
            export PATH="$best/bin:$PATH"
            return 0
        fi
    fi

    return 1
}

install_node() {
    echo ""
    echo "Node.js v20+ is required but wasn't found on this Mac."
    echo ""
    echo "I'll install it now using the official installer from nodejs.org."
    echo "This will ask for your Mac password (the same one you use to log in)."
    echo ""

    # Find the latest v22.x LTS .pkg from nodejs.org
    local pkg
    pkg=$(curl -fsSL "https://nodejs.org/dist/latest-v22.x/" 2>/dev/null | \
          grep -oE 'node-v[0-9]+\.[0-9]+\.[0-9]+\.pkg' | head -1)

    if [ -z "$pkg" ]; then
        echo "Could not determine the latest Node.js version."
        echo "Please install Node.js v22+ manually from https://nodejs.org"
        exit 1
    fi

    local url="https://nodejs.org/dist/latest-v22.x/$pkg"
    echo "Downloading $pkg..."
    curl -fSL --progress-bar "$url" -o /tmp/node-install.pkg

    echo ""
    echo "Installing (enter your Mac password if prompted)..."
    sudo installer -pkg /tmp/node-install.pkg -target /
    rm -f /tmp/node-install.pkg

    # Make sure node is on PATH
    export PATH="/usr/local/bin:$PATH"
    hash -r 2>/dev/null || true

    if ! command -v node &>/dev/null; then
        echo ""
        echo "Node.js was installed but isn't available yet."
        echo "Close this Terminal window, open a new one, and run this installer again."
        exit 1
    fi

    echo ""
    echo "Node.js $(node -v) installed successfully."
    echo ""
    NPX_CMD="npx"
}

ensure_node() {
    if find_node; then
        return 0
    fi
    install_node
}

# ── JSON helpers (use node since it's guaranteed available) ──────────

json_mcp_keys() {
    node -e "
        const c = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'));
        console.log(Object.keys(c.mcpServers || {}).join(','));
    " "$CONFIG" 2>/dev/null || echo ""
}

json_get_url() {
    local key="$1"
    node -e "
        const c = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'));
        const a = ((c.mcpServers || {})[process.argv[2]] || {}).args || [];
        const u = a.find(x => typeof x === 'string' && x.startsWith('http'));
        if (u) process.stdout.write(u);
    " "$CONFIG" "$key" 2>/dev/null || true
}

json_get_env() {
    local key="$1" var="$2"
    node -e "
        const c = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'));
        const e = ((c.mcpServers || {})[process.argv[2]] || {}).env || {};
        if (e[process.argv[3]]) process.stdout.write(e[process.argv[3]]);
    " "$CONFIG" "$key" "$var" 2>/dev/null || true
}

json_set_mcp() {
    local key="$1" entry="$2"
    node -e "
        const fs = require('fs');
        const p = process.argv[1], k = process.argv[2], e = JSON.parse(process.argv[3]);
        let c;
        try { c = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { c = {}; }
        c.mcpServers = c.mcpServers || {};
        c.mcpServers[k] = e;
        const tmp = p + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(c, null, 2) + '\n');
        fs.renameSync(tmp, p);
    " "$CONFIG" "$key" "$entry"
}

# ── Verification ────────────────────────────────────────────────────

verify_url_token() {
    local url="$1"
    local code
    code=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null) || code="000"
    case "$code" in
        200|201|400|404|405|406) echo "ok" ;;
        401) echo "rejected" ;;
        *) echo "error" ;;
    esac
}

verify_api_key() {
    local url="$1" key="$2" header="$3"
    local code
    code=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "$header: $key" "$url" 2>/dev/null) || code="000"
    case "$code" in
        200) echo "ok" ;;
        401) echo "rejected" ;;
        *) echo "error" ;;
    esac
}

# ── Install: GymHappy Support ───────────────────────────────────────

do_gymhappy() {
    echo ""
    echo "── GymHappy Support ──────────────────────"
    echo ""
    echo "Get your token at: https://app.gymhappy.co/super/mcp-token"
    echo "(Log in to GymHappy first if prompted)"
    echo ""

    local token
    token=$(ask "Paste your GymHappy token (or Enter to skip): ")
    [ -z "$token" ] && { echo ""; echo "Skipping GymHappy — no token provided."; return 1; }

    # Encode pipe characters (GymHappy tokens use id|secret format)
    token="${token//|/%7C}"
    local url="https://app.gymhappy.co/mcp/support?mcp_token=${token}"

    local result
    result=$(verify_url_token "$url")
    if [ "$result" = "rejected" ]; then
        echo ""
        echo "Token was rejected (401). Double-check it and try again."
        return 1
    fi

    local entry
    entry=$(node -e "console.log(JSON.stringify({
        command: 'npx',
        args: ['-y', 'mcp-remote', process.argv[1]]
    }))" "$url")

    json_set_mcp "gymhappy-support" "$entry"
    echo ""
    echo "GymHappy Support configured."
}

# ── Install: Metabase ───────────────────────────────────────────────

do_metabase() {
    echo ""
    echo "── Metabase ──────────────────────────────"
    echo ""
    echo "To get a Metabase API key:"
    echo ""
    echo "  1. Open Slack -> #support-data"
    echo "  2. Send: \"Hi @data, I need a Metabase API key for Claude Cowork.\""
    echo "     https://pushpress.slack.com/channels/support-data"
    echo "  3. The data team will send you a key via 1Password."
    echo ""

    local api_key
    api_key=$(ask "Paste your Metabase API key (or Enter to skip): ")
    [ -z "$api_key" ] && { echo ""; echo "Skipping Metabase. Re-run this installer when you have your key."; return 1; }

    local base_url="https://pushpress.metabaseapp.com/"

    local result
    result=$(verify_api_key "${base_url}api/user/current" "$api_key" "x-api-key")
    if [ "$result" = "rejected" ]; then
        echo ""
        echo "API key was rejected (401). Double-check it and try again."
        return 1
    fi

    local entry
    if [ -n "$NODE_BIN" ]; then
        echo "Using Node from nvm: $NPX_CMD"
        entry=$(node -e "console.log(JSON.stringify({
            command: process.argv[1],
            args: ['@cognitionai/metabase-mcp-server'],
            env: {
                METABASE_URL: process.argv[2],
                METABASE_API_KEY: process.argv[3],
                PATH: process.argv[4] + ':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
            }
        }))" "$NPX_CMD" "$base_url" "$api_key" "$NODE_BIN")
    else
        entry=$(node -e "console.log(JSON.stringify({
            command: process.argv[1],
            args: ['@cognitionai/metabase-mcp-server'],
            env: {
                METABASE_URL: process.argv[2],
                METABASE_API_KEY: process.argv[3]
            }
        }))" "$NPX_CMD" "$base_url" "$api_key")
    fi

    json_set_mcp "metabase" "$entry"
    echo ""
    echo "Metabase configured."
}

# ── Main ────────────────────────────────────────────────────────────

echo ""
echo "PushPress MCP Installer"
echo "========================"
echo "Adds PushPress tools to Claude Desktop."
echo ""

# Step 1: Ensure Node.js
ensure_node

# Step 2: Check Claude Desktop config exists
if [ ! -f "$CONFIG" ]; then
    echo "Claude Desktop config not found at:"
    echo "  $CONFIG"
    echo ""
    echo "Make sure Claude Desktop is installed and has been opened at least once."
    exit 1
fi

# Step 3: Backup config
cp "$CONFIG" "${CONFIG}.backup"

# Step 4: Check current MCP status
echo "Checking installed MCPs..."

keys=$(json_mcp_keys)
gh_status="not installed"
mb_status="not installed"

if echo ",$keys," | grep -q ",gymhappy-support,"; then
    gh_url=$(json_get_url "gymhappy-support")
    if [ -n "$gh_url" ]; then
        r=$(verify_url_token "$gh_url")
        [ "$r" = "ok" ] && gh_status="working" || gh_status="not working"
    fi
fi

if echo ",$keys," | grep -q ",metabase,"; then
    mb_url=$(json_get_env "metabase" "METABASE_URL")
    mb_key=$(json_get_env "metabase" "METABASE_API_KEY")
    if [ -n "$mb_url" ] && [ -n "$mb_key" ]; then
        r=$(verify_api_key "${mb_url}api/user/current" "$mb_key" "x-api-key")
        [ "$r" = "ok" ] && mb_status="working" || mb_status="not working"
    fi
fi

# Format status icons
case "$gh_status" in
    working)       gh_display="working  (select to update credentials)" ;;
    "not working") gh_display="installed but not working  (select to fix)" ;;
    *)             gh_display="not installed" ;;
esac

case "$mb_status" in
    working)       mb_display="working  (select to update credentials)" ;;
    "not working") mb_display="installed but not working  (select to fix)" ;;
    *)             mb_display="not installed" ;;
esac

# Step 5: Interactive menu
while true; do
    echo ""
    echo "Which MCPs would you like to install?"
    echo ""
    echo "  [1] GymHappy Support — $gh_display"
    echo "       Look up gyms, members, reviews, and diagnose issues"
    echo ""
    echo "  [2] Metabase — $mb_display"
    echo "       Query PushPress data and pull live metrics"
    echo ""
    echo "  [A] All PushPress MCPs"
    echo "  [Q] Quit"
    echo ""

    choice=$(ask "Your choice: ")
    choice=$(printf '%s' "$choice" | tr '[:lower:]' '[:upper:]')

    case "$choice" in
        ""|Q) echo "Bye!"; exit 0 ;;
        1|2|A) break ;;
        *) echo "Invalid choice. Pick a number, A, or Q." ;;
    esac
done

# Step 6: Run installers
installed=()
case "$choice" in
    1) do_gymhappy && installed+=("GymHappy Support") || true ;;
    2) do_metabase && installed+=("Metabase") || true ;;
    A)
        do_gymhappy && installed+=("GymHappy Support") || true
        do_metabase && installed+=("Metabase") || true
        ;;
esac

if [ ${#installed[@]} -eq 0 ]; then
    echo ""
    echo "Nothing was installed."
    exit 0
fi

# Step 7: Done
echo ""
echo "========================================"
echo "Installed: ${installed[*]}"
echo ""
echo "Restart Claude Desktop for changes to take effect."
echo "(Quit the app completely, then reopen it.)"
echo "========================================"
