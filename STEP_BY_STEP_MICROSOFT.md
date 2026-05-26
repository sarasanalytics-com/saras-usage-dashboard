# Step-by-Step: Microsoft Entra ID OAuth Setup

## Complete Walkthrough with Screenshots

---

## STEP 1: Get Microsoft Credentials from Azure Portal

### What You'll Get
- **Application (client) ID** - Needed for dashboard
- **Directory (tenant) ID** - Needed for backend
- **Client Secret** - Needed for backend

### Detailed Steps

#### 1.1 Open Azure Portal
```
1. Go to: https://portal.azure.com/
2. Sign in with your Azure admin account
   (Usually your @sarasanalytics.com email)
```

#### 1.2 Find "App registrations"
```
1. In the search bar at top, type: "App registrations"
2. Click on "App registrations" in the results
```

#### 1.3 Create New Registration
```
1. Click the blue "+ New registration" button
2. Fill in these fields:
   
   Name: Saras Analytics Dashboard
   
   Supported account types: 
   ☑ Accounts in this organizational directory only
     (NOT multi-tenant)
   
   Redirect URI:
   Platform: Single-page application (SPA)
   URI: http://localhost:3000

3. Click "Register"
```

#### 1.4 Copy Your Client Details
After registration, you're on the app overview page:

**SAVE THESE VALUES SOMEWHERE SAFE:**
```
Application (client) ID: ________________________
   (Example: 12345678-1234-1234-1234-123456789012)

Directory (tenant) ID: ________________________
   (Example: 87654321-4321-4321-4321-210987654321)
```

#### 1.5 Add More Redirect URIs
```
1. In left menu, click "Authentication"
2. Under "Single-page application", you should see:
   http://localhost:3000
3. Click "Add URI"
4. Add: https://yourdomain.com
   (Replace with your actual dashboard domain)
5. Click "Save"
```

#### 1.6 Create Client Secret
```
1. In left menu, click "Certificates & secrets"
2. Click "+ New client secret"
3. Description: Dashboard Backend
4. Expires: 6 months
5. Click "Add"
6. IMMEDIATELY COPY the secret value
   (You won't see it again!)
```

**SAVE THIS VALUE:**
```
Client Secret: ________________________
   (Example: abc123~xyz789def456...)
```

#### 1.7 Set Permissions
```
1. In left menu, click "API permissions"
2. You should see "User.Read" permission listed
3. Click "Grant admin consent for [Your Org]"
4. Click "Yes" in confirmation dialog
```

✅ **Step 1 Complete** - You now have all Microsoft credentials needed.

---

## STEP 2: Deploy Backend to Cloud Run

### What This Does
Creates a secure server that:
- Validates Microsoft login tokens
- Checks that users are from @sarasanalytics.com
- Creates session tokens for the dashboard

### Prerequisites
- `gcloud` CLI installed
- GCP project created
- Cloud Run API enabled

### Detailed Steps

#### 2.1 Install Backend Dependencies
```bash
# Open terminal/PowerShell
# Navigate to your repo
cd path/to/saras-usage-dashboard

# Install Node packages
cd backend
npm install

# Should complete without errors
```

#### 2.2 Deploy to Cloud Run
```bash
# Set your values from Step 1
# (Replace with actual values from Step 1)

gcloud run deploy saras-dashboard-oauth \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars \
    MICROSOFT_CLIENT_ID=<client-id-from-step-1>,\
    TENANT_ID=<tenant-id-from-step-1>,\
    ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:3000,\
    ALLOWED_DOMAIN=@sarasanalytics.com
```

**Example with actual values:**
```bash
gcloud run deploy saras-dashboard-oauth \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars \
    MICROSOFT_CLIENT_ID=12345678-1234-1234-1234-123456789012,\
    TENANT_ID=87654321-4321-4321-4321-210987654321,\
    ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:3000,\
    ALLOWED_DOMAIN=@sarasanalytics.com
```

