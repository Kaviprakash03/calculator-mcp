# Step-by-Step Deployment Guide

## Complete guide to deploy your MCP server with OAuth

---

## Step 1: Google OAuth Setup (10 minutes)

### 1.1 Create Google Cloud Project

1. Go to https://console.cloud.google.com/
2. Sign in with your Google account
3. Click "Select a project" dropdown at top
4. Click "NEW PROJECT"
5. Project name: `Calculator MCP`
6. Click "CREATE"
7. Wait 30 seconds, then click "SELECT PROJECT"

### 1.2 Enable Google+ API

1. In left menu → "APIs & Services" → "Library"
2. Search: `Google+ API`
3. Click on it
4. Click "ENABLE"

### 1.3 Configure OAuth Consent Screen

1. Left menu → "APIs & Services" → "OAuth consent screen"
2. User Type: Select "External"
3. Click "CREATE"
4. Fill in:
   - App name: `Calculator MCP`
   - User support email: Your email
   - Developer contact: Your email
5. Click "SAVE AND CONTINUE"
6. Scopes: Click "SAVE AND CONTINUE" (keep defaults)
7. Test users: Click "SAVE AND CONTINUE" (skip)
8. Click "BACK TO DASHBOARD"

### 1.4 Create OAuth Credentials

1. Left menu → "APIs & Services" → "Credentials"
2. Click "+ CREATE CREDENTIALS"
3. Select "OAuth 2.0 Client ID"
4. Application type: "Web application"
5. Name: `Calculator MCP`
6. Authorized redirect URIs:
   - Click "+ ADD URI"
   - Enter: `http://localhost:8000/oauth/callback`
   - Click "+ ADD URI" again
   - Enter: `https://calculator-mcp.onrender.com/oauth/callback`
     (Use your chosen name instead of `calculator-mcp`)
7. Click "CREATE"

### 1.5 Save Credentials

**IMPORTANT:** Copy these now!

```
Client ID: something.apps.googleusercontent.com
Client Secret: GOCSPX-something
```

Save them in a text file - you'll need them for Render.

---

## Step 2: Push to GitHub (5 minutes)

### 2.1 Initialize Git (if not already)

```bash
cd /home/kavi-23797/YaliQA/calculate

git init
git add .
git commit -m "Calculator MCP with OAuth - SSE transport"
```

### 2.2 Create GitHub Repository

1. Go to https://github.com/
2. Click "+" → "New repository"
3. Repository name: `calculator-mcp`
4. Description: `MCP server with Google OAuth and SSE transport`
5. Public or Private: Your choice
6. Don't initialize with README
7. Click "Create repository"

### 2.3 Push Code

```bash
# Add GitHub remote (replace YOUR_USERNAME)
git remote add origin https://github.com/YOUR_USERNAME/calculator-mcp.git

# Push
git branch -M main
git push -u origin main
```

Verify: Visit your GitHub repo - all files should be there.

---

## Step 3: Deploy to Render (10 minutes)

### 3.1 Create Render Account

1. Go to https://render.com/
2. Click "Get Started"
3. Sign up with GitHub (recommended)
4. Authorize Render

### 3.2 Create Web Service

1. Dashboard → Click "New +"
2. Select "Web Service"
3. Find your repository: `calculator-mcp`
4. Click "Connect"

### 3.3 Configure Service

Fill in the form:

**Name:**
```
calculator-mcp
```
(Or any name you want - this becomes your URL)

**Region:**
- Choose closest to you (e.g., Oregon, Frankfurt, Singapore)

**Branch:**
```
main
```

**Root Directory:**
- Leave blank

**Runtime:**
```
Python 3
```

**Build Command:**
```
pip install -r requirements.txt
```

**Start Command:**
```
python combined_server.py
```

**Instance Type:**
```
Free
```

### 3.4 Add Environment Variables

Scroll to "Environment Variables" section.

Click "Add Environment Variable" three times and add:

**Variable 1:**
- Key: `GOOGLE_CLIENT_ID`
- Value: `your_client_id.apps.googleusercontent.com`
  (Paste from Step 1.5)

**Variable 2:**
- Key: `GOOGLE_CLIENT_SECRET`
- Value: `GOCSPX-your_secret`
  (Paste from Step 1.5)

**Variable 3:**
- Key: `BASE_URL`
- Value: `https://calculator-mcp.onrender.com`
  (Replace `calculator-mcp` with YOUR service name from above)

