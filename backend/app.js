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

// In-memory dashboard access log (per-user aggregate). Resets when the server
// restarts; every sign-in is also console.logged so Render keeps full history.
const serverStart = Date.now();
const accessLog = new Map(); // email -> { email, name, count, first, last }
function recordAccess(email, name) {
  const e = (email || '').toLowerCase();
  if (!e) return;
  const now = Date.now();
  const rec = accessLog.get(e) || { email: e, name, count: 0, first: now, last: now };
  rec.count += 1; rec.last = now; if (name) rec.name = name;
  accessLog.set(e, rec);
  console.log(`[access] ${e} (${rec.name}) signed in — visits ${rec.count}`);
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
    res.json({ since: serverStart, totalVisits: users.reduce((s, u) => s + u.count, 0), users });
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
});
