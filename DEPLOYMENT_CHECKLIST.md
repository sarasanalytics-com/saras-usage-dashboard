# Google OAuth Deployment Checklist

## Pre-Deployment (GCP Setup)

### Google Cloud Console
- [ ] Create or select a GCP project
- [ ] Enable Google+ API
- [ ] Enable Cloud Run API
- [ ] Create OAuth 2.0 credential (Web Application)
- [ ] Add authorized JavaScript origins (production domain)
- [ ] Add authorized redirect URI (production domain)
- [ ] Copy Client ID to secure location
- [ ] Copy Client Secret (if needed for advanced setup)

## Deployment Phase 1: Backend to Cloud Run

### Prepare Backend
```bash
cd backend
npm install
```
- [ ] Verify `app.js` exists and has OAuth endpoints
- [ ] Verify `package.json` has required dependencies
- [ ] Check `Dockerfile` is present
- [ ] Check `.gcloudignore` exists

### Deploy Service
```bash
gcloud run deploy saras-dashboard-oauth \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLIENT_ID=<CLIENT_ID>,ALLOWED_ORIGINS=<ORIGINS>,ALLOWED_DOMAIN=@sarasanalytics.com
```
- [ ] Command executes successfully
- [ ] Service is created in Cloud Run
- [ ] Copy the service URL: `https://saras-dashboard-oauth-xxxxx.run.app`
- [ ] Test health endpoint: `curl https://your-url/health`

### Verify Backend Deployment
```bash
# Should return {"status":"ok"}
curl https://saras-dashboard-oauth-xxxxx.run.app/health
```
- [ ] Health check passes
- [ ] No error messages in logs
- [ ] Cloud Run service shows "Running" status

## Deployment Phase 2: GitHub Secrets

### Add Repository Secrets
1. Go to GitHub repo > Settings > Secrets and variables > Repository secrets
2. Create new secrets:

- [ ] `GOOGLE_CLIENT_ID` = Your OAuth Client ID from Step 1
- [ ] `BACKEND_URL` = Your Cloud Run service URL from Phase 1

### Verify Secrets
- [ ] Both secrets appear in the secrets list
- [ ] Secrets have correct values (spot-check first few chars)
- [ ] Secrets are marked as "Actions" type

## Deployment Phase 3: Update GitHub Actions

### Add Workflow File
1. Copy `.github/workflows/update-dashboard.yml.example` to `.github/workflows/update-dashboard.yml`
2. Verify it includes:

```yaml
env:
  GOOGLE_CLIENT_ID: ${{ secrets.GOOGLE_CLIENT_ID }}
  BACKEND_URL: ${{ secrets.BACKEND_URL }}
```

- [ ] Workflow file exists at `.github/workflows/update-dashboard.yml`
- [ ] Environment variables reference secrets correctly
- [ ] Workflow runs `python scripts/update_dashboard.py`

### Test Workflow
- [ ] Trigger workflow manually: Actions > Update Dashboard > Run workflow
- [ ] Workflow completes successfully
- [ ] Check that `index.html` was updated
- [ ] Verify OAuth credentials were injected:

```bash
grep "GOOGLE_CLIENT_ID\|BACKEND_URL" index.html | grep -v "__"
```

Should show actual values, not placeholders like `__GOOGLE_CLIENT_ID__`

- [ ] Commit message appears in git history

## Verification Phase: Local Testing

### Setup Local Environment
```bash
# Terminal 1: Backend
cd backend
export GOOGLE_CLIENT_ID="<your-client-id>"
export ALLOWED_DOMAIN="@sarasanalytics.com"
npm start
# Should print: "Server running on port 8080"
```

```bash
# Terminal 2: Dashboard
python -m http.server 3000
# Should print: "Serving HTTP on 0.0.0.0 port 3000"
```

### Test Login Flow
1. Open `http://localhost:3000`
2. Login modal appears with "Sign in with Google" button
   - [ ] Modal displays correctly
   - [ ] No JavaScript errors in console
   - [ ] Google Sign-In button is visible

3. Click "Sign in with Google"
   - [ ] Google OAuth consent screen appears
   - [ ] Can sign in with @sarasanalytics.com account
   - [ ] Backend receives request

4. Successful login
   - [ ] Login modal closes
   - [ ] Dashboard content displays
   - [ ] `sessionStorage.getItem('dashboard-auth')` returns 'true'

5. Refresh page
   - [ ] Session is still valid
   - [ ] Dashboard content displays immediately
   - [ ] No need to re-authenticate

6. Test Sign Out
   - [ ] Click "Sign Out" in header
   - [ ] Page reloads
   - [ ] Login modal reappears
   - [ ] sessionStorage is cleared

### Test Error Cases
- [ ] Try signing in with non-@sarasanalytics.com account
  - Expected: "Only @sarasanalytics.com accounts have access" error
- [ ] Try with invalid/expired session token
  - Expected: Automatic logout and re-login required
- [ ] Try accessing dashboard directly (skip login)
  - Expected: Redirected to login modal

## Verification Phase: Production Testing

### Pre-Launch Checks
- [ ] Production domain is authorized in OAuth credentials
- [ ] Cloud Run backend ALLOWED_ORIGINS includes production domain
- [ ] GitHub secrets are correct for production
- [ ] Dashboard is deployed with OAuth credentials injected

### Live Testing
1. Navigate to production dashboard URL
   - [ ] Login modal appears
   - [ ] Google Sign-In button works
   - [ ] Can authenticate with @sarasanalytics.com account

2. Verify secure connection
   - [ ] URL is HTTPS
   - [ ] SSL certificate is valid
   - [ ] No mixed content warnings

3. Test key features
   - [ ] Dashboard loads after login
   - [ ] All tabs work (Overview, Spends, etc.)
   - [ ] Data displays correctly
   - [ ] Sign Out works properly

### Monitor After Launch
- [ ] Check Cloud Run logs for errors
  ```bash
  gcloud run logs read saras-dashboard-oauth --limit=50
  ```
- [ ] Monitor authentication failures
- [ ] Check for CORS errors
- [ ] Verify session validation is working

## Rollback Plan

If deployment fails, rollback to token-based auth:

1. In GitHub Actions, temporarily remove OAuth env vars
2. Revert `index.html` to previous commit
3. Manually update `index.html` to use token auth
4. Run workflow to deploy reverted version

## Documentation

- [ ] OAUTH_SETUP.md exists and is complete
- [ ] QUICK_START.md exists and is accurate
- [ ] OAUTH_IMPLEMENTATION_SUMMARY.md is up to date
- [ ] Team has been notified of OAuth login requirement
- [ ] Documentation is accessible to all users

## Post-Deployment

### Security Measures
- [ ] Enable Cloud Audit Logs for authentication events
- [ ] Set up alerts for authentication failures
- [ ] Review Cloud Run service permissions
- [ ] Verify no logs contain sensitive tokens

### Maintenance
- [ ] Document how to update ALLOWED_DOMAIN
- [ ] Document how to add new authorized origins
- [ ] Document how to rotate OAuth credentials
- [ ] Schedule quarterly security review

### Monitoring
- [ ] Set up Cloud Run metrics dashboard
- [ ] Monitor authentication success/failure rates
- [ ] Monitor Cloud Run latency
- [ ] Monitor CORS errors

## Sign-Off

- [ ] All checks completed and passing
- [ ] Team approval obtained
- [ ] Documentation reviewed
- [ ] Ready for production deployment

---

**Deployment Date**: ___________  
**Deployed By**: ___________  
**Notes**: _________________________
