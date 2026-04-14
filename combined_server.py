#!/usr/bin/env python3
"""
Calculator MCP Server with Proper OAuth 2.0 Authorization Flow
- MCP client shows "Authenticate" button
- User clicks → redirected to Google Sign In
- After sign in → MCP is ready to use
- No manual token/session handling needed
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
# session_id → { access_token, email, created_at }
sessions: dict = {}

# state → { redirect_uri, created_at }  (OAuth PKCE-lite state)
pending_states: dict = {}

# auth_code → session_id  (short-lived codes for MCP token exchange)
auth_codes: dict = {}

# mcp_token → session_id  (bearer tokens issued to MCP clients)
mcp_tokens: dict = {}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def get_session_from_request(request: Request) -> Optional[dict]:
    """Extract MCP bearer token from Authorization header and look up session."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        session_id = mcp_tokens.get(token)
        if session_id:
            return sessions.get(session_id)
    return None


def cleanup_old_entries():
    """Remove expired pending states and auth codes (older than 10 min)."""
    now = time.time()
    expired_states = [s for s, v in pending_states.items() if now - v["created_at"] > 600]
    expired_codes  = [c for c, v in auth_codes.items()    if now - v["created_at"] > 600]
    for s in expired_states:
        pending_states.pop(s, None)
    for c in expired_codes:
        auth_codes.pop(c, None)


# ─────────────────────────────────────────────
# MCP Server
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
    return json.dumps({"result": a * b, "operation": f"{a} × {b}"})


@mcp.tool()
async def divide(a: float, b: float) -> str:
    """Divide a by b."""
    if b == 0:
        return json.dumps({"error": "Division by zero is not allowed."})
    return json.dumps({"result": a / b, "operation": f"{a} ÷ {b}"})


@mcp.tool()
async def power(a: float, b: float) -> str:
    """Raise a to the power of b."""
    return json.dumps({"result": a ** b, "operation": f"{a} ^ {b}"})


# ─────────────────────────────────────────────
# OAuth / Web Routes
# ─────────────────────────────────────────────

async def home(request: Request):
    """Landing page shown in browser."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Calculator MCP</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0a0a0f;
      --surface: #13131a;
      --border: #1e1e2e;
      --accent: #7c6af7;
      --accent2: #4fc3f7;
      --text: #e8e8f0;
      --muted: #6b6b80;
      --success: #43e97b;
    }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'DM Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 3rem 2.5rem;
      max-width: 480px;
      width: 100%;
      text-align: center;
      box-shadow: 0 0 60px rgba(124,106,247,0.08);
    }
    .icon { font-size: 3rem; margin-bottom: 1.2rem; }
    h1 { font-size: 1.8rem; font-weight: 600; margin-bottom: 0.5rem; }
    .subtitle { color: var(--muted); font-size: 0.95rem; margin-bottom: 2rem; line-height: 1.6; }
    .btn {
      display: inline-flex; align-items: center; gap: 0.6rem;
      background: var(--accent); color: white;
      padding: 0.85rem 2rem; border-radius: 10px;
      text-decoration: none; font-weight: 600; font-size: 0.95rem;
      transition: opacity 0.2s, transform 0.2s;
    }
    .btn:hover { opacity: 0.88; transform: translateY(-1px); }
    .divider { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }
    .code-block {
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1rem 1.2rem;
      font-family: 'DM Mono', monospace; font-size: 0.78rem;
      color: var(--accent2); text-align: left; line-height: 1.8;
    }
    .label { font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem; text-align: left; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🧮</div>
    <h1>Calculator MCP</h1>
    <p class="subtitle">A secure MCP server with Google OAuth.<br>Add it to your MCP client to get started.</p>
    <a href="/oauth/google" class="btn">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
      Sign in with Google
    </a>
    <hr class="divider">
    <p class="label">MCP Configuration</p>
    <div class="code-block">
{<br>
&nbsp;&nbsp;"mcpServers": {<br>
&nbsp;&nbsp;&nbsp;&nbsp;"calculator": {<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"url": "BASE_URL/sse"<br>
&nbsp;&nbsp;&nbsp;&nbsp;}<br>
&nbsp;&nbsp;}<br>
}
    </div>
  </div>
</body>
</html>""".replace("BASE_URL", BASE_URL)
    return HTMLResponse(html)


