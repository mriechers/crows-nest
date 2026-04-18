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
