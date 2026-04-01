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
    echo "Installing it now (no password needed)..."
    echo ""

    # Detect architecture
    local arch
    arch=$(uname -m)
    if [ "$arch" = "arm64" ]; then
        local node_arch="arm64"
    else
        local node_arch="x64"
    fi

    # Find the latest v22.x tarball for this architecture
    local tarball
    tarball=$(curl -fsSL "https://nodejs.org/dist/latest-v22.x/" 2>/dev/null | \
              grep -oE "node-v[0-9]+\.[0-9]+\.[0-9]+-darwin-${node_arch}\.tar\.gz" | head -1)

    if [ -z "$tarball" ]; then
        echo "Could not determine the latest Node.js version."
        echo "Please install Node.js v22+ manually from https://nodejs.org"
        exit 1
    fi

    local url="https://nodejs.org/dist/latest-v22.x/$tarball"
    local node_dir="$HOME/.local/node"

    echo "Downloading $tarball..."
    mkdir -p "$node_dir"
    curl -fSL --progress-bar "$url" | tar -xz --strip-components=1 -C "$node_dir"

    if [ ! -x "$node_dir/bin/node" ]; then
        echo ""
        echo "Download failed. Please install Node.js v22+ manually from https://nodejs.org"
        exit 1
    fi

    # Add to PATH for this session
    export PATH="$node_dir/bin:$PATH"
    NPX_CMD="$node_dir/bin/npx"
    NODE_BIN="$node_dir/bin"

    # Add to shell profile so Claude Desktop and future sessions can find it
    local shell_rc="$HOME/.zshrc"
    [ ! -f "$shell_rc" ] && shell_rc="$HOME/.bash_profile"
    if ! grep -q '.local/node/bin' "$shell_rc" 2>/dev/null; then
        echo '' >> "$shell_rc"
        echo '# Node.js (installed by PushPress Cowork setup)' >> "$shell_rc"
        echo 'export PATH="$HOME/.local/node/bin:$PATH"' >> "$shell_rc"
    fi

    echo ""
    echo "Node.js $(node -v) installed to ~/.local/node/"
    echo ""
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

# ── Install: GitHub (PushPress Code) ────────────────────────────────

WRAPPER_DIR="$HOME/.config/pushpress"
WRAPPER_PATH="$WRAPPER_DIR/github-mcp-wrapper.mjs"
WRAPPER_URL="https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/github-mcp-wrapper.mjs"

# GitHub App credentials (not secrets — just identifiers)
GITHUB_APP_ID_VAL="3239230"
GITHUB_INSTALLATION_ID_VAL="120511610"

