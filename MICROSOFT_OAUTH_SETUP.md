# Microsoft Entra ID Setup Guide for Saras Dashboard

## Overview
This guide explains how to set up Microsoft Entra ID OAuth with Cloud Run backend for secure dashboard authentication using Microsoft accounts.

## Prerequisites
- Azure Portal access with admin role
- `gcloud` CLI installed (for Cloud Run deployment)
- Node.js 18+ (for local development)
- GitHub account with repository access

## Step 1: Register Application in Azure Portal

### 1.1 Access Azure Portal
1. Go to https://portal.azure.com/
2. Sign in with your Azure admin account
3. Search for **"App registrations"** and click it

### 1.2 Create New App Registration
1. Click **"+ New registration"**
2. Fill in the form:
   - **Name**: `Saras Analytics Dashboard`
   - **Supported account types**: Select **"Accounts in this organizational directory only"** (single tenant)
   - Click **"Register"**

### 1.3 Copy Application Details
You're now on the app overview page. Copy these values (save in secure location):
- **Application (client) ID**: You'll need this for the backend
- **Directory (tenant) ID**: You'll need this too

Example format:
- Client ID: `12345678-1234-1234-1234-123456789012`
- Tenant ID: `87654321-4321-4321-4321-210987654321`

### 1.4 Create Client Secret
1. Go to **"Certificates & secrets"** in the left menu
2. Click **"+ New client secret"**
3. Description: `Dashboard Backend`
4. Expires: **6 months** (rotate regularly)
5. Click **"Add"**
6. **Copy the secret value immediately** (you won't see it again!)

Keep this secret safe - only the backend will use it.

### 1.5 Configure Redirect URIs
1. Go to **"Authentication"** in the left menu
2. Under "Platform configurations", click **"+ Add a platform"**
3. Choose **"Single-page application (SPA)"**
4. Add Redirect URIs:
   - `http://localhost:3000` (for local testing)
   - `https://yourdomain.com` (production domain)
5. Click **"Configure"**

### 1.6 Set API Permissions
1. Go to **"API permissions"** in the left menu
2. You should already have **"User.Read"** permission
3. If not, click **"+ Add a permission"**
   - Select **"Microsoft Graph"**
   - Choose **"Delegated permissions"**
   - Search for and select: `openid`, `profile`, `email`
4. Click **"Grant admin consent for [Your Org]"** (blue button)
5. Click **"Yes"**

## Step 2: Deploy Backend to Cloud Run

### 2.1 Prepare Backend Files
```bash
cd backend
npm install
```

### 2.2 Deploy to Cloud Run
```bash
# Set your variables
PROJECT_ID="your-gcp-project-id"
SERVICE_NAME="saras-dashboard-oauth"
REGION="us-central1"
MICROSOFT_CLIENT_ID="your-client-id-from-step-1"
TENANT_ID="your-tenant-id-from-step-1"
ALLOWED_ORIGINS="https://yourdomain.com,http://localhost:3000"
ALLOWED_DOMAIN="@sarasanalytics.com"

# Deploy
gcloud run deploy $SERVICE_NAME \
  --source . \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars MICROSOFT_CLIENT_ID=$MICROSOFT_CLIENT_ID,TENANT_ID=$TENANT_ID,ALLOWED_ORIGINS=$ALLOWED_ORIGINS,ALLOWED_DOMAIN=$ALLOWED_DOMAIN
```

**Copy the service URL**: `https://saras-dashboard-oauth-xxxxx.run.app`

### 2.3 Test Backend
```bash
curl https://saras-dashboard-oauth-xxxxx.run.app/health

# Should respond: {"status":"ok"}
```

## Step 3: Update Dashboard Configuration

### 3.1 Store Secrets in GitHub
Add two secrets to your GitHub repository (Settings > Secrets > Repository secrets):
- `MICROSOFT_CLIENT_ID`: The Client ID from Step 1.3
- `BACKEND_URL`: The Cloud Run service URL from Step 2.2

### 3.2 Update GitHub Actions Workflow
In your workflow file that runs `update_dashboard.py`, add:

```yaml
env:
  MICROSOFT_CLIENT_ID: ${{ secrets.MICROSOFT_CLIENT_ID }}
  BACKEND_URL: ${{ secrets.BACKEND_URL }}
```

The Python script will automatically inject these values into `index.html`.

## Step 4: Local Testing

### 4.1 Create .env File
Create `backend/.env`:
```
PORT=8080
MICROSOFT_CLIENT_ID=your-client-id-from-step-1
TENANT_ID=your-tenant-id-from-step-1
ALLOWED_ORIGINS=http://localhost:3000
ALLOWED_DOMAIN=@sarasanalytics.com
```

### 4.2 Start Backend
```bash
cd backend
npm start
# Should print: "Server running on port 8080"
```

### 4.3 Update HTML for Local Testing
In `index.html`, find these lines and replace with actual values:
```javascript
const MICROSOFT_CLIENT_ID = '__MICROSOFT_CLIENT_ID__';
const BACKEND_URL = '__BACKEND_URL__';
```

Replace with:
```javascript
const MICROSOFT_CLIENT_ID = 'your-client-id-from-step-1';
const BACKEND_URL = 'http://localhost:8080';
```

### 4.4 Start Dashboard Server
```bash
cd ..
python -m http.server 3000
# Visit http://localhost:3000
```

### 4.5 Test Sign-In
1. Open http://localhost:3000
2. You should see login modal with "Sign in with Microsoft" button
3. Click the button
4. Sign in with your @sarasanalytics.com account
5. After authentication, dashboard should display
6. Click "Sign Out" to test logout

## Step 5: Security Considerations

### CORS Configuration
The backend only accepts requests from configured origins. If you change the domain:

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

### Tenant vs Multi-Tenant
- **Single Tenant**: Only users from your Azure AD directory can sign in
- **Multi-Tenant**: Any Microsoft account can sign in (less secure)

Currently configured as **single tenant** (recommended).

### Session Management
- Sessions stored in browser `sessionStorage` (cleared on browser close)
- Session tokens valid for 24 hours
- Tokens cannot be forged (verified by backend)
- Sign-out clears all session data

## Troubleshooting

### Error: "Only @sarasanalytics.com accounts have access"
**Cause**: User email doesn't match allowed domain
**Fix**: 
- Verify user is signing in with correct work account
- Check backend `ALLOWED_DOMAIN` env var is correct

### Error: "Authentication failed"
**Cause**: Could be multiple reasons
**Debug**:
1. Check browser console for errors
2. Check backend logs: `gcloud run logs read $SERVICE_NAME`
3. Verify MICROSOFT_CLIENT_ID matches Azure app

### Error: CORS blocked request
**Cause**: Dashboard origin not in allowed list
**Fix**:
1. Check backend `ALLOWED_ORIGINS` includes your domain
2. Redeploy backend with correct origins

### Error: Can't find "Sign in with Microsoft" button
**Cause**: MSAL library failed to load or credentials not injected
**Debug**:
1. Check browser console for errors
2. Check that `MICROSOFT_CLIENT_ID` is not `__MICROSOFT_CLIENT_ID__` (placeholder)
3. Verify `update_dashboard.py` ran successfully

## Monitoring

### View Backend Logs
```bash
gcloud run logs read saras-dashboard-oauth --region=us-central1 --limit=50
```

### Monitor Cloud Run Service
```bash
gcloud run services list --region=us-central1
gcloud run services describe saras-dashboard-oauth --region=us-central1
```

### Check Azure AD Logs
1. Go to Azure Portal > Azure AD > Sign-in logs
2. Filter by app name "Saras Analytics Dashboard"
3. Review authentication attempts and failures

## Additional Resources

- [Microsoft Entra ID Documentation](https://learn.microsoft.com/en-us/azure/active-directory/)
- [MSAL.js Documentation](https://github.com/AzureAD/microsoft-authentication-library-for-js)
- [Cloud Run Deployment Guide](https://cloud.google.com/run/docs/quickstarts/deploy-container)
- [OAuth 2.0 Best Practices](https://tools.ietf.org/html/draft-ietf-oauth-security-topics)

## Next Steps

1. **Configure custom domain** in Cloud Run (optional but recommended)
2. **Enable multi-factor authentication** in Azure AD
3. **Set up Azure AD group-based access** (optional)
4. **Enable audit logging** in Azure Portal
5. **Monitor sign-in activity** regularly

## Rotating Credentials

### Rotate Client Secret (every 6 months)
1. In Azure Portal, go to **Certificates & secrets**
2. Create new client secret
3. Store securely
4. Update backend with new secret
5. Delete old secret

### Rotate Client ID (rare)
Only needed if compromised:
1. Delete the app registration
2. Create new one from scratch
3. Update GitHub secrets
4. Redeploy backend

---

**Status**: Ready for deployment  
**Last Updated**: May 25, 2026  
**Next**: Follow Step 1 onwards to deploy
