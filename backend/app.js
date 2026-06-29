const express = require('express');
const cors = require('cors');
const jwt = require('jsonwebtoken');
const axios = require('axios');
const path = require('path');
const jwksClient = require('jwks-rsa');

const app = express();

app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3000'],
  credentials: true
}));

app.use(express.json());

const TENANT_ID = process.env.TENANT_ID || 'common';
const CLIENT_ID = process.env.MICROSOFT_CLIENT_ID;
const ALLOWED_DOMAIN = process.env.ALLOWED_DOMAIN || '@sarasanalytics.com';

// Allowed users - comma-separated email list (optional)
// If empty, all users from ALLOWED_DOMAIN can access
const ALLOWED_USERS = process.env.ALLOWED_USERS
  ? process.env.ALLOWED_USERS.split(',').map(e => e.trim().toLowerCase())
  : [];

// Admins who may view the dashboard access log (comma-separated; default Anudeep)
const ADMIN_EMAILS = (process.env.ADMIN_EMAILS || 'anudeep.kolla@sarasanalytics.com')
  .split(',').map(e => e.trim().toLowerCase());

// Dashboard access log (per-user aggregate). Durably persisted to a committed
// file in the repo (data/dashboard_access.json) via the GitHub API, and loaded
// on startup — so the in-dashboard panel keeps FULL history across restarts.
// Requires GITHUB_TOKEN (a token with contents:write on the repo) in the env;
// without it, the log is in-memory only (still console.logged for Render logs).
const serverStart = Date.now();
const accessLog = new Map(); // email -> { email, name, count, first, last }

const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';
const GITHUB_REPO  = process.env.GITHUB_REPO || 'sarasanalytics-com/saras-usage-dashboard';
const ACCESS_FILE  = 'data/dashboard_access.json';
const GH_HEADERS   = { Authorization: `Bearer ${GITHUB_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'saras-dashboard' };
let accessFileSha  = null;
let _saving = false, _pending = false;

async function loadAccessLog() {
  if (!GITHUB_TOKEN) { console.log('[access] no GITHUB_TOKEN — durable history disabled (in-memory only)'); return; }
  try {
    const url = `https://api.github.com/repos/${GITHUB_REPO}/contents/${ACCESS_FILE}`;
    const r = await axios.get(url, { headers: GH_HEADERS });
    accessFileSha = r.data.sha;
    const content = JSON.parse(Buffer.from(r.data.content, 'base64').toString());
    (content.users || []).forEach(u => { if (u.email) accessLog.set(u.email.toLowerCase(), u); });
    console.log(`[access] loaded ${accessLog.size} users from ${ACCESS_FILE}`);
  } catch (e) {
    if (e.response && e.response.status === 404) console.log('[access] no access file yet — created on first sign-in');
    else console.error('[access] load failed:', e.message);
  }
}

async function saveAccessLog() {
  if (!GITHUB_TOKEN) return;
  if (_saving) { _pending = true; return; } // coalesce concurrent writes
  _saving = true;
  try {
    const users = [...accessLog.values()].sort((a, b) => b.last - a.last);
    const body = {
      message: 'chore: dashboard access log update',
      content: Buffer.from(JSON.stringify({ updatedAt: Date.now(), users }, null, 2)).toString('base64'),
    };
    if (accessFileSha) body.sha = accessFileSha;
    const url = `https://api.github.com/repos/${GITHUB_REPO}/contents/${ACCESS_FILE}`;
    const r = await axios.put(url, body, { headers: GH_HEADERS });
    accessFileSha = r.data.content.sha;
  } catch (e) {
    if (e.response && e.response.status === 409) {        // stale sha → refresh and retry
      try {
        const url = `https://api.github.com/repos/${GITHUB_REPO}/contents/${ACCESS_FILE}`;
        const g = await axios.get(url, { headers: GH_HEADERS });
        accessFileSha = g.data.sha;
        _pending = true;
      } catch (_) {}
    } else { console.error('[access] save failed:', e.message); }
  } finally {
    _saving = false;
    if (_pending) { _pending = false; saveAccessLog(); }
  }
}

