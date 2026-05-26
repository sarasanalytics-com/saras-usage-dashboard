# Quick Reference Card - Microsoft OAuth Setup

## 🎯 6-Step Deployment

### Step 1️⃣: Azure Portal (3 min)
```
https://portal.azure.com/ → 
  App registrations → 
    New registration → 
      Name: "Saras Analytics Dashboard"
      Accounts: "This org only"
      
COPY:
  Application (client) ID: _________________
  Directory (tenant) ID:  _________________
```

### Step 2️⃣: Deploy Backend (2 min)
```bash
cd backend
npm install

gcloud run deploy saras-dashboard-oauth \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars \
    MICROSOFT_CLIENT_ID=<step1-client-id>,\
    TENANT_ID=<step1-tenant-id>,\
    ALLOWED_ORIGINS=https://yourdomain.com,\
    ALLOWED_DOMAIN=@sarasanalytics.com

COPY:
  Backend URL: _________________
```

### Step 3️⃣: GitHub Secrets (1 min)
```
GitHub Repo Settings → Secrets → New secret

Name: MICROSOFT_CLIENT_ID
Value: <from step 1>

Name: BACKEND_URL
Value: <from step 2>
```

### Step 4️⃣: Update Workflow (1 min)
```yaml
# In .github/workflows/update-dashboard.yml
- name: Update Dashboard
  env:
    MICROSOFT_CLIENT_ID: ${{ secrets.MICROSOFT_CLIENT_ID }}
    BACKEND_URL: ${{ secrets.BACKEND_URL }}
  run: python scripts/update_dashboard.py
```

### Step 5️⃣: Test Locally (5 min - optional)
```bash
# Terminal 1
cd backend
export MICROSOFT_CLIENT_ID=<step1-id>
export TENANT_ID=<step1-tid>
npm start

# Terminal 2
python -m http.server 3000

# Browser: http://localhost:3000
```

### Step 6️⃣: Production (1 min)
```bash
git commit -am "Add Microsoft OAuth"
git push
# Workflow runs automatically
```

---

## 📋 Checklist

- [ ] Step 1: Azure Portal credentials copied
- [ ] Step 2: Backend deployed, health check works
- [ ] Step 3: GitHub secrets created
- [ ] Step 4: Workflow updated
- [ ] Step 5: Local test passed (optional)
- [ ] Step 6: Production deployed

---

## 🔧 Environment Variables

```
# Microsoft Credentials (from Azure Portal)
MICROSOFT_CLIENT_ID=12345678-1234-1234-1234-123456789012
TENANT_ID=87654321-4321-4321-4321-210987654321

# Deployment URLs
BACKEND_URL=https://saras-dashboard-oauth-xxxxx.run.app

# Access Control
ALLOWED_DOMAIN=@sarasanalytics.com
ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:3000
```

---

## 🚨 Troubleshooting

| Problem | Check |
|---------|-------|
| No sign-in button | MICROSOFT_CLIENT_ID is not `__MICROSOFT_CLIENT_ID__` |
| CORS error | ALLOWED_ORIGINS includes your domain |
| Connection failed | Backend URL is correct, service is running |
| "Only @sarasanalytics.com" | User signing in with work account? |
| Token expired | Backend running? Restart with `npm start` |

**View logs:**
```bash
# Backend logs
gcloud run logs read saras-dashboard-oauth --limit=50

# Azure AD logs
Azure Portal → Azure AD → Sign-in logs
```

---

## 📞 Documentation

| Need | File |
|------|------|
| Step-by-step walkthrough | `STEP_BY_STEP_MICROSOFT.md` ⭐ START HERE |
| Complete reference | `MICROSOFT_OAUTH_SETUP.md` |
| 5-minute overview | `MICROSOFT_QUICK_START.md` |
| Verification | `DEPLOYMENT_CHECKLIST.md` |
| Implementation notes | `MICROSOFT_IMPLEMENTATION_SUMMARY.md` |

---

## ✅ What's Implemented

✅ Dashboard updated with Microsoft Sign-In button  
✅ Backend OAuth service created  
✅ Python credential injection configured  
✅ GitHub Actions ready  
✅ Comprehensive documentation  

## 🎬 Next Action

👉 **Open and follow**: `STEP_BY_STEP_MICROSOFT.md`

---

**Time to deploy**: ~15 minutes  
**Difficulty**: Easy (copy-paste friendly)  
**Support**: All docs in repo
