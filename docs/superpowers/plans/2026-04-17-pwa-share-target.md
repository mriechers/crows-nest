# PWA Share Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PWA share target at `share.bymarkriechers.com/save` that replaces the broken iOS Shortcut for queuing URLs into the Crow's Nest pipeline.

**Architecture:** Three new routes (`/save`, `/manifest.json`, `/sw.js`) are added to the existing Cloudflare Worker. The PWA HTML is defined in a separate module (`worker/src/pwa.js`) and imported by the Worker. The `wrangler.toml` route pattern is widened from `/api/*` to `/*` since the Worker already falls through to R2 for unmatched paths.

**Tech Stack:** Cloudflare Workers, D1, vanilla HTML/CSS/JS, Web Share Target API, Web App Manifest

**Spec:** `docs/superpowers/specs/2026-04-17-pwa-share-target-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `worker/src/pwa.js` | Exports three functions: `serveSavePage()`, `serveManifest()`, `serveServiceWorker()` — each returns a `Response` |
| Modify | `worker/src/index.js:12-59` | Add route handlers for `/save`, `/manifest.json`, `/sw.js` in the `fetch` dispatcher |
| Modify | `worker/wrangler.toml:7-9` | Change route pattern from `/api/*` to `/*` |

---

### Task 1: Create the PWA page module

**Files:**
- Create: `worker/src/pwa.js`

- [ ] **Step 1: Create `worker/src/pwa.js` with `serveSavePage()`**

This function returns an HTML `Response` containing the complete PWA page. The page has two views toggled by JS: setup (token entry) and form (URL + context + save button).

```javascript
// worker/src/pwa.js
// PWA share target page for Crow's Nest URL ingest.

export function serveSavePage() {
  return new Response(SAVE_HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

export function serveManifest() {
  const manifest = {
    name: "Crow's Nest",
    short_name: "Crow's Nest",
    start_url: "/save",
    display: "standalone",
    background_color: "#1a1a2e",
    theme_color: "#1a1a2e",
    icons: [
      {
        src: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🪺</text></svg>",
        sizes: "any",
        type: "image/svg+xml",
      },
    ],
    share_target: {
      action: "/save",
      method: "GET",
      params: {
        url: "url",
        title: "context",
      },
    },
  };
  return Response.json(manifest, {
    headers: { "Content-Type": "application/manifest+json" },
  });
}

export function serveServiceWorker() {
  const sw = `// Minimal service worker — required for PWA installability.
// No offline caching; requests pass through to network.
self.addEventListener("fetch", () => {});
`;
  return new Response(sw, {
    headers: { "Content-Type": "application/javascript" },
  });
}

const SAVE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#1a1a2e">
<link rel="manifest" href="/manifest.json">
<title>Crow's Nest</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: #1a1a2e; color: #e0e0e0;
    min-height: 100vh; min-height: 100dvh;
    display: flex; align-items: center; justify-content: center;
    padding: 1rem;
  }
  .card {
    background: #16213e; border-radius: 12px; padding: 1.5rem;
    max-width: 480px; width: 100%;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  h1 { font-size: 1.3rem; margin-bottom: 0.3rem; }
  .subtitle { color: #888; font-size: 0.85rem; margin-bottom: 1.2rem; }
  label { display: block; font-size: 0.8rem; color: #aaa; margin-bottom: 0.3rem; }
  input, textarea {
    width: 100%; padding: 0.6rem 0.8rem; border-radius: 6px;
    border: 1px solid #333; background: #0f3460; color: #e0e0e0;
    font-family: inherit; font-size: 0.95rem; margin-bottom: 0.8rem;
    -webkit-appearance: none;
  }
  textarea { resize: vertical; min-height: 2.4rem; height: 2.4rem; }
  input:focus, textarea:focus { outline: none; border-color: #e94560; }
  .btn {
    width: 100%; padding: 0.75rem; border: none; border-radius: 8px;
    background: #e94560; color: #fff; font-size: 1rem; font-weight: 600;
    cursor: pointer; transition: background 0.2s, transform 0.1s;
  }
  .btn:active { transform: scale(0.98); }
  .btn:disabled { background: #444; cursor: not-allowed; transform: none; }
  .btn.success { background: #2ecc71; }
  .btn.error { background: #c73650; }
  .error-msg {
    color: #e94560; font-size: 0.85rem; margin-top: 0.5rem;
    min-height: 1.2rem;
  }
  .settings {
    position: absolute; top: 0.8rem; right: 0.8rem;
    background: none; border: none; color: #555; font-size: 1.2rem;
    cursor: pointer; padding: 0.3rem;
  }
  .settings:hover { color: #aaa; }
  .card { position: relative; }
  .hidden { display: none; }
  .install-hint {
    margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #333;
    font-size: 0.8rem; color: #666; text-align: center;
  }
</style>
</head>
<body>
<div class="card">
  <button class="settings hidden" id="settingsBtn" title="Settings">&#9881;</button>

  <!-- Setup view -->
  <div id="setup">
    <h1>Crow's Nest</h1>
    <p class="subtitle">Connect to the pipeline</p>
    <label for="tokenInput">Ingest Token</label>
    <input type="password" id="tokenInput" placeholder="Paste your token" autocomplete="off">
    <button class="btn" id="connectBtn">Connect</button>
    <p class="error-msg" id="setupError"></p>
    <div class="install-hint">
      After connecting, tap Share &#x2794; Add to Home Screen to enable sharing from any app.
    </div>
  </div>

  <!-- Form view -->
  <div id="form" class="hidden">
    <h1>Crow's Nest</h1>
    <p class="subtitle">Save to pipeline</p>
    <label for="urlInput">URL</label>
    <input type="url" id="urlInput" placeholder="https://..." autocomplete="off">
    <label for="contextInput">Context <span style="color:#555">(optional)</span></label>
    <textarea id="contextInput" placeholder="Notes about this link..."></textarea>
    <button class="btn" id="saveBtn">Save</button>
    <p class="error-msg" id="formError"></p>
  </div>
</div>

<script>
(function() {
  const TOKEN_KEY = "crows_nest_token";

  // DOM refs
  const setupView   = document.getElementById("setup");
  const formView     = document.getElementById("form");
  const settingsBtn  = document.getElementById("settingsBtn");
  const tokenInput   = document.getElementById("tokenInput");
  const connectBtn   = document.getElementById("connectBtn");
  const setupError   = document.getElementById("setupError");
  const urlInput     = document.getElementById("urlInput");
  const contextInput = document.getElementById("contextInput");
  const saveBtn      = document.getElementById("saveBtn");
  const formError    = document.getElementById("formError");

  // --- Token management ---

  function getToken() { return localStorage.getItem(TOKEN_KEY); }
  function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  // Check for token in URL fragment (#token=...)
  function checkFragmentToken() {
    const hash = location.hash;
    if (hash.startsWith("#token=")) {
      const t = decodeURIComponent(hash.slice(7));
      if (t) { setToken(t); history.replaceState(null, "", location.pathname + location.search); }
    }
  }

  // --- View switching ---

  function showSetup() {
    setupView.classList.remove("hidden");
    formView.classList.add("hidden");
    settingsBtn.classList.add("hidden");
    tokenInput.value = "";
    setupError.textContent = "";
  }

  function showForm() {
    setupView.classList.add("hidden");
    formView.classList.remove("hidden");
    settingsBtn.classList.remove("hidden");
    formError.textContent = "";
    // Pre-fill from share target query params
    const params = new URLSearchParams(location.search);
    const sharedUrl = params.get("url");
    const sharedContext = params.get("context");
    if (sharedUrl) urlInput.value = sharedUrl;
    if (sharedContext) contextInput.value = sharedContext;
    // Clean URL without losing PWA context
    if (sharedUrl || sharedContext) {
      history.replaceState(null, "", "/save");
    }
    urlInput.focus();
  }

  // --- Connect ---

  connectBtn.addEventListener("click", function() {
    const t = tokenInput.value.trim();
    if (!t) { setupError.textContent = "Token is required."; return; }
    setToken(t);
    showForm();
  });

  // --- Settings (clear token) ---

  settingsBtn.addEventListener("click", function() {
    if (confirm("Disconnect from Crow's Nest? You'll need to re-enter your token.")) {
      clearToken();
      showSetup();
    }
  });

  // --- Save ---

  saveBtn.addEventListener("click", async function() {
    const url = urlInput.value.trim();
    if (!url) { formError.textContent = "URL is required."; return; }

    formError.textContent = "";
    saveBtn.textContent = "Saving...";
    saveBtn.disabled = true;
    saveBtn.className = "btn";

    try {
      const resp = await fetch("/api/ingest", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": getToken(),
        },
        body: JSON.stringify({
          url: url,
          context: contextInput.value.trim() || undefined,
          source: "pwa",
        }),
      });
      const data = await resp.json();

      if (!resp.ok || data.error) {
        throw new Error(data.error || "HTTP " + resp.status);
      }

      saveBtn.textContent = "Saved";
      saveBtn.className = "btn success";
      setTimeout(function() {
        urlInput.value = "";
        contextInput.value = "";
        saveBtn.textContent = "Save";
        saveBtn.className = "btn";
        saveBtn.disabled = false;
      }, 1500);
    } catch (err) {
      saveBtn.textContent = "Retry";
      saveBtn.className = "btn error";
      saveBtn.disabled = false;
      formError.textContent = err.message;
    }
  });

  // --- Init ---

  checkFragmentToken();
  if (getToken()) { showForm(); }
  else { showSetup(); }

  // Register service worker for PWA installability
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function() {});
  }
})();
</script>
</body>
</html>`;
```

- [ ] **Step 2: Verify the module exports work**

Run from the worker directory:

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest/worker
node -e "const p = require('./src/pwa.js'); console.log(typeof p.serveSavePage, typeof p.serveManifest, typeof p.serveServiceWorker)"
```

This will fail because the file uses ES module exports. Instead verify syntax:

```bash
node --check src/pwa.js
```

Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add worker/src/pwa.js
git commit -m "feat: add PWA share target page module"
```

---

### Task 2: Wire Worker routes and update wrangler.toml

**Files:**
- Modify: `worker/src/index.js:1-59`
- Modify: `worker/wrangler.toml:5-9`

- [ ] **Step 1: Add import and route handlers to `worker/src/index.js`**

At the top of the file (line 1), add the import:

```javascript
import { serveSavePage, serveManifest, serveServiceWorker } from "./pwa.js";
```

Inside the `fetch` handler, add three new route checks **before** the final R2 fallthrough (before line 57 `// Everything else`). Insert after the heartbeat handler (after line 55):

```javascript
    // --- PWA share target ---

    if (url.pathname === "/save" && request.method === "GET") {
      return serveSavePage();
    }

    if (url.pathname === "/manifest.json" && request.method === "GET") {
      return serveManifest();
    }

    if (url.pathname === "/sw.js" && request.method === "GET") {
      return serveServiceWorker();
    }
```

- [ ] **Step 2: Update `worker/wrangler.toml` route pattern**

Change the routes section from:

```toml
# Route: handle /api/* on the existing share domain.
# All other paths fall through to R2 (media archive).
routes = [
  { pattern = "share.bymarkriechers.com/api/*", zone_name = "bymarkriechers.com" }
]
```

To:

```toml
# Route: handle all paths on the share domain.
# /api/* = ingest API, /save = PWA, /manifest.json + /sw.js = PWA support.
# Unmatched paths fall through to R2 (media archive) via fetch(request).
routes = [
  { pattern = "share.bymarkriechers.com/*", zone_name = "bymarkriechers.com" }
]
```

- [ ] **Step 3: Verify syntax**

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest/worker
node --check src/index.js
```

Expected: no output (syntax OK)

- [ ] **Step 4: Commit**

```bash
git add worker/src/index.js worker/wrangler.toml
git commit -m "feat: wire PWA routes in Worker and widen route pattern"
```

---

### Task 3: Deploy and test

**Files:** None (operational)

- [ ] **Step 1: Deploy the Worker**

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest/worker
npx wrangler deploy
```

Expected: successful deployment to `share.bymarkriechers.com`

- [ ] **Step 2: Verify R2 fallthrough still works**

Media archive URLs should still resolve. Test with a known archived media file:

```bash
curl -sI "https://share.bymarkriechers.com/2026/04/anthropic-releases-advisor-mode.mp4" | head -5
```

Expected: `HTTP/2 200` with `content-type: video/mp4` (or a redirect to R2)

- [ ] **Step 3: Verify the PWA page loads**

```bash
curl -s "https://share.bymarkriechers.com/save" | head -5
```

Expected: `<!DOCTYPE html>` followed by the PWA HTML

- [ ] **Step 4: Verify the manifest**

```bash
curl -s "https://share.bymarkriechers.com/manifest.json" | python3 -m json.tool
```

Expected: JSON with `name: "Crow's Nest"`, `share_target` with `action: "/save"`

- [ ] **Step 5: Verify the service worker**

```bash
curl -s "https://share.bymarkriechers.com/sw.js"
```

Expected: `self.addEventListener("fetch", () => {});`

- [ ] **Step 6: Test share target pre-fill**

```bash
curl -s "https://share.bymarkriechers.com/save?url=https://example.com&context=test" | grep -o 'value="[^"]*"' | head -2
```

Expected: the URL and context should appear in the HTML (they're filled by JS, so this won't show in curl — verify in a browser instead by opening: `https://share.bymarkriechers.com/save?url=https://example.com&context=test`)

- [ ] **Step 7: Test authenticated submit**

```bash
TOKEN=$(security find-generic-password -s "developer.workspace.CROWS_NEST_INGEST_TOKEN" -w)
curl -s "https://share.bymarkriechers.com/api/ingest" \
  -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"url":"https://example.com/pwa-deploy-test","source":"pwa"}'
```

Expected: `{"id":N,"status":"queued","url":"https://example.com/pwa-deploy-test"}`

Then clean up the test entry:

```bash
# Via D1 MCP tool or wrangler d1 execute:
cd /Users/mriechers/Developer/second-brain/crows-nest/worker
npx wrangler d1 execute crows-nest-ingest --command "DELETE FROM ingest_queue WHERE url = 'https://example.com/pwa-deploy-test'"
```

- [ ] **Step 8: Commit deploy confirmation**

No code change needed. If all tests pass, the deployment is confirmed.

---

### Task 4: Mobile install and end-to-end test

**Files:** None (manual testing on phone)

- [ ] **Step 1: Generate the token install URL**

On your Mac, run:

```bash
TOKEN=$(security find-generic-password -s "developer.workspace.CROWS_NEST_INGEST_TOKEN" -w)
echo "https://share.bymarkriechers.com/save#token=$TOKEN"
```

Open that URL in a browser tab. You can also generate a QR code:

```bash
# If qrencode is installed (brew install qrencode):
qrencode -t UTF8 "https://share.bymarkriechers.com/save#token=$TOKEN"
```

- [ ] **Step 2: On iPhone — scan QR or open URL**

Open the URL in Safari. The page should briefly show the setup view, auto-detect the `#token=` fragment, store the token, and switch to the empty form view.

- [ ] **Step 3: On iPhone — Add to Home Screen**

In Safari: Share button → Add to Home Screen → Add. This registers the PWA as a share target.

- [ ] **Step 4: Test the share target**

Open Safari, navigate to any page. Tap Share → look for "Crow's Nest" in the share sheet. Tap it. The PWA should open with the URL pre-filled. Tap Save. Verify "Saved" confirmation.

- [ ] **Step 5: Verify the link arrived in D1**

Back on the Mac:

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest/worker
npx wrangler d1 execute crows-nest-ingest --command "SELECT id, url, source, synced FROM ingest_queue ORDER BY id DESC LIMIT 3"
```

Expected: the test URL appears with `source = "pwa"` and `synced = 0`.

- [ ] **Step 6: Verify the poller picks it up**

Wait for the next ingest-poll scheduler run (every 5 minutes), or trigger it manually:

```bash
curl -s -X POST "http://127.0.0.1:27185/jobs/ingest-poll/run"
```

Then check the local pipeline DB:

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('data/crows-nest.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT id, url, source_type, status FROM links ORDER BY id DESC LIMIT 3')
for row in c.fetchall(): print(dict(row))
"
```

Expected: the test URL appears with `source_type = "ingest-api"` and `status = "pending"`.
