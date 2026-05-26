# Microsoft Entra ID Setup - Quick Start (5 Minutes)

## 1. Azure Portal Setup (3 minutes)

1. Go to https://portal.azure.com/
2. Search **"App registrations"** → Click it
3. **+ New registration**
   - Name: `Saras Analytics Dashboard`
   - Supported accounts: `Accounts in this organizational directory only`
   - Click **Register**
4. **Copy these values:**
   - Application (client) ID
   - Directory (tenant) ID
5. **Certificates & secrets** → **+ New client secret**
   - Copy the secret value
6. **Authentication**
   - Click **+ Add a platform** → **Single-page application**
   - Add redirect URIs:
     - `http://localhost:3000`
     - `https://yourdomain.com`
7. **API permissions**
   - Ensure you have `User.Read` permission
   - Click **Grant admin consent**

## 2. Deploy Backend to Cloud Run (2 minutes)

```bash
cd backend
npm install

# Replace with your values from Step 1
gcloud run deploy saras-dashboard-oauth \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars MICROSOFT_CLIENT_ID=<client-id>,TENANT_ID=<tenant-id>,ALLOWED_ORIGINS=https://yourdomain.com,ALLOWED_DOMAIN=@sarasanalytics.com
```

**Save the service URL**: `https://saras-dashboard-oauth-xxxxx.run.app`

## 3. Add GitHub Secrets (1 minute)

In GitHub repo Settings > Secrets > Repository secrets:
- `MICROSOFT_CLIENT_ID`: From Step 1
- `BACKEND_URL`: From Step 2

## 4. Update GitHub Actions (1 minute)

In your workflow file (e.g., `.github/workflows/update-dashboard.yml`):

```yaml
env:
  MICROSOFT_CLIENT_ID: ${{ secrets.MICROSOFT_CLIENT_ID }}
  BACKEND_URL: ${{ secrets.BACKEND_URL }}
```

Next time workflow runs, `update_dashboard.py` will automatically inject these.

## 5. Test Locally (Optional)

```bash
# Terminal 1: Backend
cd backend
export MICROSOFT_CLIENT_ID="your-client-id"
export TENANT_ID="your-tenant-id"
npm start

# Terminal 2: Dashboard
python -m http.server 3000

# Visit http://localhost:3000
```

---

## ✅ What You Get

- ✅ "Sign in with Microsoft" button (no password needed)
- ✅ Only @sarasanalytics.com accounts can access
- ✅ Automatic domain verification
- ✅ 24-hour session expiry
- ✅ Secure token validation
- ✅ Sign out clears all session data

## 🆘 Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| "Only @sarasanalytics.com accounts" | User must sign in with work account |
| CORS error | Add domain to ALLOWED_ORIGINS in backend |
| Connection failed | Check backend URL is correct, verify health endpoint |
| No sign-in button | Check MICROSOFT_CLIENT_ID is not a placeholder |

---

**Full guide**: See `MICROSOFT_OAUTH_SETUP.md`  
**Deployment checklist**: See `DEPLOYMENT_CHECKLIST.md`
