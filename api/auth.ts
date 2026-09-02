// Forensic Platform — Auth Module (Cloudflare Workers)
// PBKDF2-SHA512 (600k iterations), AES-256-GCM, constant-time comparison

export interface Env {
  DB: D1Database;
  KV: KVNamespace;
  R2: R2Bucket;
  JWT_SECRET: string;
  ENCRYPTION_KEY: string;
  TURNSTILE_SECRET: string;
  ENVIRONMENT: string;
  ALLOWED_ORIGINS: string;
}

// ============================================================
// PASSWORD HASHING (PBKDF2-SHA512, 600k iterations — OWASP 2023)
// ============================================================

export async function hashPassword(password: string): Promise<string> {
  const encoder = new TextEncoder();
  const salt = crypto.getRandomValues(new Uint8Array(32));
  const keyMaterial = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const hash = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-512' }, keyMaterial, 512);
  const hashB64 = btoa(String.fromCharCode(...new Uint8Array(hash)));
  const saltB64 = btoa(String.fromCharCode(...salt));
  return `pbkdf2:600000:${saltB64}:${hashB64}`;
}

export async function verifyPassword(password: string, storedHash: string): Promise<boolean> {
  const parts = storedHash.split(':');
  if (parts.length !== 4 || parts[0] !== 'pbkdf2') return false;
  const iterations = parseInt(parts[1], 10);
  const salt = Uint8Array.from(atob(parts[2]), c => c.charCodeAt(0));
  const expected = Uint8Array.from(atob(parts[3]), c => c.charCodeAt(0));
  const encoder = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const hash = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations, hash: 'SHA-512' }, keyMaterial, 512));
  if (hash.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < hash.length; i++) diff |= hash[i] ^ expected[i];
  return diff === 0;
}

// ============================================================
// SESSION MANAGEMENT
// ============================================================

export async function createSession(db: D1Database, userId: string, ip: string, ua: string) {
  const id = crypto.randomUUID();
  const token = generateToken(64);
  const tokenHash = await sha256(token);
  const expires = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
  await db.prepare('INSERT INTO sessions (id,user_id,token_hash,expires_at,ip_address,user_agent) VALUES (?,?,?,?,?,?)')
    .bind(id, userId, tokenHash, expires, ip, ua).run();
  return { id, token, expires };
}

export async function validateSession(db: D1Database, token: string) {
  const hash = await sha256(token);
  const s = await db.prepare('SELECT id,user_id,expires_at,revoked FROM sessions WHERE token_hash=? AND revoked=0').bind(hash).first();
  if (!s) return null;
  if (new Date(s.expires_at as string) < new Date()) {
    await db.prepare('DELETE FROM sessions WHERE id=?').bind(s.id).run();
    return null;
  }
  return { sessionId: s.id as string, userId: s.user_id as string };
}

export async function revokeSession(db: D1Database, sessionId: string) {
  await db.prepare('UPDATE sessions SET revoked=1 WHERE id=?').bind(sessionId).run();
}

export async function revokeAllSessions(db: D1Database, userId: string) {
  await db.prepare('UPDATE sessions SET revoked=1 WHERE user_id=?').bind(userId).run();
}

// ============================================================
// API KEY ENCRYPTION (AES-256-GCM)
// ============================================================

export async function encryptApiKey(apiKey: string, key: string): Promise<string> {
  const cryptoKey = await deriveKey(key);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const enc = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, cryptoKey, new TextEncoder().encode(apiKey)));
  const combined = new Uint8Array(iv.length + enc.length);
  combined.set(iv); combined.set(enc, iv.length);
  return btoa(String.fromCharCode(...combined));
}

export async function decryptApiKey(encrypted: string, key: string): Promise<string> {
  const combined = Uint8Array.from(atob(encrypted), c => c.charCodeAt(0));
  const iv = combined.slice(0, 12);
  const ct = combined.slice(12);
  const cryptoKey = await deriveKey(key);
  const dec = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, cryptoKey, ct);
  return new TextDecoder().decode(dec);
}

// ============================================================
// RATE LIMITING (KV sliding window)
// ============================================================

export async function checkRateLimit(kv: KVNamespace, key: string, max: number, windowSec: number) {
  const now = Math.floor(Date.now() / 1000);
  const wk = `rl:${key}:${Math.floor(now / windowSec)}`;
  const count = ((await kv.get(wk, 'json')) as number || 0) + 1;
  if (count > max) return { allowed: false, remaining: 0 };
  await kv.put(wk, count.toString(), { expirationTtl: windowSec * 2 });
  return { allowed: true, remaining: max - count };
}

// ============================================================
// AUDIT LOGGING
// ============================================================

export async function logAudit(db: D1Database, userId: string | null, action: string, resourceType?: string, resourceId?: string, details?: Record<string, unknown>, ip?: string) {
  await db.prepare('INSERT INTO audit_log (id,user_id,action,resource_type,resource_id,details_json,ip_address) VALUES (?,?,?,?,?,?,?)')
    .bind(crypto.randomUUID(), userId, action, resourceType || null, resourceId || null, details ? JSON.stringify(details) : null, ip || null).run();
}

// ============================================================
// CSRF (double-submit cookie)
// ============================================================

export function generateCsrfToken(): string { return generateToken(32); }
export function validateCsrf(cookie: string, header: string): boolean {
  if (!cookie || !header) return false;
  let d = 0; for (let i = 0; i < cookie.length; i++) d |= cookie.charCodeAt(i) ^ header.charCodeAt(i);
  return d === 0;
}

// ============================================================
// INPUT VALIDATION
// ============================================================

export function validateEmail(email: string): boolean {
  return /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/.test(email) && email.length <= 254;
}

export function validatePassword(pw: string): { valid: boolean; errors: string[] } {
  const e: string[] = [];
  if (pw.length < 12) e.push('Mínimo 12 caracteres');
  if (pw.length > 128) e.push('Máximo 128 caracteres');
  if (!/[a-z]/.test(pw)) e.push('Una minúscula');
  if (!/[A-Z]/.test(pw)) e.push('Una mayúscula');
  if (!/[0-9]/.test(pw)) e.push('Un número');
  if (!/[^a-zA-Z0-9]/.test(pw)) e.push('Un símbolo');
  const common = ['password','123456','qwerty','abc123','admin','letmein'];
  if (common.includes(pw.toLowerCase())) e.push('Contraseña muy común');
  return { valid: e.length === 0, errors: e };
}

export function sanitize(s: string): string {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
}

// ============================================================
// HELPERS
// ============================================================

function generateToken(len: number): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(len)))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
}

async function sha256(input: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
  return btoa(String.fromCharCode(...new Uint8Array(hash)));
}

async function deriveKey(material: string): Promise<CryptoKey> {
  const enc = new TextEncoder();
  const raw = await crypto.subtle.importKey('raw', enc.encode(material), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt: enc.encode('forensic-platform-salt-v1'), iterations: 100000, hash: 'SHA-256' }, raw, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
