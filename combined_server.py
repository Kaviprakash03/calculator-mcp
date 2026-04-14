#!/usr/bin/env python3
"""
Combined OAuth + MCP Server with SSE transport
Single deployment - handles both OAuth and MCP
"""

import logging
import os
import json
import secrets
from urllib.parse import urlencode
from typing import Optional
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import HTMLResponse, RedirectResponse, Response
import requests as http_requests
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
REDIRECT_URI = f"{BASE_URL}/oauth/callback"

# MCP server
mcp = FastMCP("calculator-oauth")

# Session storage
sessions = {}
pending_auth = {}


class AuthenticatedSession:
    def __init__(self, session_id: str, access_token: str):
        self.session_id = session_id
        self.access_token = access_token
        self.user_email = None
        self.authenticated = False
        
    def authenticate(self) -> bool:
        try:
            response = http_requests.get(
                "https://www.googleapis.com/oauth2/v3/tokeninfo",
                params={"access_token": self.access_token}
            )
            if response.status_code == 200:
                self.user_email = response.json().get("email", "unknown")
                self.authenticated = True
                return True
            return False
        except:
            return False


# OAuth Routes
async def oauth_home(request):
    html = """
    <!DOCTYPE html>
    <html><head><title>Calculator MCP - OAuth</title>
    <style>
        body { font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px; }
        .button { background: #4285f4; color: white; padding: 15px 40px; 
                  border: none; border-radius: 8px; font-size: 18px; 
                  text-decoration: none; display: inline-block; }
    </style></head><body>
    <h1>🔐 Calculator MCP</h1>
    <p>Secure OAuth Authentication</p>
    <a href="/oauth/google" class="button">🔑 Sign in with Google</a>
    </body></html>
    """
    return HTMLResponse(html)


async def oauth_google(request):
    state = secrets.token_urlsafe(32)
    pending_auth[state] = {"timestamp": "now"}
    
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


async def oauth_callback(request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    
    if not code or state not in pending_auth:
        return HTMLResponse("<h1>❌ Invalid Request</h1>", status_code=400)
    
    try:
        token_response = http_requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code"
            }
        )
        
        token_data = token_response.json()
        if "error" in token_data:
            raise Exception(token_data["error"])
        
        access_token = token_data.get("access_token")
        
        user_info_response = http_requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_email = user_info_response.json().get("email", "unknown")
        
        html = f"""
        <!DOCTYPE html>
        <html><head><title>Authentication Successful</title>
        <style>
            body {{ font-family: Arial; max-width: 800px; margin: 50px auto; padding: 20px; }}
            .token-box {{ background: #f5f5f5; padding: 20px; border-radius: 8px; 
                         font-family: monospace; word-break: break-all; }}
            .copy-btn {{ background: #4285f4; color: white; padding: 12px 30px; 
                        border: none; border-radius: 8px; cursor: pointer; }}
        </style></head><body>
        <h1>✅ Authentication Successful!</h1>
        <p><strong>Logged in as:</strong> {user_email}</p>
        <h3>Your Access Token:</h3>
        <div class="token-box" id="token">{access_token}</div>
        <br>
        <button class="copy-btn" onclick="copyToken()">📋 Copy Token</button>
        <script>
            function copyToken() {{
                navigator.clipboard.writeText(document.getElementById('token').textContent);
                alert('Token copied!');
            }}
            window.onload = copyToken;
        </script>
        <p>Use this token with the <code>authenticate</code> tool in your MCP client.</p>
        </body></html>
        """
        
        pending_auth.pop(state, None)
        return HTMLResponse(html)
        
    except Exception as e:
        return HTMLResponse(f"<h1>❌ Error: {str(e)}</h1>", status_code=500)


# MCP Tools
@mcp.tool()
async def authenticate(access_token: str, session_id: Optional[str] = None) -> str:
    """Authenticate with Google OAuth token"""
    import uuid
    if not session_id:
        session_id = str(uuid.uuid4())
    
    session = AuthenticatedSession(session_id, access_token)
    if not session.authenticate():
        return json.dumps({"status": "error", "error": "Authentication failed"})
    
    sessions[session_id] = session
    return json.dumps({
        "status": "success",
        "session_id": session_id,
        "user": session.user_email
    })


def verify_session(session_id: Optional[str]):
    if not session_id:
        return False, None, "No session_id"
    session = sessions.get(session_id)
    if not session or not session.authenticated:
        return False, None, "Invalid session"
    return True, session, ""


@mcp.tool()
async def add(a: float, b: float, session_id: str) -> str:
    """Add two numbers"""
    is_valid, session, error_msg = verify_session(session_id)
    if not is_valid:
        return json.dumps({"status": "error", "error": error_msg})
    return json.dumps({"result": a + b, "user": session.user_email})


@mcp.tool()
async def subtract(a: float, b: float, session_id: str) -> str:
    """Subtract two numbers"""
    is_valid, session, error_msg = verify_session(session_id)
    if not is_valid:
        return json.dumps({"status": "error", "error": error_msg})
    return json.dumps({"result": a - b, "user": session.user_email})


@mcp.tool()
async def multiply(a: float, b: float, session_id: str) -> str:
    """Multiply two numbers"""
    is_valid, session, error_msg = verify_session(session_id)
    if not is_valid:
        return json.dumps({"status": "error", "error": error_msg})
    return json.dumps({"result": a * b, "user": session.user_email})


@mcp.tool()
async def divide(a: float, b: float, session_id: str) -> str:
    """Divide two numbers"""
    is_valid, session, error_msg = verify_session(session_id)
    if not is_valid:
        return json.dumps({"status": "error", "error": error_msg})
    if b == 0:
        return json.dumps({"status": "error", "error": "Division by zero"})
    return json.dumps({"result": a / b, "user": session.user_email})


@mcp.tool()
async def power(a: float, b: float, session_id: str) -> str:
    """Calculate power"""
    is_valid, session, error_msg = verify_session(session_id)
    if not is_valid:
        return json.dumps({"status": "error", "error": error_msg})
    return json.dumps({"result": a ** b, "user": session.user_email})


# MCP SSE endpoint
async def handle_sse(request):
    async with mcp.sse_server() as (read_stream, write_stream):
        return await SseServerTransport.handle(read_stream, write_stream, request)


async def health(request):
    return Response("OK")


# Create app
app = Starlette(
    routes=[
        Route("/", oauth_home),
        Route("/oauth/google", oauth_google),
        Route("/oauth/callback", oauth_callback),
        Route("/sse", handle_sse),
        Route("/health", health),
    ]
)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting Combined Server on port {port}")
    logger.info(f"OAuth: {BASE_URL}")
    logger.info(f"MCP SSE: {BASE_URL}/sse")
    uvicorn.run(app, host="0.0.0.0", port=port)