do_github() {
    echo ""
    echo "── GitHub (PushPress Code) ─────────────────"
    echo ""
    echo "This gives Claude read-only access to PushPress source code."
    echo "Uses a shared GitHub App — no personal tokens needed."
    echo ""
    echo "Get the private key from 1Password:"
    echo "  Search for \"PushPress Cowork Code Reader\""
    echo "  Copy the ENTIRE contents (including BEGIN/END lines)"
    echo ""

    local pem
    echo "Paste the private key contents, then press Enter twice:"
    if [ -t 0 ]; then
        pem=""
        while IFS= read -r line; do
            [ -z "$line" ] && [ -n "$pem" ] && break
            pem="${pem}${line}\n"
        done
    else
        pem=""
        while IFS= read -r line </dev/tty; do
            [ -z "$line" ] && [ -n "$pem" ] && break
            pem="${pem}${line}\n"
        done
    fi

    if [ -z "$pem" ]; then
        echo "Skipping GitHub. Re-run this installer when you have the key."
        return 1
    fi

    # Verify: try generating a token with these credentials
    echo "Verifying credentials..."
    local verify_result
    verify_result=$(GITHUB_APP_ID="$GITHUB_APP_ID_VAL" \
        GITHUB_INSTALLATION_ID="$GITHUB_INSTALLATION_ID_VAL" \
        GITHUB_APP_PRIVATE_KEY="$(printf '%b' "$pem")" \
        node -e "
            import { createSign } from 'node:crypto';
            import { request } from 'node:https';
            const pk = process.env.GITHUB_APP_PRIVATE_KEY;
            const id = process.env.GITHUB_APP_ID;
            const iid = process.env.GITHUB_INSTALLATION_ID;
            function b64u(b){return b.toString('base64').replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');}
            const now=Math.floor(Date.now()/1000);
            const h=b64u(Buffer.from(JSON.stringify({alg:'RS256',typ:'JWT'})));
            const p=b64u(Buffer.from(JSON.stringify({iat:now-60,exp:now+600,iss:id})));
            const d=h+'.'+p;
            const s=createSign('SHA256');s.update(d);
            const jwt=d+'.'+b64u(s.sign(pk));
            const r=request({hostname:'api.github.com',path:'/app/installations/'+iid+'/access_tokens',method:'POST',
                headers:{Authorization:'Bearer '+jwt,Accept:'application/vnd.github+json','User-Agent':'test','X-GitHub-Api-Version':'2022-11-28'}
            },(res)=>{let b='';res.on('data',c=>b+=c);res.on('end',()=>{console.log(res.statusCode===201?'ok':'fail:'+res.statusCode);});});
            r.on('error',()=>console.log('fail:network'));r.end();
        " --input-type=module 2>&1) || verify_result="fail:error"

    if [ "$verify_result" != "ok" ]; then
        echo ""
        echo "Credentials didn't work ($verify_result)."
        echo "Make sure you copied the ENTIRE private key including the"
        echo "-----BEGIN RSA PRIVATE KEY----- and -----END RSA PRIVATE KEY----- lines."
        return 1
    fi
    echo "Credentials verified."

    # Download the wrapper script
    mkdir -p "$WRAPPER_DIR"
    curl -fsSL "$WRAPPER_URL" -o "$WRAPPER_PATH" 2>/dev/null
    if [ ! -f "$WRAPPER_PATH" ]; then
        echo "Failed to download wrapper script."
        return 1
    fi

    # Build config entry — the wrapper reads env vars and generates tokens
    local node_cmd="node"
    [ -n "$NODE_BIN" ] && node_cmd="$NODE_BIN/node"

    local entry
    if [ -n "$NODE_BIN" ]; then
        entry=$(node -e "console.log(JSON.stringify({
            command: process.argv[1],
            args: [process.argv[2]],
            env: {
                GITHUB_APP_ID: process.argv[3],
                GITHUB_INSTALLATION_ID: process.argv[4],
                GITHUB_APP_PRIVATE_KEY: process.argv[5],
                NPX_PATH: process.argv[6],
                PATH: process.argv[7] + ':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
            }
        }))" "$node_cmd" "$WRAPPER_PATH" "$GITHUB_APP_ID_VAL" "$GITHUB_INSTALLATION_ID_VAL" "$(printf '%b' "$pem")" "$NPX_CMD" "$NODE_BIN")
    else
        entry=$(node -e "console.log(JSON.stringify({
            command: 'node',
            args: [process.argv[1]],
            env: {
                GITHUB_APP_ID: process.argv[2],
                GITHUB_INSTALLATION_ID: process.argv[3],
                GITHUB_APP_PRIVATE_KEY: process.argv[4]
            }
        }))" "$WRAPPER_PATH" "$GITHUB_APP_ID_VAL" "$GITHUB_INSTALLATION_ID_VAL" "$(printf '%b' "$pem")")
    fi

    json_set_mcp "github" "$entry"
    echo ""
    echo "GitHub configured."
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

PLUGIN_URL="https://raw.githubusercontent.com/duyemura/pushpress-claude-mcp-installer/main/pushpress-team.zip"
PLUGIN_DEST="$HOME/Desktop/pushpress-team.zip"
COWORK_SESSIONS="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"

echo ""
echo "PushPress Cowork Setup"
echo "======================"
echo ""

# Step 1: Ensure Node.js
ensure_node

# Step 2: Check/install the Cowork plugin
plugin_dir=$(find "$COWORK_SESSIONS" -path "*/cowork_plugins/marketplaces/local-desktop-app-uploads/pushpress-team" -type d 2>/dev/null | head -1)
uploads_dir=$(find "$COWORK_SESSIONS" -path "*/cowork_plugins/marketplaces/local-desktop-app-uploads" -type d 2>/dev/null | head -1)

if [ -n "$plugin_dir" ] && [ -f "$plugin_dir/.mcp.json" ]; then
    echo "PushPress Team plugin: already installed"

    # Auto-update to latest version
    if curl -fsSL "$PLUGIN_URL" -o /tmp/pushpress-team-update.zip 2>/dev/null; then
        unzip -qo /tmp/pushpress-team-update.zip -d "$plugin_dir" 2>/dev/null && \
            echo "(Updated to latest version)" || true
        rm -f /tmp/pushpress-team-update.zip
    fi
    echo ""

elif [ -n "$uploads_dir" ]; then
    # Cowork exists but plugin not installed — auto-install
    echo "Installing PushPress Team plugin..."
    if curl -fsSL "$PLUGIN_URL" -o /tmp/pushpress-team-install.zip 2>/dev/null; then
        mkdir -p "$uploads_dir/pushpress-team"
        unzip -qo /tmp/pushpress-team-install.zip -d "$uploads_dir/pushpress-team" 2>/dev/null

        # Register in marketplace.json
        marketplace_json="$uploads_dir/.claude-plugin/marketplace.json"
        if [ -f "$marketplace_json" ]; then
            node -e "
                const fs = require('fs');
                const p = process.argv[1];
                const m = JSON.parse(fs.readFileSync(p, 'utf8'));
                if (!m.plugins.some(x => x.name === 'pushpress-team')) {
                    m.plugins.push({name:'pushpress-team',version:'1.2.0',source:'./pushpress-team'});
                    fs.writeFileSync(p, JSON.stringify(m, null, 2) + '\n');
                }
            " "$marketplace_json" 2>/dev/null || true
        fi

        rm -f /tmp/pushpress-team-install.zip
        echo "PushPress Team plugin installed."
        echo "  (Restart Claude Desktop to activate it.)"
    else
        echo "Could not download plugin. Check your internet connection."
    fi
    echo ""

