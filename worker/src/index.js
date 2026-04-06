// Ingest endpoint for the Crow's Nest pipeline.
// Accepts URLs from iOS Shortcuts, browser extensions, etc.
// Writes to D1 queue; local poller drains into pipeline DB.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/health" && request.method === "GET") {
      return Response.json({ status: "ok", service: "crows-nest-ingest" });
    }

    if (url.pathname === "/api/ingest" && request.method === "POST") {
      return handleIngest(request, env);
    }

    if (url.pathname === "/api/pending" && request.method === "GET") {
      return handlePending(request, env);
    }

    if (url.pathname === "/api/mark-synced" && request.method === "POST") {
      return handleMarkSynced(request, env);
    }

    // Everything else: not handled by this Worker (falls through to R2)
    return fetch(request);
  },
};

function authenticate(request, env) {
  const header = request.headers.get("Authorization") || "";
  const token = header.replace(/^Bearer\s+/i, "");
  return token && token === env.INGEST_TOKEN;
}

async function handleIngest(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const linkUrl = (body.url || "").trim();
  if (!linkUrl) {
    return Response.json({ error: "url is required" }, { status: 400 });
  }

  try {
    new URL(linkUrl);
  } catch {
    return Response.json({ error: "invalid url" }, { status: 400 });
  }

  const context = (body.context || "").trim() || null;
  const source = (body.source || "shortcut").trim();
  const now = new Date().toISOString();

  try {
    const result = await env.DB.prepare(
      "INSERT INTO ingest_queue (url, context, source, created_at) VALUES (?, ?, ?, ?)"
    )
      .bind(linkUrl, context, source, now)
      .run();

    return Response.json({
      id: result.meta.last_row_id,
      status: "queued",
      url: linkUrl,
    });
  } catch (err) {
    return Response.json(
      { error: "queue insert failed", detail: err.message },
      { status: 500 }
    );
  }
}

async function handlePending(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const limit = Math.min(
    parseInt(new URL(request.url).searchParams.get("limit") || "50", 10),
    200
  );

  const { results } = await env.DB.prepare(
    "SELECT id, url, context, source, created_at FROM ingest_queue WHERE synced = 0 ORDER BY id ASC LIMIT ?"
  )
    .bind(limit)
    .all();

  return Response.json({ items: results });
}

async function handleMarkSynced(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const ids = body.ids;
  if (!Array.isArray(ids) || ids.length === 0) {
    return Response.json({ error: "ids array is required" }, { status: 400 });
  }

  const statements = ids.map((id) =>
    env.DB.prepare("UPDATE ingest_queue SET synced = 1 WHERE id = ?").bind(id)
  );

  await env.DB.batch(statements);
  return Response.json({ synced: ids.length });
}
