# Realtalk Web Embed — Frontend Interface Spec

Handoff document for integrating the Realtalk terminal into the conle.ai site.
The backend (FastAPI + PTY over WebSocket) and Cloud Run infra live in this
repo under `web/server/` and `web/infra/`. The frontend work is to embed an
xterm.js terminal in the marketing site that connects to these services.

## Deployed services

Two Cloud Run services, fronted by distinct hostnames.

| Service | Hostname (prod) | Purpose |
|---------|-----------------|---------|
| `realtalk-api` | `https://api.realtalk.conle.ai` | Mints session tokens. Stateless. Concurrency=80. |
| `realtalk-ws`  | `wss://ws.realtalk.conle.ai`    | Hosts a PTY running the `realtalk` CLI. Concurrency=1. Max 10 active globally. |

Both expose `GET /_health` returning `{"status": "ok"}`.

## End-to-end flow

```
[browser]                 [realtalk-api]              [realtalk-ws]
   │                            │                          │
   │ 1. POST /session  ────────▶│                          │
   │    (Origin header)         │ check origin, rate-limit,│
   │                            │ capacity. Acquire slot.  │
   │                            │ Mint HMAC token (5m TTL).│
   │◀───── 200 {token, ws_url} ─│                          │
   │                            │                          │
   │ 2. WebSocket(ws_url)  ────────────────────────────────▶│
   │                                      verify token, spawn PTY
   │◀────────────────────── {type: "ready", cols, rows} ───│
   │                                                       │
   │ 3. User types:                                        │
   │     {type: "input", data: "hi\r"}  ──────────────────▶│  (writes to PTY stdin)
   │◀──── {type: "output", data: "hi\r\n..."} ─────────────│  (reads PTY stdout)
   │                                                       │
   │ 4. Terminal resize:                                   │
   │     {type: "resize", cols, rows}  ────────────────────▶│
   │                                                       │
   │ 5. Session ends (CLI exits or timeout):               │
   │◀─── {type: "exit", code: 0}  or  {type: "error", ...} │
   │      server closes with CLOSE_NORMAL / CLOSE_SESSION_TIMEOUT / CLOSE_POLICY
```

Slot is released when the WebSocket closes (either side). The token is only
valid for the one socket it is consumed on.

---

## HTTP: `POST /session`

Mint a short-lived token and reserve a capacity slot.

**Request**

```
POST https://api.realtalk.conle.ai/session
Origin: https://conle.ai
Content-Type: application/json

{}   # body is currently ignored; send {} for forward compatibility
```

**Response — 200**

```json
{
  "session_id": "s_abc123...",
  "token": "eyJzaWQiOiJzXzEyMyIsImV4cCI6MTczMDAwMDAwMH0.AbCd...",
  "ws_url": "wss://ws.realtalk.conle.ai/ws?token=eyJ...",
  "expires_in_s": 300
}
```

Use `ws_url` verbatim. The token embedded in it is single-use.

**Error responses**

| HTTP | `error` | Meaning | How the UI should react |
|------|---------|---------|-------------------------|
| 403  | `forbidden_origin` | `Origin` header not in allow-list | Should not happen in prod. Surface a generic "unavailable" state and log. |
| 429  | `rate_limited` | Too many sessions from this IP | Show "Too many sessions — try again in a few minutes." |
| 503  | `at_capacity` | All 10 global slots in use | Show waitlist copy. Retry after `retry_after_s` seconds (default 30). |

Error body shape:

```json
{ "error": "at_capacity", "message": "all game slots in use", "retry_after_s": 30 }
```

**CORS.** The API allows `POST /session` from the origins configured in
`REALTALK_ALLOWED_ORIGINS` on the Cloud Run service (prod: `https://conle.ai`,
`https://www.conle.ai`; add preview domains as needed).

---

## WebSocket: `wss://ws.realtalk.conle.ai/ws?token=…`

All frames are JSON text frames (no binary). Each frame has a `type`
discriminator.

### Client → server frames

