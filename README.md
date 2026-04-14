# Calculator MCP with OAuth

MCP server with Google OAuth - fully hosted via SSE transport.

## Deployment

### 1. Google OAuth
- Google Cloud Console → New Project → Enable Google+ API
- Create OAuth 2.0 Client (Web app)
- Redirect URI: `https://your-app.onrender.com/oauth/callback`
- Save Client ID & Secret

### 2. Deploy to Render
```bash
git push origin main
```
On Render.com:
- New Web Service → Connect repo
- Environment:
  - `GOOGLE_CLIENT_ID`: your_id
  - `GOOGLE_CLIENT_SECRET`: your_secret
  - `BASE_URL`: `https://your-app.onrender.com`
- Deploy

## Usage

Users add to MCP config:

```json
{
  "mcpServers": {
    "calculator": {
      "url": "https://your-app.onrender.com/sse"
    }
  }
}
```

Then:
1. Visit `https://your-app.onrender.com` → Sign in with Google
2. Copy token
3. Use `authenticate(access_token)` tool
4. Calculate with session_id

## Tools
- `authenticate(access_token)` - Login with Google token
- `add(a, b, session_id)` - Addition
- `subtract(a, b, session_id)` - Subtraction
- `multiply(a, b, session_id)` - Multiplication
- `divide(a, b, session_id)` - Division
- `power(a, b, session_id)` - Exponentiation