async def oauth_authorize(request: Request):
    """
    MCP OAuth Authorization Endpoint.
    MCP clients redirect here to start the login flow.
    Supports both direct browser visits and MCP client-initiated flows.
    """
    cleanup_old_entries()

    # Parameters sent by MCP client (RFC 6749)
    client_id     = request.query_params.get("client_id", "mcp-client")
    redirect_uri  = request.query_params.get("redirect_uri", "")
    state         = request.query_params.get("state", "")
    response_type = request.query_params.get("response_type", "code")

    # Generate our own state to pass to Google, encoding the client's redirect info
    google_state = secrets.token_urlsafe(32)
    pending_states[google_state] = {
        "client_redirect_uri": redirect_uri,
        "client_state": state,
        "created_at": time.time(),
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
    Google redirects here after user signs in.
    We exchange the code, create a session, then redirect back to MCP client.
    """
    code  = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(f"<h2>❌ Google sign-in cancelled: {error}</h2>", status_code=400)

    if not code or state not in pending_states:
        return HTMLResponse("<h2>❌ Invalid or expired request. Please try again.</h2>", status_code=400)

    state_data = pending_states.pop(state)

    # Exchange code for tokens with Google
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

        # Fetch user info
        user_resp  = http_requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_info  = user_resp.json()
        user_email = user_info.get("email", "unknown")

    except Exception as exc:
        logger.error(f"Token exchange failed: {exc}")
        return HTMLResponse(f"<h2>❌ Authentication error: {exc}</h2>", status_code=500)

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

    # ── Case 1: MCP client initiated the flow ──
    # Issue a short-lived auth code; client will exchange it for an MCP token
    if client_redirect:
        auth_code = secrets.token_urlsafe(32)
        auth_codes[auth_code] = {
            "session_id": session_id,
            "created_at": time.time(),
        }
        sep = "&" if "?" in client_redirect else "?"
        redirect_url = f"{client_redirect}{sep}code={auth_code}"
        if client_state:
            redirect_url += f"&state={client_state}"
        return RedirectResponse(redirect_url)

    # ── Case 2: Direct browser visit ──
    # Issue an MCP bearer token and show success page
    mcp_token = secrets.token_urlsafe(32)
    mcp_tokens[mcp_token] = session_id

    sse_url = f"{BASE_URL}/sse"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Signed In — Calculator MCP</title>
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
      box-shadow: 0 0 60px rgba(67,233,123,0.06);
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
    .label {{ font-size: 0.78rem; color: var(--muted); margin-bottom: 0.4rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }}
    .code-box {{
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1rem 1.2rem;
      font-family: 'DM Mono', monospace; font-size: 0.8rem;
      color: var(--accent2); line-height: 1.8; position: relative;
    }}
    .copy-btn {{
      position: absolute; top: 0.6rem; right: 0.6rem;
      background: var(--accent); color: white; border: none;
      border-radius: 6px; padding: 0.3rem 0.7rem;
      font-size: 0.72rem; cursor: pointer; font-family: inherit;
    }}
    .copy-btn:hover {{ opacity: 0.85; }}
    .note {{ font-size: 0.82rem; color: var(--muted); margin-top: 1rem; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">✓ Authenticated</div>
    <h1>You're all set!</h1>
    <p class="email">Signed in as <strong>{user_email}</strong></p>

    <div class="section">
      <div class="label">Add to your MCP client config</div>
      <div class="code-box" id="config-box">
        {{<br>
        &nbsp;&nbsp;"mcpServers": {{<br>
        &nbsp;&nbsp;&nbsp;&nbsp;"calculator": {{<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"url": "{sse_url}",<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"headers": {{<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"Authorization": "Bearer {mcp_token}"<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;}}<br>
        &nbsp;&nbsp;&nbsp;&nbsp;}}<br>
        &nbsp;&nbsp;}}<br>
        }}
        <button class="copy-btn" onclick="copyConfig()">Copy</button>
      </div>
    </div>

    <p class="note">
      Paste this config into your MCP client (Claude Desktop, Cline, etc.).<br>
      You can now use <strong>add, subtract, multiply, divide, power</strong> — no further steps needed.
    </p>
  </div>
  <script>
    function copyConfig() {{
      const config = JSON.stringify({{
        mcpServers: {{
          calculator: {{
            url: "{sse_url}",
            headers: {{ Authorization: "Bearer {mcp_token}" }}
          }}
        }}
      }}, null, 2);
      navigator.clipboard.writeText(config);
      document.querySelector('.copy-btn').textContent = 'Copied!';
      setTimeout(() => document.querySelector('.copy-btn').textContent = 'Copy', 2000);
    }}
    window.onload = copyConfig;
  </script>
</body>
</html>"""
    return HTMLResponse(html)


async def oauth_token(request: Request):
    """
    MCP Token Endpoint (RFC 6749 §4.1.3).
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

    return JSONResponse({
        "access_token": mcp_token,
        "token_type":   "Bearer",
        "expires_in":   86400,
    })


async def oauth_metadata(request: Request):
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414).
    MCP clients fetch this to discover endpoints automatically.
    """
    return JSONResponse({
        "issuer":                                BASE_URL,
        "authorization_endpoint":               f"{BASE_URL}/oauth/authorize",
        "token_endpoint":                        f"{BASE_URL}/oauth/token",
        "response_types_supported":             ["code"],
        "grant_types_supported":                ["authorization_code"],
        "code_challenge_methods_supported":     [],
        "token_endpoint_auth_methods_supported": ["none"],
    })


async def handle_sse(request: Request):
    """SSE endpoint — validates bearer token before starting MCP session."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None

    # Also accept token as query param for clients that don't support headers
    if not token:
        token = request.query_params.get("token")

    if not token or token not in mcp_tokens:
        return Response(
            "Unauthorized. Please sign in at " + BASE_URL,
            status_code=401,
            media_type="text/plain",
        )

    session_id = mcp_tokens[token]
    session    = sessions.get(session_id)
    if not session:
        return Response("Session expired. Please sign in again.", status_code=401)

    logger.info(f"MCP session started for {session['email']}")

    async with mcp.sse_server() as (read_stream, write_stream):
        return await SseServerTransport.handle(read_stream, write_stream, request)


async def health(request: Request):
    return Response("OK", media_type="text/plain")


# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────
app = Starlette(
    routes=[
        # Web / OAuth
        Route("/",                        home),
        Route("/oauth/google",            oauth_authorize),   # kept for direct browser use
        Route("/oauth/authorize",         oauth_authorize),   # MCP standard endpoint
        Route("/oauth/callback",          oauth_callback),
        Route("/oauth/token",             oauth_token, methods=["POST"]),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),

        # MCP
        Route("/sse",    handle_sse),
        Route("/health", health),
    ]
)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting Calculator MCP on port {port}")
    logger.info(f"Base URL : {BASE_URL}")
    logger.info(f"SSE      : {BASE_URL}/sse")
    logger.info(f"OAuth    : {BASE_URL}/oauth/authorize")
    uvicorn.run(app, host="0.0.0.0", port=port)