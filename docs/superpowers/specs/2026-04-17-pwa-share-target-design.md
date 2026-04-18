# PWA Share Target for Crow's Nest Pipeline

## Goal

Replace the broken iOS Shortcut with a PWA share target at `share.bymarkriechers.com/save` that receives URLs from the iOS Share Sheet, submits them to the existing `/api/ingest` Worker endpoint, and provides clear success/failure feedback.

## Architecture

The PWA is a single HTML page served inline from the Cloudflare Worker. No R2 files, no build step. Three new routes are added to the existing Worker (`worker/src/index.js`):

- `GET /save` — the PWA HTML
- `GET /manifest.json` — Web App Manifest with `share_target` declaration
- `GET /sw.js` — minimal service worker (required for PWA installability)

The Worker already handles `/api/ingest` (POST, auth via `INGEST_TOKEN`). The PWA calls this same endpoint on the same origin — no CORS needed.

## Token Provisioning

Token is stored in `localStorage`. Two provisioning methods:

1. **QR code install** (recommended) — Visit `/save` on desktop, which shows a QR code encoding `https://share.bymarkriechers.com/save#token=<TOKEN>`. Scan with phone → Safari opens → PWA reads token from URL fragment, stores in localStorage, switches to form mode.

2. **Manual paste** — If no `#token=` fragment, the setup screen shows a field to paste the token directly.

The token is read from the Keychain-stored `INGEST_TOKEN` secret. The URL fragment (`#`) never leaves the browser — it's not sent to the server.

The token is sent as a bare value in the `Authorization` header. The Worker's `authenticate()` function already handles both bare tokens and `Bearer`-prefixed tokens via regex stripping.

## PWA Manifest & Share Target

`manifest.json` declares:

```json
{
  "name": "Crow's Nest",
  "short_name": "Crow's Nest",
  "start_url": "/save",
  "display": "standalone",
  "background_color": "#1a1a2e",
  "theme_color": "#1a1a2e",
  "share_target": {
    "action": "/save",
    "method": "GET",
    "params": {
      "url": "url",
      "title": "context"
    }
  }
}
```

When the user shares from Safari/apps, iOS opens `/save?url=<URL>&context=<page-title>`. The form reads these query params and pre-fills the fields.

The service worker (`/sw.js`) is a no-op — it just registers to satisfy the PWA installability requirement. No offline caching.

## UI States

### Setup (shown once)

- "Crow's Nest" heading
- Brief instruction
- Token input field + "Connect" button
- If `#token=...` is in the URL, auto-stores and skips to form mode

### Form (primary view)

- URL field — pre-filled from `?url=` query param, or empty for manual paste
- Context field (optional) — pre-filled from `?title=` query param
- "Save" button
- Gear icon in corner → clears token, returns to setup

### Submitting

- Button text changes to "Saving...", disabled
- No spinner — requests take <1 second

### Success

- Button turns green, shows "Saved"
- Fields clear after 1.5 seconds
- Ready for another submission

### Error

- Red message below button with actual error text (e.g. "unauthorized", "network error")
- Button becomes "Retry"
- Fully debuggable — no silent failures

## Visual Style

Matches existing bookmarklet page: dark theme (`#1a1a2e` background, `#16213e` card, `#e94560` accent), system font stack, centered card layout. Mobile-first sizing. Proof-of-concept level — a UX polish pass is tracked in mriechers/second-brain#61.

## Dual Mode

The PWA works in two modes with no code distinction:

1. **Share Target** — URL and context pre-filled from query params
2. **Standalone** — Open `/save` directly, paste a URL manually

## Offline Behavior

No offline queue. If the POST fails (network error, auth error), the PWA shows a clear error message with a retry button. Offline queuing via IndexedDB + service worker sync is a potential future enhancement.

## Worker Changes

Add three route handlers to `worker/src/index.js`:

1. `GET /save` — returns inline HTML string with the PWA page
2. `GET /manifest.json` — returns the manifest JSON
3. `GET /sw.js` — returns a minimal service worker (`self.addEventListener('fetch', () => {})`)

The Worker's existing `wrangler.toml` routes on `share.bymarkriechers.com/api/*`. This pattern needs to be updated to include the new non-API paths. Either:
- Change to `share.bymarkriechers.com/*` (catch-all, with explicit R2 fallthrough for unknown paths)
- Add specific routes: `/save`, `/manifest.json`, `/sw.js`

The catch-all approach is simpler since the Worker already falls through to R2 via `return fetch(request)` for unmatched paths.

## Files Changed

| Action | File | Purpose |
|--------|------|---------|
| Modify | `worker/src/index.js` | Add `/save`, `/manifest.json`, `/sw.js` route handlers |
| Modify | `worker/wrangler.toml` | Update route pattern to catch non-API paths |

No new files — everything is inline in the Worker.

## Testing

- Manual: deploy to Cloudflare, visit `/save` on desktop and mobile
- Verify share target works after "Add to Home Screen" on iOS
- Verify token provisioning via QR code scan
- Verify success/error feedback on submit
- Verify fallthrough to R2 still works for media archive URLs

## Out of Scope

- Offline queuing (tracked as future enhancement)
- Chrome extension / bookmarklet refactoring (they work independently)
- Visual design polish (tracked in mriechers/second-brain#61)
- iOS Shortcut debugging (tracked in mriechers/crows-nest#93)
