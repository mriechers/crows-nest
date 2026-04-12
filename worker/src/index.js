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

    if (url.pathname === "/api/jobs" && request.method === "POST") {
      return handleJobCreate(request, env);
    }

    if (url.pathname === "/api/jobs/claim" && request.method === "POST") {
      return handleJobClaim(request, env);
    }

    if (url.pathname === "/api/jobs/complete" && request.method === "POST") {
      return handleJobComplete(request, env);
    }

    if (url.pathname === "/api/jobs/pending" && request.method === "GET") {
      return handleJobsPending(request, env);
    }

    if (url.pathname === "/api/nodes/heartbeat" && request.method === "POST") {
      return handleHeartbeat(request, env);
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
    console.error("queue insert failed:", err.message);
    return Response.json(
      { error: "queue insert failed" },
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

async function handleJobCreate(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const type = (body.type || "").trim();
  if (!type) {
    return Response.json({ error: "type is required" }, { status: 400 });
  }

  if (body.id && !/^[a-zA-Z0-9_-]{1,128}$/.test(body.id)) {
    return Response.json({ error: "invalid id format" }, { status: 400 });
  }
  const id = body.id || crypto.randomUUID();
  const payload = JSON.stringify(body.payload || {});
  const priority = Number.isInteger(body.priority) ? body.priority : 0;
  const now = new Date().toISOString();

  try {
    await env.DB.prepare(
      "INSERT INTO jobs (id, type, payload, status, priority, created_at) VALUES (?, ?, ?, 'pending', ?, ?)"
    )
      .bind(id, type, payload, priority, now)
      .run();

    return Response.json({ id, status: "pending", type });
  } catch (err) {
    console.error("job insert failed:", err.message);
    return Response.json(
      { error: "job insert failed" },
      { status: 500 }
    );
  }
}

async function handleJobsPending(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const params = new URL(request.url).searchParams;
  const type = params.get("type");
  const limit = Math.min(parseInt(params.get("limit") || "20", 10), 100);

  let sql = "SELECT * FROM jobs WHERE status = 'pending'";
  const binds = [];

  if (type) {
    sql += " AND type = ?";
    binds.push(type);
  }

  sql += " ORDER BY priority DESC, created_at ASC LIMIT ?";
  binds.push(limit);

  try {
    const stmt = env.DB.prepare(sql);
    const { results } = await stmt.bind(...binds).all();
    return Response.json({ jobs: results });
  } catch (err) {
    console.error("jobs pending query failed:", err.message);
    return Response.json({ error: "query failed" }, { status: 500 });
  }
}

async function handleJobClaim(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const jobId = (body.job_id || "").trim();
  const nodeId = (body.node_id || "").trim();
  if (!jobId || !nodeId) {
    return Response.json({ error: "job_id and node_id are required" }, { status: 400 });
  }

  const now = new Date().toISOString();

  try {
    const result = await env.DB.prepare(
      "UPDATE jobs SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ? AND status = 'pending'"
    )
      .bind(nodeId, now, jobId)
      .run();

    if (result.meta.changes === 0) {
      return Response.json({ error: "job not available" }, { status: 409 });
    }

    return Response.json({ job_id: jobId, claimed_by: nodeId, status: "claimed" });
  } catch (err) {
    console.error("job claim failed:", err.message);
    return Response.json({ error: "claim failed" }, { status: 500 });
  }
}

async function handleJobComplete(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const jobId = (body.job_id || "").trim();
  const status = body.status === "failed" ? "failed" : "done";
  const error = body.error || null;
  const now = new Date().toISOString();

  if (!jobId) {
    return Response.json({ error: "job_id is required" }, { status: 400 });
  }

  try {
    const result = await env.DB.prepare(
      "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ? AND status = 'claimed'"
    )
      .bind(status, now, error, jobId)
      .run();

    if (result.meta.changes === 0) {
      return Response.json({ error: "job not found or not in claimed state" }, { status: 409 });
    }

    return Response.json({ job_id: jobId, status });
  } catch (err) {
    console.error("job complete failed:", err.message);
    return Response.json({ error: "complete failed" }, { status: 500 });
  }
}

async function handleHeartbeat(request, env) {
  if (!authenticate(request, env)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const nodeId = (body.node_id || "").trim();
  if (!nodeId) {
    return Response.json({ error: "node_id is required" }, { status: 400 });
  }

  const hostname = body.hostname || "";
  const capabilities = JSON.stringify(body.capabilities || []);
  const currentJob = body.current_job || null;
  const now = new Date().toISOString();

  try {
    await env.DB.prepare(
      `INSERT INTO nodes (node_id, hostname, capabilities, last_heartbeat, current_job)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(node_id) DO UPDATE SET
         hostname = excluded.hostname,
         capabilities = excluded.capabilities,
         last_heartbeat = excluded.last_heartbeat,
         current_job = excluded.current_job`
    )
      .bind(nodeId, hostname, capabilities, now, currentJob)
      .run();

    return Response.json({ node_id: nodeId, last_heartbeat: now });
  } catch (err) {
    console.error("heartbeat failed:", err.message);
    return Response.json({ error: "heartbeat failed" }, { status: 500 });
  }
}