#### 2.3 Wait for Deployment
```
The command will show progress. When done, you'll see:
  Service: saras-dashboard-oauth
  Revision: [something]
  Status: ✓ Active
  
  URL: https://saras-dashboard-oauth-xxxxx.run.app
```

#### 2.4 Save the Service URL
```
COPY and SAVE this URL:
Backend URL: https://saras-dashboard-oauth-xxxxx.run.app
```

#### 2.5 Test Backend Health
```bash
# Replace with your actual URL
curl https://saras-dashboard-oauth-xxxxx.run.app/health

# Should return: {"status":"ok"}
```

✅ **Step 2 Complete** - Backend is deployed and ready.

---

## STEP 3: Add GitHub Secrets

### What This Does
Stores your OAuth credentials securely so GitHub Actions can use them when deploying.

### Detailed Steps

#### 3.1 Open GitHub Repository
```
1. Go to your GitHub repository
2. Click "Settings" tab at the top
3. In left menu, scroll to "Security"
4. Click "Secrets and variables" → "Actions"
```

#### 3.2 Add First Secret (Client ID)
```
1. Click "New repository secret" button
2. In "Name" field, type: MICROSOFT_CLIENT_ID
3. In "Secret" field, paste the client ID from Step 1
   Example: 12345678-1234-1234-1234-123456789012
4. Click "Add secret"
```

#### 3.3 Add Second Secret (Backend URL)
```
1. Click "New repository secret" button
2. In "Name" field, type: BACKEND_URL
3. In "Secret" field, paste the URL from Step 2
   Example: https://saras-dashboard-oauth-xxxxx.run.app
4. Click "Add secret"
```

✅ **Step 3 Complete** - Secrets are stored securely.

---

## STEP 4: Update GitHub Actions Workflow

### What This Does
Tells GitHub Actions to inject your OAuth credentials when updating the dashboard.

### Detailed Steps

#### 4.1 Find Your Workflow File
```
Your workflow file is at:
.github/workflows/update-dashboard.yml

Or create it from the example:
.github/workflows/update-dashboard.yml.example
```

#### 4.2 Add Environment Variables
In the "Update Dashboard" job, find the `env:` section:

```yaml
- name: Update Dashboard with Microsoft OAuth
  env:
    # Add these two lines:
    MICROSOFT_CLIENT_ID: ${{ secrets.MICROSOFT_CLIENT_ID }}
    BACKEND_URL: ${{ secrets.BACKEND_URL }}
    
    # Keep existing secrets:
    ANTHROPIC_ANALYTICS_KEY: ${{ secrets.ANTHROPIC_ANALYTICS_KEY }}
    CLICKUP_API_KEY: ${{ secrets.CLICKUP_API_KEY }}

  run: |
    python scripts/update_dashboard.py
```

#### 4.3 Commit and Push
```bash
# From repo root
git add .github/workflows/update-dashboard.yml
git commit -m "Add Microsoft OAuth to GitHub Actions workflow"
git push
```

✅ **Step 4 Complete** - Workflow is configured.

---

## STEP 5: Test Locally (Optional but Recommended)

### What This Does
Lets you test the complete OAuth flow on your local machine before production.

### Detailed Steps

#### 5.1 Create Backend Environment File
In `backend/` folder, create file called `.env`:

```
PORT=8080
MICROSOFT_CLIENT_ID=<paste-client-id-from-step-1>
TENANT_ID=<paste-tenant-id-from-step-1>
ALLOWED_ORIGINS=http://localhost:3000
ALLOWED_DOMAIN=@sarasanalytics.com
```

#### 5.2 Start Backend Server
```bash
# In backend folder
npm start

# Should show: "Server running on port 8080"
```

#### 5.3 Update HTML for Local Testing
Edit `index.html`, find these lines (around line 1256):

