#!/usr/bin/env python3
"""
Calculator MCP Server with Proper OAuth 2.0 Authorization Flow
Supports:
  - Dynamic Client Registration (RFC 7591) — VS Code auto-registers
  - Authorization Code Flow (RFC 6749)
  - OAuth Metadata Discovery (RFC 8414)
  - No manual token/session handling for users
"""

import logging
import os
import json
import secrets
import time
from urllib.parse import urlencode
from typing import Optional
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
import requests as http_requests
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
BASE_URL             = os.getenv("BASE_URL", "http://localhost:8000")
REDIRECT_URI         = f"{BASE_URL}/oauth/callback"

# ─────────────────────────────────────────────
# In-memory stores
# ─────────────────────────────────────────────
sessions: dict = {}          # session_id -> { access_token, email, created_at }
pending_states: dict = {}    # google_state -> { client_redirect_uri, client_state, created_at }
auth_codes: dict = {}        # auth_code -> { session_id, created_at }
mcp_tokens: dict = {}        # mcp_token -> session_id

# Dynamic client registry — pre-seed VS Code known redirect URIs
registered_clients: dict = {
    "vscode-mcp": {
        "client_id":     "vscode-mcp",
        "client_secret": None,
        "redirect_uris": [
            "http://127.0.0.1:33418",
            "https://vscode.dev/redirect",
        ],
        "client_name": "VS Code MCP",
    }
}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def cleanup_old_entries():
    now = time.time()
    for s in [k for k, v in pending_states.items() if now - v["created_at"] > 600]:
        pending_states.pop(s, None)
    for c in [k for k, v in auth_codes.items() if now - v["created_at"] > 600]:
        auth_codes.pop(c, None)


# ─────────────────────────────────────────────
# MCP Server & Tools
# ─────────────────────────────────────────────
mcp = FastMCP("calculator")


@mcp.tool()
async def add(a: float, b: float) -> str:
    """Add two numbers together."""
    return json.dumps({"result": a + b, "operation": f"{a} + {b}"})


@mcp.tool()
async def subtract(a: float, b: float) -> str:
    """Subtract b from a."""
    return json.dumps({"result": a - b, "operation": f"{a} - {b}"})


@mcp.tool()
async def multiply(a: float, b: float) -> str:
    """Multiply two numbers."""
    return json.dumps({"result": a * b, "operation": f"{a} x {b}"})


@mcp.tool()
async def divide(a: float, b: float) -> str:
    """Divide a by b."""
    if b == 0:
        return json.dumps({"error": "Division by zero is not allowed."})
    return json.dumps({"result": a / b, "operation": f"{a} / {b}"})


@mcp.tool()
async def power(a: float, b: float) -> str:
    """Raise a to the power of b."""
    return json.dumps({"result": a ** b, "operation": f"{a} ^ {b}"})


# ─────────────────────────────────────────────
# OAuth Discovery & Registration
# ─────────────────────────────────────────────
async def oauth_metadata(request: Request):
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414).
    MCP clients fetch /.well-known/oauth-authorization-server to discover all endpoints.
    """
    return JSONResponse({
        "issuer":                                  BASE_URL,
        "authorization_endpoint":                 f"{BASE_URL}/oauth/authorize",
        "token_endpoint":                          f"{BASE_URL}/oauth/token",
        "registration_endpoint":                   f"{BASE_URL}/oauth/register",
        "response_types_supported":               ["code"],
        "grant_types_supported":                  ["authorization_code"],
        "code_challenge_methods_supported":        ["S256", "plain"],
        "token_endpoint_auth_methods_supported":   ["none"],
        "scopes_supported":                        ["openid", "email", "profile"],
    })


async def oauth_register(request: Request):
    """
    Dynamic Client Registration (RFC 7591).
    VS Code and other MCP clients POST here to register themselves automatically.
    This is what eliminates the 'manually provide a client registration' prompt.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "Expected JSON body"},
            status_code=400,
        )

    redirect_uris = body.get("redirect_uris", [])
    client_name   = body.get("client_name", "MCP Client")
    client_id     = secrets.token_urlsafe(16)

    registered_clients[client_id] = {
        "client_id":     client_id,
        "client_secret": None,
        "redirect_uris": redirect_uris,
        "client_name":   client_name,
    }

    logger.info(f"Registered client '{client_name}' ({client_id}) -> {redirect_uris}")

    return JSONResponse({
        "client_id":                  client_id,
        "client_name":                client_name,
        "redirect_uris":              redirect_uris,
        "grant_types":                ["authorization_code"],
        "response_types":             ["code"],
        "token_endpoint_auth_method": "none",
    }, status_code=201)