else
    # No Cowork session found — download to Desktop with step-by-step
    echo "Downloading PushPress Team plugin..."
    if curl -fsSL "$PLUGIN_URL" -o "$PLUGIN_DEST" 2>/dev/null; then
        echo "Saved to your Desktop: pushpress-team.zip"
        echo ""
        echo "  To install the plugin:"
        echo ""
        echo "  1. Open the Claude app (the tan/beige icon in your Dock)"
        echo "  2. Start a new project:"
        echo "     - Click the folder icon in the top-left corner"
        echo "     - Pick any folder (like Documents — it doesn't matter which)"
        echo "  3. Look for a puzzle piece icon in the left sidebar"
        echo "  4. Click it, then choose \"Install from file\""
        echo "  5. Navigate to your Desktop and select pushpress-team.zip"
        echo "  6. Quit Claude completely (Cmd+Q) and reopen it"
        echo ""
        echo "  Then run this installer again to set up your tools."
        echo ""
        echo "  Stuck? Screenshot your screen and send it to #support-data on Slack."
    else
        echo "Could not download plugin. Check your internet connection."
    fi
    echo ""
fi

# Step 3: Check Claude Desktop config exists
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
git_status="not installed"

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

# Format status display
format_status() {
    case "$1" in
        working)       echo "working  (select to update credentials)" ;;
        "not working") echo "installed but not working  (select to fix)" ;;
        *)             echo "not installed" ;;
    esac
}

gh_display=$(format_status "$gh_status")
mb_display=$(format_status "$mb_status")

# ── Detailed help for non-technical users ────────────────────────────

show_help() {
    echo ""
    echo "================================================================"
    echo "  DETAILED SETUP GUIDE"
    echo "================================================================"
    echo ""
    echo "  What is this?"
    echo "  ─────────────"
    echo "  This installer connects Claude (your AI assistant) to"
    echo "  PushPress tools so it can help you with real work —"
    echo "  like looking up gym info or pulling data reports."
    echo ""
    echo "  What you'll need"
    echo "  ────────────────"
    echo "  Each tool needs a \"key\" (like a password) to connect."
    echo "  You only need to set this up once."
    echo ""
    echo "  GymHappy Support — lets Claude look up gyms, reviews,"
    echo "    members, and diagnose support issues."
    echo ""
    echo "    How to get your key:"
    echo "      1. Open this link in your browser:"
    echo "         https://app.gymhappy.co/super/mcp-token"
    echo "      2. Log in if asked (use your normal GymHappy login)"
    echo "      3. You'll see a long string of letters and numbers"
    echo "      4. Select all of it and copy it (Cmd+A, then Cmd+C)"
    echo "      5. Come back here and paste it (Cmd+V) when asked"
    echo ""
    echo "  Metabase — lets Claude query PushPress data and metrics."
    echo ""
    echo "    How to get your key:"
    echo "      1. Open Slack and go to the #support-data channel"
    echo "         https://pushpress.slack.com/channels/support-data"
    echo "      2. Send this message:"
    echo "         \"Hi! I need a Metabase API key for Claude Cowork.\""
    echo "      3. Someone from the data team will send you a key"
    echo "         via 1Password (check your 1Password notifications)"
    echo "      4. Copy the key and paste it here when asked"
    echo ""
    echo "  What happens next"
    echo "  ─────────────────"
    echo "  After you paste your key(s), this installer will:"
    echo "    - Verify the key works"
    echo "    - Save it to Claude Desktop's config"
    echo "    - You'll need to quit and reopen Claude Desktop"
    echo ""
    echo "  If something goes wrong"
    echo "  ──────────────────────"
    echo "  - You can run this installer again anytime — it's safe"
    echo "  - It won't break anything that's already working"
    echo "  - If you get stuck, ask in #support-data on Slack"
    echo ""
    echo "================================================================"
    echo ""
}

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
    echo "  [H] Help — detailed instructions for each step"
    echo "  [Q] Quit"
    echo ""

    choice=$(ask "Your choice: ")
    choice=$(printf '%s' "$choice" | tr '[:lower:]' '[:upper:]')

    case "$choice" in
        ""|Q) echo "Bye!"; exit 0 ;;
        H) show_help; continue ;;
        1|2|A) break ;;
        *) echo "Invalid choice. Pick a number, A, H, or Q." ;;
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
echo "MCPs installed: ${installed[*]}"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Desktop (quit completely, then reopen)"
echo "  2. If you haven't yet: install the plugin in Cowork"
echo "     (Open Cowork → Plugins → pushpress-team.zip on your Desktop)"
echo "========================================"