function recordAccess(email, name) {
  const e = (email || '').toLowerCase();
  if (!e) return;
  const now = Date.now();
  const rec = accessLog.get(e) || { email: e, name, count: 0, first: now, last: now };
  rec.count += 1; rec.last = now; if (name) rec.name = name;
  accessLog.set(e, rec);
  console.log(`[access] ${e} (${rec.name}) signed in — visits ${rec.count}`);
  saveAccessLog(); // persist durably (fire-and-forget)
}

// JWKS client for Microsoft token verification
const client = jwksClient({
  jwksUri: `https://login.microsoftonline.com/${TENANT_ID}/discovery/v2.0/keys`,
  cache: true,
  cacheMaxEntries: 10,
  cacheMaxAge: 86400000 // 24 hours
});

function getSigningKey(kid) {
  return client.getSigningKey(kid);
}

app.post('/auth/verify', async (req, res) => {
  try {
    const { token } = req.body;
    if (!token) {
      return res.status(400).json({ error: 'Token required' });
    }

    // Get the key ID from token header
    const decoded = jwt.decode(token, { complete: true });
    if (!decoded) {
      return res.status(401).json({ error: 'Invalid token format' });
    }

    const kid = decoded.header.kid;

    // Get the signing key from JWKS
    const signingKey = await getSigningKey(kid);
    const key = signingKey.getPublicKey();

    // Verify the token
    const payload = jwt.verify(token, key, {
      algorithms: ['RS256'],
      audience: CLIENT_ID
    });

    const email = payload.upn || payload.email;
    if (!email) {
      return res.status(401).json({ error: 'Email not found in token' });
    }

    if (!email.endsWith(ALLOWED_DOMAIN)) {
      return res.status(403).json({ error: 'Unauthorized domain' });
    }

    // Check if user is in whitelist (if whitelist is configured)
    if (ALLOWED_USERS.length > 0 && !ALLOWED_USERS.includes(email.toLowerCase())) {
      return res.status(403).json({ error: 'User access denied - not in allowed users list' });
    }

    const sessionToken = Buffer.from(
      JSON.stringify({ email, exp: Date.now() + 86400000, iat: Date.now() })
    ).toString('base64');

    recordAccess(email, payload.name || email.split('@')[0]);

    res.json({
      success: true,
      sessionToken,
      email,
      name: payload.name || email.split('@')[0],
      picture: null
    });
  } catch (err) {
    console.error('Verification error:', err.message);
    res.status(401).json({ error: 'Invalid token' });
  }
});

app.post('/auth/validate', (req, res) => {
  try {
    const { sessionToken } = req.body;
    if (!sessionToken) {
      return res.status(400).json({ valid: false });
    }

    const payload = JSON.parse(Buffer.from(sessionToken, 'base64').toString());
    if (payload.exp < Date.now()) {
      return res.json({ valid: false });
    }

    res.json({ valid: true, email: payload.email });
  } catch (err) {
    res.json({ valid: false });
  }
});

// Admin-only: who has signed in to the dashboard
app.post('/auth/access-log', (req, res) => {
  try {
    const { sessionToken } = req.body;
    if (!sessionToken) return res.status(401).json({ error: 'unauthorized' });
    const payload = JSON.parse(Buffer.from(sessionToken, 'base64').toString());
    if (!payload.exp || payload.exp < Date.now()) return res.status(401).json({ error: 'expired' });
    if (!ADMIN_EMAILS.includes((payload.email || '').toLowerCase())) {
      return res.status(403).json({ error: 'forbidden' });
    }
    const users = [...accessLog.values()].sort((a, b) => b.last - a.last);
    const since = users.length ? Math.min(...users.map(u => u.first || serverStart)) : serverStart;
    res.json({ since, totalVisits: users.reduce((s, u) => s + u.count, 0), users });
  } catch (err) {
    res.status(401).json({ error: 'invalid' });
  }
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

// Serve static files from public directory
app.use(express.static(path.join(__dirname, 'public')));

// Fallback: serve index.html for all other routes (SPA support)
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
  loadAccessLog(); // restore durable access history from the repo
});