```ts
type ClientFrame =
  | { type: "input"; data: string }                           // keystrokes; append to PTY stdin
  | { type: "resize"; cols: number; rows: number };           // cols 20..300, rows 10..100
```

`data` is a UTF-8 string. Send control characters as literal bytes (e.g.
`"\r"` for Enter, `"\u0003"` for Ctrl-C). xterm.js `onData` already produces
this shape — just wrap it.

### Server → client frames

```ts
type ServerFrame =
  | { type: "ready"; cols: number; rows: number }             // PTY spawned; you may start sending input
  | { type: "output"; data: string }                          // UTF-8 PTY stdout/stderr; write to xterm
  | { type: "exit"; code: number }                            // CLI exited; socket will close next
  | { type: "error"; code: ErrorCode; message: string };      // fatal; socket will close next
```

### Error codes (in `ErrorFrame.code`)

| Code | Meaning |
|------|---------|
| `invalid_token` | Token missing, malformed, expired, or bad signature. Request a new one from `/session`. |
| `invalid_frame` | A client frame failed validation. The socket is dropped; reconnect. |
| `session_timeout` | Idle > 5 min or total > 20 min. Show "Session ended — reload to play again." |
| `pty_spawn_failed` | Server couldn't start the CLI. Treat as transient; retry after ~10s. |
| `internal` | Unexpected server error. Retry after ~10s. |

### Close codes

The server may also close without an ErrorFrame (e.g. network cut). Close
codes:

| Code | Meaning |
|------|---------|
| 1000 (Normal) | CLI exited cleanly (`exit` frame already sent). |
| 1008 (Policy) | Token invalid or origin disallowed. Do not retry with the same token. |
| 1011 (Internal) | Server error. Retry allowed. |
| 4000 (Custom)  | Session timeout. Show timeout copy. |

---

## Sizing

The server needs PTY dimensions before it forks. Send them **before** any
input, and again on every terminal resize:

1. Optional: a `resize` frame before connecting would be ideal, but since the
   URL carries the token, send a `resize` immediately after the `ready` frame
   if your xterm cols/rows differ from the server defaults (80×24).
2. On every `fit.fit()` in xterm (window resize, container resize), send a
   `resize` frame with the new cols/rows.

---

## Reference implementation (React + xterm.js)

This is a working sketch. Copy into `packages/site/components/RealtalkTerminal.tsx`
(or wherever marketing components live).

