# Google OAuth Setup - Quick Start

## 1. GCP Setup (5 minutes)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. **APIs & Services > Credentials**
3. Create OAuth 2.0 credential (Web application):
   - Add authorized origins: `https://yourdomain.com`
   - Copy the **Client ID**

## 2. Deploy Backend to Cloud Run (5 minutes)

```bash
cd backend
npm install

# Replace with your values
gcloud run deploy saras-dashboard-oauth \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLIENT_ID=<your-client-id>,ALLOWED_ORIGINS=https://yourdomain.com,ALLOWED_DOMAIN=@sarasanalytics.com
```

Copy the service URL: `https://saras-dashboard-oauth-xxxxx.run.app`

## 3. Update GitHub Secrets

Add to your GitHub repo Settings > Secrets:
- `GOOGLE_CLIENT_ID`: The Client ID from step 1
- `BACKEND_URL`: The Cloud Run URL from step 2

## 4. Update GitHub Actions Workflow

Add to your workflow file:
```yaml
env:
  GOOGLE_CLIENT_ID: ${{ secrets.GOOGLE_CLIENT_ID }}
  BACKEND_URL: ${{ secrets.BACKEND_URL }}
```

When `update_dashboard.py` runs, it will automatically inject these values.

## 5. Test Locally (optional)

```bash
# Terminal 1: Start backend
cd backend
export GOOGLE_CLIENT_ID="your-client-id"
npm start

# Terminal 2: Start dashboard (from repo root)
python -m http.server 3000

# Visit http://localhost:3000
```

## Security Notes

✅ **Automatic**
- Domain restriction to @sarasanalytics.com
- CORS protection
- Session expiry after 24 hours
- Secure token validation

⚙️ **Recommended**
- Enable Cloud Audit Logs in GCP
- Set up Cloud Run alerts
- Regularly review authorized users

## Troubleshooting

**"Only @sarasanalytics.com accounts" error?**
- Verify ALLOWED_DOMAIN env var on backend
- Check user's email domain

**CORS error?**
- Add dashboard domain to ALLOWED_ORIGINS
- Redeploy backend

**Connection failed?**
- Verify BACKEND_URL is correct
- Check backend is running: `curl https://backend-url/health`

## Full Setup Guide

See `OAUTH_SETUP.md` for complete documentation.
