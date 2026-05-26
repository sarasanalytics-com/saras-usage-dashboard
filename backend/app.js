const express = require('express');
const cors = require('cors');
const jwt = require('jsonwebtoken');
const axios = require('axios');
const path = require('path');

const app = express();

app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3000'],
  credentials: true
}));

app.use(express.json());

const TENANT_ID = process.env.TENANT_ID || 'common';
const CLIENT_ID = process.env.MICROSOFT_CLIENT_ID;
const ALLOWED_DOMAIN = process.env.ALLOWED_DOMAIN || '@sarasanalytics.com';

// Cache for JWKS (JSON Web Key Set) from Microsoft
let jwksCache = null;
let jwksCacheTime = 0;

async function getJWKS() {
  const now = Date.now();
  // Cache for 24 hours
  if (jwksCache && (now - jwksCacheTime) < 86400000) {
    return jwksCache;
  }

  try {
    const response = await axios.get(
      `https://login.microsoftonline.com/${TENANT_ID}/discovery/v2.0/keys`
    );
    jwksCache = response.data;
    jwksCacheTime = now;
    return jwksCache;
  } catch (err) {
    console.error('Failed to get JWKS:', err);
    throw err;
  }
}

function getKeyFromJWKS(kid, jwks) {
  const key = jwks.keys.find(k => k.kid === kid);
  if (!key) throw new Error('Key not found in JWKS');

  const jwk = {
    kty: key.kty,
    use: key.use,
    kid: key.kid,
    x5c: key.x5c,
    x5t: key.x5t,
    n: key.n,
    e: key.e
  };

  return require('jsonwebtoken').jwkToPem(jwk);
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
    const jwks = await getJWKS();
    const key = getKeyFromJWKS(kid, jwks);

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

    const sessionToken = Buffer.from(
      JSON.stringify({ email, exp: Date.now() + 86400000, iat: Date.now() })
    ).toString('base64');

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

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

// Serve static files from parent directory
app.use(express.static(path.join(__dirname, '..')));

// Fallback: serve index.html for all other routes (SPA support)
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'index.html'));
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