```tsx
"use client";

import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";

type SessionResponse = {
  session_id: string;
  token: string;
  ws_url: string;
  expires_in_s: number;
};

type ServerFrame =
  | { type: "ready"; cols: number; rows: number }
  | { type: "output"; data: string }
  | { type: "exit"; code: number }
  | { type: "error"; code: string; message: string };

const API_BASE = process.env.NEXT_PUBLIC_REALTALK_API ?? "https://api.realtalk.conle.ai";

type UIState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "running" }
  | { kind: "ended"; reason: string }
  | { kind: "waitlist"; retryInS: number }
  | { kind: "error"; message: string };

export function RealtalkTerminal() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<UIState>({ kind: "idle" });

  useEffect(() => {
    if (!containerRef.current || state.kind !== "connecting") return;

    const term = new Terminal({ fontFamily: "JetBrains Mono, monospace", fontSize: 14 });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();

    let ws: WebSocket | null = null;
    let cancelled = false;

    (async () => {
      const res = await fetch(`${API_BASE}/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ error: "unknown", message: "" }));
        if (res.status === 503 && body.error === "at_capacity") {
          setState({ kind: "waitlist", retryInS: body.retry_after_s ?? 30 });
        } else if (res.status === 429) {
          setState({ kind: "error", message: "Too many sessions. Try again soon." });
        } else {
          setState({ kind: "error", message: body.message ?? "Could not start a session." });
        }
        return;
      }
      const session: SessionResponse = await res.json();
      if (cancelled) return;

      ws = new WebSocket(session.ws_url);
      ws.onopen = () => {
        // send initial resize to match the rendered terminal
        ws!.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      };
      ws.onmessage = (ev) => {
        let frame: ServerFrame;
        try { frame = JSON.parse(ev.data); } catch { return; }
        switch (frame.type) {
          case "ready": setState({ kind: "running" }); break;
          case "output": term.write(frame.data); break;
          case "exit": setState({ kind: "ended", reason: `Session ended (exit ${frame.code}).` }); break;
          case "error": setState({ kind: "error", message: frame.message }); break;
        }
      };
      ws.onclose = (ev) => {
        if (ev.code === 4000) setState({ kind: "ended", reason: "Session timed out." });
        else setState((s) => (s.kind === "running" ? { kind: "ended", reason: "Disconnected." } : s));
      };

      term.onData((data) => ws?.send(JSON.stringify({ type: "input", data })));

      const ro = new ResizeObserver(() => {
        fit.fit();
        ws?.readyState === WebSocket.OPEN &&
          ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      });
      ro.observe(containerRef.current!);
      return () => ro.disconnect();
    })();

    return () => {
      cancelled = true;
      ws?.close();
      term.dispose();
    };
  }, [state.kind]);

  if (state.kind === "idle") {
    return <button onClick={() => setState({ kind: "connecting" })}>Launch realtalk</button>;
  }
  if (state.kind === "waitlist") {
    return <p>All slots full. Retrying in {state.retryInS}s…</p>;
  }
  if (state.kind === "error") {
    return <p>{state.message}</p>;
  }
  return <div ref={containerRef} style={{ width: "100%", height: 480 }} />;
}
```

Dependencies: `@xterm/xterm`, `@xterm/addon-fit`.

---

## Environment variables (frontend)

| Name | Default | Notes |
|------|---------|-------|
| `NEXT_PUBLIC_REALTALK_API` | `https://api.realtalk.conle.ai` | Base URL of `realtalk-api`. Override for preview/staging. |

The WebSocket URL comes from the `/session` response — do not hard-code it.

---

## Origin allow-list

`realtalk-api` checks the `Origin` header on `POST /session`. The allow-list
is set via the `REALTALK_ALLOWED_ORIGINS` env var on the Cloud Run service
(comma-separated). Adding a new preview domain is a backend config change —
coordinate with the infra owner.

---

## Things to know

- **Capacity**: global cap is 10 concurrent sessions. Above that, `/session`
  returns 503 `at_capacity`. Show waitlist copy, don't retry aggressively.
- **Per-IP rate limit**: 5 sessions per hour per IP. Rare in practice — surface
  generic "try again later" copy on 429.
- **Timeouts**: 5 min idle, 20 min hard cap. The server will close with
  code 4000 and an `error` frame with `code: "session_timeout"`.
- **One socket per token**: if the WebSocket drops, call `POST /session` again
  to get a fresh token. Tokens are single-use.
- **No binary frames**: all PTY bytes are UTF-8-decoded server-side and sent
  as JSON strings. xterm handles the terminal escape sequences verbatim.
- **No reconnect across page loads**: the PTY is ephemeral. Closing the tab
  kills the game.

---

## Quick smoke test from the browser console

Once deployed, you can sanity-check the backend without the UI:

```js
const r = await fetch("https://api.realtalk.conle.ai/session", {
  method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"
});
const s = await r.json();
const ws = new WebSocket(s.ws_url);
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.onopen = () => ws.send(JSON.stringify({type:"resize",cols:80,rows:24}));
// after `ready`:
// ws.send(JSON.stringify({type:"input",data:"\r"}));
```

---

## Contacts / source of truth

- **Protocol types**: `web/server/realtalk_web/protocol.py` (pydantic models — regenerate TS types from here if desired)
- **API handler**: `web/server/realtalk_web/api_main.py`
- **WS handler**: `web/server/realtalk_web/ws_main.py`
- **Infra**: `web/infra/*.tf`
- **Overall spec**: `docs/spec/web-embed.md`
