#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// GitHub MCP Wrapper for PushPress Cowork
// ─────────────────────────────────────────────────────────────────────
// Generates a short-lived GitHub App installation token from a .pem
// private key, then launches the GitHub MCP server with that token.
//
// This means no long-lived PATs on anyone's machine — just the .pem
// (which never expires) and auto-generated 1-hour tokens.
//
// Required env vars:
//   GITHUB_APP_ID            — from the GitHub App settings page
//   GITHUB_INSTALLATION_ID   — from the installation URL
//   GITHUB_APP_PRIVATE_KEY   — contents of the .pem file (with \n for newlines)
//
// Optional env vars:
//   GITHUB_READ_ONLY         — set to "true" to restrict to read-only ops
// ─────────────────────────────────────────────────────────────────────

import { createSign } from "node:crypto";
import { request } from "node:https";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";

const APP_ID = process.env.GITHUB_APP_ID;
const INSTALLATION_ID = process.env.GITHUB_INSTALLATION_ID;
const PRIVATE_KEY = (process.env.GITHUB_APP_PRIVATE_KEY || "").replace(/\\n/g, "\n");

if (!APP_ID || !INSTALLATION_ID || !PRIVATE_KEY) {
  console.error("Missing required env vars: GITHUB_APP_ID, GITHUB_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY");
  process.exit(1);
}

// ── JWT generation (RS256, no external deps) ────────────────────────

function base64url(buf) {
  return buf.toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function createJWT() {
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" })));
  const payload = base64url(Buffer.from(JSON.stringify({
    iat: now - 60,       // issued 60s ago (clock skew buffer)
    exp: now + 10 * 60,  // expires in 10 minutes
    iss: APP_ID,
  })));

  const data = `${header}.${payload}`;
  const sign = createSign("SHA256");
  sign.update(data);
  const signature = base64url(sign.sign(PRIVATE_KEY));

  return `${data}.${signature}`;
}

// ── Installation token exchange ─────────────────────────────────────

function getInstallationToken(jwt) {
  return new Promise((resolve, reject) => {
    const req = request({
      hostname: "api.github.com",
      path: `/app/installations/${INSTALLATION_ID}/access_tokens`,
      method: "POST",
      headers: {
        "Authorization": `Bearer ${jwt}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "PushPress-Cowork-MCP",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    }, (res) => {
      let body = "";
      res.on("data", (chunk) => body += chunk);
      res.on("end", () => {
        if (res.statusCode !== 201) {
          reject(new Error(`GitHub API returned ${res.statusCode}: ${body}`));
          return;
        }
        try {
          resolve(JSON.parse(body).token);
        } catch (e) {
          reject(new Error(`Failed to parse response: ${body}`));
        }
      });
    });
    req.on("error", reject);
    req.end();
  });
}

// ── Launch the MCP server ───────────────────────────────────────────

async function main() {
  let token;
  try {
    const jwt = createJWT();
    token = await getInstallationToken(jwt);
  } catch (err) {
    console.error(`Failed to generate GitHub token: ${err.message}`);
    process.exit(1);
  }

  // Find npx — use the same one that launched us, or fall back to PATH
  const npxCmd = process.env.NPX_PATH || "npx";

  const child = spawn(npxCmd, ["-y", "@modelcontextprotocol/server-github"], {
    env: {
      ...process.env,
      GITHUB_PERSONAL_ACCESS_TOKEN: token,
    },
    stdio: "inherit",
  });

  child.on("error", (err) => {
    console.error(`Failed to start MCP server: ${err.message}`);
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code || 0);
  });
}

main();
