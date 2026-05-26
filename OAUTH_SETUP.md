# Google OAuth Setup Guide for Saras Dashboard

## Overview
This guide explains how to set up Google OAuth with Cloud Run backend for secure dashboard authentication.

## Prerequisites
- GCP account with admin access
- `gcloud` CLI installed
- Node.js 18+ (for local development)
- GitHub account with repository access

## Step 1: Create OAuth 2.0 Credentials in GCP

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **APIs & Services > Credentials**
4. Click **+ Create Credentials > OAuth client ID**
5. If prompted, configure the OAuth consent screen first:
   - Choose **Internal** (for company use)
   - Fill in app name: "Saras Analytics Dashboard"
   - Add user support email
   - Add admin email
   - In scopes, add `openid`, `email`, `profile`
6. For the OAuth client:
   - Choose **Web application**
   - Name: "Dashboard Client"
   - Add authorized JavaScript origins:
     - `https://yourdomain.com`
     - `http://localhost:3000` (for testing)
   - Add authorized redirect URIs:
     - `https://yourdomain.com` (where dashboard is hosted)
     - `http://localhost:3000` (for testing)
7. Copy the **Client ID** - you'll need this

## Step 2: Deploy Backend to Cloud Run

### 2.1 Prepare for deployment
```bash
cd backend
npm install
```

### 2.2 Create and configure Cloud Run service
```bash
# Set variables
PROJECT_ID="your-gcp-project-id"
SERVICE_NAME="saras-dashboard-oauth"
REGION="us-central1"
GOOGLE_CLIENT_ID="your-client-id-from-step-1"
ALLOWED_ORIGINS="https://yourdomain.com,http://localhost:3000"
ALLOWED_DOMAIN="@sarasanalytics.com"

# Build and deploy
gcloud run deploy $SERVICE_NAME \
  --source . \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID,ALLOWED_ORIGINS=$ALLOWED_ORIGINS,ALLOWED_DOMAIN=$ALLOWED_DOMAIN
```

This will output a service URL like: `https://saras-dashboard-oauth-xxxxx.run.app`

### 2.3 Test the backend
```bash
# Health check
curl https://saras-dashboard-oauth-xxxxx.run.app/health

# Should respond: {"status":"ok"}
```

## Step 3: Update Dashboard Configuration

### 3.1 Store secrets in GitHub (recommended)
Add two secrets to your GitHub repository:
- `GOOGLE_CLIENT_ID`: The client ID from Step 1
- `BACKEND_URL`: The Cloud Run service URL from Step 2

### 3.2 Update the Python script
Modify `update_dashboard.py` to inject these values:

```python
import os

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '__GOOGLE_CLIENT_ID__')
BACKEND_URL = os.getenv('BACKEND_URL', '__BACKEND_URL__')

# In the HTML injection section:
html = html.replace('__GOOGLE_CLIENT_ID__', GOOGLE_CLIENT_ID)
html.replace('__BACKEND_URL__', BACKEND_URL)
```

### 3.3 Update GitHub Actions Workflow
Add to your workflow that runs `update_dashboard.py`:

```yaml
env:
  GOOGLE_CLIENT_ID: ${{ secrets.GOOGLE_CLIENT_ID }}
  BACKEND_URL: ${{ secrets.BACKEND_URL }}
```

## Step 4: Local Testing

### 4.1 Start the backend locally
```bash
cd backend
export GOOGLE_CLIENT_ID="your-client-id"
export ALLOWED_DOMAIN="@sarasanalytics.com"
npm start
# Backend runs on http://localhost:8080
```

### 4.2 Update index.html for local testing
Replace in index.html:
- `__GOOGLE_CLIENT_ID__` with your actual Client ID
- `__BACKEND_URL__` with `http://localhost:8080`

### 4.3 Start a local server for the dashboard
```bash
cd ..
# Using Python
python -m http.server 3000
# or Node
npx serve -p 3000
```

Visit `http://localhost:3000` and test the Google sign-in flow.

## Step 5: Security Considerations

### CORS
The backend only accepts requests from configured origins. Update if hosting domain changes:
```bash
gcloud run services update $SERVICE_NAME \
  --update-env-vars ALLOWED_ORIGINS="https://newdomain.com"
```

### Domain Restriction
Currently restricted to `@sarasanalytics.com`. To change:
```bash
gcloud run services update $SERVICE_NAME \
  --update-env-vars ALLOWED_DOMAIN="@yourdomain.com"
```

### Session Management
- Sessions are stored in browser sessionStorage
- Session tokens are base64-encoded JWTs with 24-hour expiry
- Backend validates tokens on each request
- Logout clears all session data and disables auto-login

## Troubleshooting

### "Only @sarasanalytics.com accounts have access"
This error means the authenticated email doesn't match the allowed domain. Verify:
1. User is signing in with correct account
2. Backend `ALLOWED_DOMAIN` environment variable is correct

### CORS errors
This means the dashboard origin isn't in the allowed list:
1. Check backend `ALLOWED_ORIGINS` includes your domain
2. Redeploy with correct origin

### "Connection error" on signin
This usually means:
1. Backend is down - check Cloud Run service status
2. Network connectivity issue
3. Backend URL is incorrect in HTML

## Monitoring

### View backend logs
```bash
gcloud run logs read $SERVICE_NAME --region=$REGION --limit=50
```

### Monitor Cloud Run service
```bash
gcloud run services list --region=$REGION
gcloud run services describe $SERVICE_NAME --region=$REGION
```

## Next Steps

1. Configure custom domain in Cloud Run (optional but recommended)
2. Set up monitoring and alerting
3. Enable Cloud Audit Logs for security
4. Consider adding role-based access control in the backend

## Additional Resources

- [Google Sign-In Documentation](https://developers.google.com/identity/gsi/web)
- [Cloud Run Deployment Guide](https://cloud.google.com/run/docs/quickstarts/deploy-container)
- [OAuth 2.0 Best Practices](https://tools.ietf.org/html/draft-ietf-oauth-security-topics)