### 3.5 Deploy!

1. Click "Create Web Service"
2. Wait for deployment (2-3 minutes)
3. Watch the logs - should see "Starting Combined Server..."
4. Status changes to "Live" ✅

### 3.6 Get Your URL

Copy your URL:
```
https://calculator-mcp.onrender.com
```
(Or whatever name you chose)

---

## Step 4: Update Google OAuth (2 minutes)

### 4.1 Add Production Redirect URI

1. Go back to Google Cloud Console
2. "APIs & Services" → "Credentials"
3. Click on your OAuth 2.0 Client
4. Under "Authorized redirect URIs"
5. Make sure you have:
   ```
   https://calculator-mcp.onrender.com/oauth/callback
   ```
   (Replace with YOUR Render URL)
6. Click "SAVE"

---

## Step 5: Test Everything (5 minutes)

### 5.1 Test OAuth Flow

1. Visit your URL in browser:
   ```
   https://calculator-mcp.onrender.com
   ```

2. You should see:
   - "Calculator MCP" heading
   - "Sign in with Google" button

3. Click "Sign in with Google"

4. Sign in with your Google account

5. Authorize the app

6. You should see:
   - "Authentication Successful!"
   - Your email
   - An access token
   - Token auto-copied to clipboard

**If this works: OAuth is working! ✅**

### 5.2 Test MCP Endpoint

Visit:
```
https://calculator-mcp.onrender.com/health
```

Should show: `OK`

**If this works: MCP server is running! ✅**

---

## Step 6: Share with Users

### 6.1 What to Tell Users

Share this with anyone who wants to use your MCP:

```markdown
# Calculator MCP

Add to your MCP configuration:

{
  "mcpServers": {
    "calculator": {
      "url": "https://calculator-mcp.onrender.com/sse"
    }
  }
}

First use:
1. Visit https://calculator-mcp.onrender.com
2. Sign in with Google
3. Copy the token
4. In your MCP client, call: authenticate(access_token="your_token")
5. You'll get a session_id
6. Use calculator tools with that session_id!

Tools:
- add(a, b, session_id)
- subtract(a, b, session_id)
- multiply(a, b, session_id)
- divide(a, b, session_id)
- power(a, b, session_id)
```

### 6.2 User Configuration Examples

**VS Code / Cline:**
```json
{
  "mcpServers": {
    "calculator": {
      "url": "https://calculator-mcp.onrender.com/sse"
    }
  }
}
```

**Claude Desktop:**
```json
{
  "mcpServers": {
    "calculator": {
      "url": "https://calculator-mcp.onrender.com/sse"
    }
  }
}
```

---

## Troubleshooting

### Issue: "redirect_uri_mismatch"

**Solution:**
1. Check Google Console → Credentials → OAuth Client
2. Make sure redirect URI exactly matches:
   `https://your-app.onrender.com/oauth/callback`
3. No trailing slash!

### Issue: Server not responding

**Solution:**
1. Check Render logs (on Render dashboard)
2. Verify environment variables are set correctly
3. Restart service if needed

### Issue: "Invalid client"

**Solution:**
- Verify `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render exactly match Google Console

### Issue: First request is slow

**Normal!** Render free tier sleeps after 15 minutes. First request takes 30-60 seconds to wake up.

---

## Complete Checklist

- [ ] Google Cloud project created
- [ ] Google+ API enabled
- [ ] OAuth consent screen configured
- [ ] OAuth 2.0 credentials created
- [ ] Client ID & Secret saved
- [ ] Code pushed to GitHub
- [ ] Render account created
- [ ] Web service created on Render
- [ ] Environment variables added (3 total)
- [ ] Service deployed successfully
- [ ] Production redirect URI added to Google
- [ ] OAuth flow tested (can sign in)
- [ ] Health endpoint tested
- [ ] URL shared with users

---

## Summary

**What you deployed:**
- Single server handling both OAuth and MCP
- Uses SSE transport (streamable HTTP)
- Fully hosted on Render (free)

**What users do:**
1. Add your SSE URL to config
2. Visit your site to get token
3. Authenticate in MCP client
4. Use calculator

**Your URLs:**
- OAuth: `https://your-app.onrender.com`
- MCP SSE: `https://your-app.onrender.com/sse`
- Health: `https://your-app.onrender.com/health`

**Cost:** $0 (Render free tier)

---

🎉 **You're done! Your MCP server is live and ready for users!**