# ─────────────────────────────────────────────
# OAuth Authorization Flow
# ─────────────────────────────────────────────
async def oauth_authorize(request: Request):
    """
    Authorization endpoint.
    MCP client redirects user here -> we redirect to Google -> Google returns to /oauth/callback.
    """
    cleanup_old_entries()

    client_redirect = request.query_params.get("redirect_uri", "")
    client_state    = request.query_params.get("state", "")

    google_state = secrets.token_urlsafe(32)
    pending_states[google_state] = {
        "client_redirect_uri": client_redirect,
        "client_state":        client_state,
        "created_at":          time.time(),
    }

    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         google_state,
        "access_type":   "online",
        "prompt":        "select_account",
    }

    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


async def oauth_callback(request: Request):
    """
    Google redirects here after sign-in.
    - MCP client flow: issue auth_code, redirect back to client's redirect_uri.
    - Direct browser visit: show success page with ready-to-use bearer token.
    """
    code  = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(f"<h2>Sign-in cancelled: {error}</h2>", status_code=400)

    if not code or state not in pending_states:
        return HTMLResponse("<h2>Invalid or expired request. Please try again.</h2>", status_code=400)

    state_data = pending_states.pop(state)

    # Exchange Google code for Google access token
    try:
        token_resp = http_requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
            timeout=10,
        )
        token_data = token_resp.json()
        if "error" in token_data:
            raise ValueError(token_data.get("error_description", token_data["error"]))

        access_token = token_data["access_token"]

        user_resp  = http_requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_email = user_resp.json().get("email", "unknown")

    except Exception as exc:
        logger.error(f"Token exchange error: {exc}")
        return HTMLResponse(f"<h2>Authentication error: {exc}</h2>", status_code=500)

    # Create server-side session
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "session_id":   session_id,
        "access_token": access_token,
        "email":        user_email,
        "created_at":   time.time(),
    }

    client_redirect = state_data.get("client_redirect_uri", "")
    client_state    = state_data.get("client_state", "")

    # ── MCP client initiated the flow ──
    if client_redirect:
        # Generate MCP token directly for VS Code
        mcp_token = secrets.token_urlsafe(32)
        mcp_tokens[mcp_token] = session_id
        
        # Create auth code for OAuth flow
        auth_code = secrets.token_urlsafe(32)
        auth_codes[auth_code] = {"session_id": session_id, "created_at": time.time()}

        sep = "&" if "?" in client_redirect else "?"
        redirect_url = f"{client_redirect}{sep}code={auth_code}"
        if client_state:
            redirect_url += f"&state={client_state}"

        logger.info(f"Redirecting {user_email} back to MCP client with auth code")
        return RedirectResponse(redirect_url)

    # ── Direct browser visit ──
    mcp_token = secrets.token_urlsafe(32)
    mcp_tokens[mcp_token] = session_id
    sse_url = f"{BASE_URL}/sse"

    config = json.dumps({
        "mcpServers": {
            "calculator": {
                "url": sse_url,
                "headers": {"Authorization": f"Bearer {mcp_token}"}
            }
        }
    }, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Signed In - Calculator MCP</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0f; --surface: #13131a; --border: #1e1e2e;
      --accent: #7c6af7; --accent2: #4fc3f7; --text: #e8e8f0;
      --muted: #6b6b80; --success: #43e97b;
    }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: 'DM Sans', sans-serif;
      min-height: 100vh; display: flex;
      align-items: center; justify-content: center; padding: 2rem;
    }}
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 20px; padding: 3rem 2.5rem;
      max-width: 520px; width: 100%; text-align: center;
    }}
    .badge {{
      display: inline-block; background: rgba(67,233,123,0.12);
      color: var(--success); border: 1px solid rgba(67,233,123,0.3);
      border-radius: 20px; padding: 0.3rem 1rem;
      font-size: 0.8rem; font-weight: 600; margin-bottom: 1.2rem;
    }}
    h1 {{ font-size: 1.7rem; font-weight: 600; margin-bottom: 0.4rem; }}
    .email {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
    .section {{ text-align: left; margin-bottom: 1.5rem; }}
    .label {{ font-size: 0.78rem; color: var(--muted); margin-bottom: 0.4rem;
              font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }}
    .code-box {{
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1rem 1.2rem;
      font-family: 'DM Mono', monospace; font-size: 0.78rem;
      color: var(--accent2); line-height: 1.8; white-space: pre; overflow-x: auto;
    }}
    .copy-btn {{
      display: block; width: 100%; margin-top: 0.8rem;
      background: var(--accent); color: white; border: none;
      border-radius: 8px; padding: 0.7rem;
      font-size: 0.88rem; cursor: pointer; font-family: inherit; font-weight: 600;
    }}
    .copy-btn:hover {{ opacity: 0.85; }}
    .note {{ font-size: 0.82rem; color: var(--muted); margin-top: 1.2rem; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">Authenticated</div>
    <h1>You're all set!</h1>
    <p class="email">Signed in as <strong>{user_email}</strong></p>
    <div class="section">
      <div class="label">Paste into your MCP client config</div>
      <div class="code-box" id="cfg">{config}</div>
      <button class="copy-btn" id="btn" onclick="copyConfig()">Copy Config</button>
    </div>
    <p class="note">
      Add this to Claude Desktop, Cline, or any MCP client.<br>
      Use add, subtract, multiply, divide, power right away.
    </p>
  </div>
  <script>
    function copyConfig() {{
      navigator.clipboard.writeText(document.getElementById('cfg').textContent);
      document.getElementById('btn').textContent = 'Copied!';
      setTimeout(() => document.getElementById('btn').textContent = 'Copy Config', 2000);
    }}
    window.onload = copyConfig;
  </script>
</body>
</html>"""
    return HTMLResponse(html)


async def oauth_token(request: Request):
    """
    Token endpoint (RFC 6749 section 4.1.3).
    MCP client POSTs the auth_code here to receive a bearer token.
    """
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)

    grant_type = body.get("grant_type")
    code       = body.get("code")

    if grant_type != "authorization_code" or not code:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    code_data = auth_codes.pop(code, None)
    if not code_data:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    if time.time() - code_data["created_at"] > 600:
        return JSONResponse({"error": "invalid_grant", "error_description": "Code expired"}, status_code=400)

    session_id = code_data["session_id"]
    mcp_token  = secrets.token_urlsafe(32)
    mcp_tokens[mcp_token] = session_id

    logger.info(f"Issued MCP token for session {session_id[:8]}")

    return JSONResponse({
        "access_token": mcp_token,
        "token_type":   "Bearer",
        "expires_in":   86400,
    })


# ─────────────────────────────────────────────
# SSE (MCP transport)
# ─────────────────────────────────────────────
async def handle_sse(request: Request):
    """Validate bearer token then start MCP session."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None

    if not token:
        token = request.query_params.get("token")

    if not token or token not in mcp_tokens:
        return Response(
            f"Unauthorized. Please sign in at {BASE_URL}",
            status_code=401,
            media_type="text/plain",
        )

    session = sessions.get(mcp_tokens[token])
    if not session:
        return Response("Session expired. Please sign in again.", status_code=401)

    logger.info(f"MCP session started for {session['email']}")

    async with mcp.sse_server() as streams:
        sse = SseServerTransport("/sse")
        return await sse(request, streams[0], streams[1])


# ─────────────────────────────────────────────
# Landing page
# ─────────────────────────────────────────────
async def home(request: Request):
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Calculator MCP</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0f; --surface: #13131a; --border: #1e1e2e;
      --accent: #7c6af7; --accent2: #4fc3f7; --text: #e8e8f0; --muted: #6b6b80;
    }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: 'DM Sans', sans-serif;
      min-height: 100vh; display: flex;
      align-items: center; justify-content: center; padding: 2rem;
    }}
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 20px; padding: 3rem 2.5rem;
      max-width: 480px; width: 100%; text-align: center;
      box-shadow: 0 0 60px rgba(124,106,247,0.08);
    }}
    .icon {{ font-size: 3rem; margin-bottom: 1.2rem; }}
    h1 {{ font-size: 1.8rem; font-weight: 600; margin-bottom: 0.5rem; }}
    .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 2rem; line-height: 1.6; }}
    .btn {{
      display: inline-flex; align-items: center; gap: 0.6rem;
      background: var(--accent); color: white;
      padding: 0.85rem 2rem; border-radius: 10px;
      text-decoration: none; font-weight: 600; font-size: 0.95rem;
      transition: opacity 0.2s, transform 0.2s;
    }}
    .btn:hover {{ opacity: 0.88; transform: translateY(-1px); }}
    hr {{ border: none; border-top: 1px solid var(--border); margin: 2rem 0; }}
    .label {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem; text-align: left; }}
    .code-block {{
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1rem 1.2rem;
      font-family: 'DM Mono', monospace; font-size: 0.78rem;
      color: var(--accent2); text-align: left; line-height: 1.8;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🧮</div>
    <h1>Calculator MCP</h1>
    <p class="subtitle">Secure MCP server with Google OAuth.<br>Add the URL below to your MCP client — it will ask you to sign in automatically.</p>
    <a href="/oauth/authorize" class="btn">
      <svg width="18" height="18" viewBox="0 0 24 24">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
      Sign in with Google
    </a>
    <hr>
    <p class="label">MCP Server URL — paste this into your client</p>
    <div class="code-block">{BASE_URL}/sse</div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


async def health(request: Request):
    return Response("OK", media_type="text/plain")


# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────
app = Starlette(
    routes=[
        Route("/",                                       home),
        Route("/oauth/authorize",                        oauth_authorize),
        Route("/oauth/callback",                         oauth_callback),
        Route("/oauth/token",                            oauth_token,    methods=["POST"]),
        Route("/oauth/register",                         oauth_register, methods=["POST"]),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Route("/sse",                                    handle_sse,     methods=["GET", "POST"]),
        Route("/health",                                 health),
    ]
)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting Calculator MCP - {BASE_URL}")
    uvicorn.run(app, host=host, port=port)