```javascript
const MICROSOFT_CLIENT_ID = '__MICROSOFT_CLIENT_ID__';
const BACKEND_URL = '__BACKEND_URL__';
```

Replace with actual values:

```javascript
const MICROSOFT_CLIENT_ID = '12345678-1234-1234-1234-123456789012';
const BACKEND_URL = 'http://localhost:8080';
```

#### 5.4 Start Dashboard Server
In a new terminal:

```bash
# From repo root (not in backend folder)
python -m http.server 3000

# Should show: "Serving HTTP on 0.0.0.0 port 3000"
```

#### 5.5 Test Sign-In
```
1. Open browser: http://localhost:3000
2. You should see "Sign in with Microsoft" button
3. Click the button
4. Microsoft login popup appears
5. Sign in with your @sarasanalytics.com email
6. After login, modal closes and dashboard shows
7. Click "Sign Out" to test logout
```

### Troubleshooting Local Test

**Problem**: "Sign in with Microsoft" button not visible
- Solution: Check that `MICROSOFT_CLIENT_ID` is not `__MICROSOFT_CLIENT_ID__`

**Problem**: Click button but nothing happens
- Solution: Check browser console (F12 → Console tab) for errors
- Check that MSAL library loaded successfully

**Problem**: "Connection error" message
- Solution: Make sure backend is running (`npm start`)
- Verify BACKEND_URL is correct in HTML

✅ **Step 5 Complete** - Local testing works.

---

## STEP 6: Deploy to Production

### Detailed Steps

#### 6.1 Verify Secrets Are Set
In GitHub repo, Settings > Secrets:
- ☑ MICROSOFT_CLIENT_ID is set
- ☑ BACKEND_URL is set

#### 6.2 Update HTML for Production
In `index.html`, verify these lines use placeholders:

```javascript
const MICROSOFT_CLIENT_ID = '__MICROSOFT_CLIENT_ID__';
const BACKEND_URL = '__BACKEND_URL__';
```

(Do NOT use actual values in production - let GitHub Actions inject them)

#### 6.3 Run Update Workflow
```bash
# Push a change that triggers workflow
# Or manually trigger from GitHub:
#   Actions tab → Update Dashboard → Run workflow
```

#### 6.4 Verify Dashboard
```
1. Go to https://yourdomain.com
2. Login modal should appear with "Sign in with Microsoft" button
3. Click and test sign-in with @sarasanalytics.com account
4. Dashboard should load
5. Try Sign Out
```

✅ **Step 6 Complete** - Production deployment done.

---

## Verification Checklist

- [ ] Step 1: Have Microsoft Client ID and Tenant ID
- [ ] Step 2: Backend deployed to Cloud Run, health check works
- [ ] Step 3: GitHub Secrets created (MICROSOFT_CLIENT_ID, BACKEND_URL)
- [ ] Step 4: GitHub Actions workflow updated
- [ ] Step 5: Local testing works (optional)
- [ ] Step 6: Production dashboard shows Microsoft login

---

## Quick Reference

| What | Where to Get |
|------|-------------|
| Client ID | Azure Portal > App registrations > Application (client) ID |
| Tenant ID | Azure Portal > App registrations > Directory (tenant) ID |
| Backend URL | Cloud Run console > saras-dashboard-oauth > Service URL |
| GitHub Secrets | GitHub Repo > Settings > Secrets and variables |

---

## Still Need Help?

**For detailed information:**
- See `MICROSOFT_OAUTH_SETUP.md` - Complete reference guide
- See `MICROSOFT_QUICK_START.md` - 5-minute overview
- See `DEPLOYMENT_CHECKLIST.md` - Full verification checklist

**For troubleshooting:**
- Check Cloud Run logs: `gcloud run logs read saras-dashboard-oauth --limit=50`
- Check Azure AD sign-in logs: Azure Portal > Azure AD > Sign-in logs
- Check browser console: F12 → Console tab for JavaScript errors
