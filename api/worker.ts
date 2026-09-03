// Forensic Platform — API Worker (Cloudflare Workers + Hono)
// Entry point for all API routes

import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { secureHeaders } from 'hono/secure-headers';
import {
  hashPassword, verifyPassword, createSession, validateSession,
  revokeSession, revokeAllSessions, encryptApiKey, decryptApiKey,
  checkRateLimit, logAudit, generateCsrfToken, validateCsrf,
  validateEmail, validatePassword, sanitize,
  type Env,
} from './auth';

const app = new Hono<{ Bindings: Env }>();

// ============================================================
// MIDDLEWARE
// ============================================================

// Security headers
app.use('*', secureHeaders());

// CORS
app.use('/api/*', cors({
  origin: (origin, c) => {
    const allowed = c.env.ALLOWED_ORIGINS?.split(',') || [];
    return allowed.includes(origin) ? origin : allowed[0] || '*';
  },
  credentials: true,
}));

// Auth middleware for protected routes
async function requireAuth(c: any) {
  const cookie = c.req.header('Cookie') || '';
  const sessionToken = cookie.split(';').find((s: string) => s.trim().startsWith('session='))?.split('=')[1];
  if (!sessionToken) return c.json({ error: 'No autenticado' }, 401);
  const session = await validateSession(c.env.DB, sessionToken);
  if (!session) return c.json({ error: 'Sesión inválida o expirada' }, 401);
  return session;
}

// Rate limit middleware
async function rateLimitMiddleware(c: any, key: string, max: number = 100, window: number = 60) {
  const result = await checkRateLimit(c.env.KV, key, max, window);
  if (!result.allowed) return c.json({ error: 'Demasiadas solicitudes' }, 429);
  return null;
}

// ============================================================
// AUTH ROUTES
// ============================================================

// POST /api/auth/register
app.post('/api/auth/register', async (c) => {
  const ip = c.req.header('CF-Connecting-IP') || 'unknown';
  const rl = await rateLimitMiddleware(c, `register:${ip}`, 5, 900);
  if (rl) return rl;

  const body = await c.req.json();
  const { email, password, nombre, apellido, organizacion } = body;

  if (!email || !password || !nombre || !apellido) {
    return c.json({ error: 'Campos requeridos: email, password, nombre, apellido' }, 400);
  }

  if (!validateEmail(email)) {
    return c.json({ error: 'Correo electrónico inválido' }, 400);
  }

  const pwValidation = validatePassword(password);
  if (!pwValidation.valid) {
    return c.json({ error: 'Contraseña no cumple requisitos', details: pwValidation.errors }, 400);
  }

  // Check if email exists
  const existing = await c.env.DB.prepare('SELECT id FROM users WHERE email=?').bind(email.toLowerCase()).first();
  if (existing) {
    return c.json({ error: 'Este correo ya está registrado' }, 409);
  }

  const userId = crypto.randomUUID();
  const passwordHash = await hashPassword(password);

  await c.env.DB.prepare(
    'INSERT INTO users (id,email,password_hash,nombre,apellido,organizacion) VALUES (?,?,?,?,?,?)'
  ).bind(userId, email.toLowerCase(), sanitize(passwordHash), sanitize(nombre), sanitize(apellido), sanitize(organizacion || '')).run();

  const session = await createSession(c.env.DB, userId, ip, c.req.header('User-Agent') || '');
  await logAudit(c.env.DB, userId, 'register', 'user', userId, null, ip);

  c.header('Set-Cookie', `session=${session.token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`);
  return c.json({ ok: true, user: { id: userId, email: email.toLowerCase(), nombre, apellido } });
});

// POST /api/auth/login
app.post('/api/auth/login', async (c) => {
  try {
    const ip = c.req.header('CF-Connecting-IP') || 'unknown';
    const rl = await rateLimitMiddleware(c, `login:${ip}`, 10, 900);
    if (rl) return rl;

    const body = await c.req.json();
    const { email, password } = body;
    if (!email || !password) return c.json({ error: 'Email y contraseña requeridos' }, 400);

    const user = await c.env.DB.prepare(
      'SELECT id,email,password_hash,nombre,apellido,role,failed_login_attempts,locked_until FROM users WHERE email=?'
    ).bind(email.toLowerCase()).first();

    if (!user) {
      await logAudit(c.env.DB, null, 'login_failed', 'user', null, { email }, ip);
      return c.json({ error: 'Credenciales inválidas' }, 401);
    }

    // Check account lockout
    if (user.locked_until && new Date(user.locked_until as string) > new Date()) {
      return c.json({ error: 'Cuenta bloqueada temporalmente. Intenta más tarde.' }, 423);
    }

    const valid = await verifyPassword(password, user.password_hash as string);
    if (!valid) {
      const attempts = (user.failed_login_attempts as number || 0) + 1;
      const lockUntil = attempts >= 5 ? new Date(Date.now() + 15 * 60 * 1000).toISOString() : null;
      await c.env.DB.prepare('UPDATE users SET failed_login_attempts=?, locked_until=? WHERE id=?')
        .bind(attempts, lockUntil, user.id).run();
      await logAudit(c.env.DB, user.id as string, 'login_failed', 'user', user.id as string, { attempts }, ip);
      return c.json({ error: 'Credenciales inválidas' }, 401);
    }

    // Reset failed attempts
    await c.env.DB.prepare('UPDATE users SET failed_login_attempts=0, locked_until=NULL, last_login=datetime("now") WHERE id=?')
      .bind(user.id).run();

    const session = await createSession(c.env.DB, user.id as string, ip, c.req.header('User-Agent') || '');
    await logAudit(c.env.DB, user.id as string, 'login', 'user', user.id as string, null, ip);

    c.header('Set-Cookie', `session=${session.token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`);
    return c.json({ ok: true, user: { id: user.id, email: user.email, nombre: user.nombre, apellido: user.apellido, role: user.role } });
  } catch (err: any) {
    console.error('Login error:', err);
    return c.json({ error: 'Error interno', detail: err.message }, 500);
  }
});

// POST /api/auth/logout
app.post('/api/auth/logout', async (c) => {
  const cookie = c.req.header('Cookie') || '';
  const token = cookie.split(';').find(s => s.trim().startsWith('session='))?.split('=')[1];
  if (token) {
    const hash = await (await import('./auth')).default?.sha256?.(token) || '';
    // Revoke session in DB
    const session = await validateSession(c.env.DB, token);
    if (session) {
      await revokeSession(c.env.DB, session.sessionId);
      await logAudit(c.env.DB, session.userId, 'logout', 'user', session.userId, null, c.req.header('CF-Connecting-IP'));
    }
  }
  c.header('Set-Cookie', 'session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0');
  return c.json({ ok: true });
});

// GET /api/auth/me
app.get('/api/auth/me', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const user = await c.env.DB.prepare('SELECT id,email,nombre,apellido,telefono,organizacion,role,created_at FROM users WHERE id=?')
    .bind(session.userId).first();
  if (!user) return c.json({ error: 'Usuario no encontrado' }, 404);
  return c.json(user);
});

// PATCH /api/auth/me
app.patch('/api/auth/me', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const body = await c.req.json();
  const { nombre, apellido, telefono, organizacion } = body;
  await c.env.DB.prepare('UPDATE users SET nombre=?,apellido=?,telefono=?,organizacion=?,updated_at=datetime("now") WHERE id=?')
    .bind(sanitize(nombre||''), sanitize(apellido||''), sanitize(telefono||''), sanitize(organizacion||''), session.userId).run();
  await logAudit(c.env.DB, session.userId, 'profile_update', 'user', session.userId);
  const user = await c.env.DB.prepare('SELECT id,email,nombre,apellido,telefono,organizacion,role,created_at FROM users WHERE id=?')
    .bind(session.userId).first();
  return c.json(user);
});

// POST /api/auth/password
app.post('/api/auth/password', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const { current_password, new_password } = await c.req.json();
  if (!current_password || !new_password) return c.json({ error: 'Contraseñas requeridas' }, 400);
  const pwV = validatePassword(new_password);
  if (!pwV.valid) return c.json({ error: 'Contraseña no cumple requisitos', details: pwV.errors }, 400);
  const user = await c.env.DB.prepare('SELECT password_hash FROM users WHERE id=?').bind(session.userId).first();
  if (!user || !await verifyPassword(current_password, user.password_hash as string)) {
    return c.json({ error: 'Contraseña actual incorrecta' }, 401);
  }
  const newHash = await hashPassword(new_password);
  await c.env.DB.prepare('UPDATE users SET password_hash=?,updated_at=datetime("now") WHERE id=?').bind(newHash, session.userId).run();
  await revokeAllSessions(c.env.DB, session.userId);
  await logAudit(c.env.DB, session.userId, 'password_change', 'user', session.userId);
  c.header('Set-Cookie', 'session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0');
  return c.json({ ok: true });
});

// GET /api/auth/sessions
app.get('/api/auth/sessions', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const sessions = await c.env.DB.prepare('SELECT id,ip_address,user_agent,created_at FROM sessions WHERE user_id=? AND revoked=0 ORDER BY created_at DESC')
    .bind(session.userId).all();
  return c.json(sessions.results.map((s: any) => ({ ...s, current: s.id === session.sessionId })));
});

// DELETE /api/auth/sessions/:id
app.delete('/api/auth/sessions/:id', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  await c.env.DB.prepare('UPDATE sessions SET revoked=1 WHERE id=? AND user_id=?').bind(c.req.param('id'), session.userId).run();
  return c.json({ ok: true });
});

// DELETE /api/auth/sessions (revoke all except current)
app.delete('/api/auth/sessions', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  await c.env.DB.prepare('UPDATE sessions SET revoked=1 WHERE user_id=? AND id!=?').bind(session.userId, session.sessionId).run();
  await logAudit(c.env.DB, session.userId, 'revoke_all_sessions', 'user', session.userId);
  return c.json({ ok: true });
});

// DELETE /api/auth/me
app.delete('/api/auth/me', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  await c.env.DB.prepare('DELETE FROM users WHERE id=?').bind(session.userId).run();
  await logAudit(c.env.DB, session.userId, 'account_deleted', 'user', session.userId);
  c.header('Set-Cookie', 'session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0');
  return c.json({ ok: true });
});

// ============================================================
// CASES ROUTES
// ============================================================

// GET /api/cases
app.get('/api/cases', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const owned = await c.env.DB.prepare(
    `SELECT c.*, (SELECT COUNT(*) FROM documents WHERE case_id=c.id) as document_count,
     (SELECT COUNT(*) FROM analysis_results WHERE case_id=c.id) as result_count
     FROM cases c WHERE c.owner_id=? AND c.status!='deleted' ORDER BY c.updated_at DESC`
  ).bind(session.userId).all();
  const shared = await c.env.DB.prepare(
    `SELECT c.*, cs.permission, (SELECT COUNT(*) FROM documents WHERE case_id=c.id) as document_count,
     (SELECT COUNT(*) FROM analysis_results WHERE case_id=c.id) as result_count
     FROM case_shares cs JOIN cases c ON cs.case_id=c.id WHERE cs.user_id=? AND c.status!='deleted' ORDER BY c.updated_at DESC`
  ).bind(session.userId).all();
  return c.json({ owned: owned.results, shared: shared.results });
});

// POST /api/cases
app.post('/api/cases', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const { name, description } = await c.req.json();
  if (!name) return c.json({ error: 'Nombre requerido' }, 400);
  const id = crypto.randomUUID();
  await c.env.DB.prepare('INSERT INTO cases (id,owner_id,name,description) VALUES (?,?,?,?)')
    .bind(id, session.userId, sanitize(name), sanitize(description || '')).run();
  await logAudit(c.env.DB, session.userId, 'case_create', 'case', id);
  return c.json({ id, name, description, status: 'active' });
});

// GET /api/cases/:id
app.get('/api/cases/:id', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const caseData = await c.env.DB.prepare('SELECT * FROM cases WHERE id=?').bind(c.req.param('id')).first();
  if (!caseData) return c.json({ error: 'Caso no encontrado' }, 404);
  // Check access
  if (caseData.owner_id !== session.userId) {
    const share = await c.env.DB.prepare('SELECT permission FROM case_shares WHERE case_id=? AND user_id=?')
      .bind(c.req.param('id'), session.userId).first();
    if (!share) return c.json({ error: 'Sin acceso' }, 403);
  }
  return c.json(caseData);
});

// PATCH /api/cases/:id
app.patch('/api/cases/:id', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const body = await c.req.json();
  const caseData = await c.env.DB.prepare('SELECT owner_id FROM cases WHERE id=?').bind(c.req.param('id')).first();
  if (!caseData || caseData.owner_id !== session.userId) return c.json({ error: 'Sin permiso' }, 403);
  const { name, description, status, config_json } = body;
  await c.env.DB.prepare('UPDATE cases SET name=COALESCE(?,name), description=COALESCE(?,description), status=COALESCE(?,status), config_json=COALESCE(?,config_json), updated_at=datetime("now") WHERE id=?')
    .bind(name ? sanitize(name) : null, description !== undefined ? sanitize(description) : null, status || null, config_json || null, c.req.param('id')).run();
  return c.json({ ok: true });
});

// DELETE /api/cases/:id
app.delete('/api/cases/:id', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  await c.env.DB.prepare('UPDATE cases SET status="deleted", updated_at=datetime("now") WHERE id=? AND owner_id=?')
    .bind(c.req.param('id'), session.userId).run();
  await logAudit(c.env.DB, session.userId, 'case_delete', 'case', c.req.param('id'));
  return c.json({ ok: true });
});

// POST /api/cases/:id/share
app.post('/api/cases/:id/share', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const { email, permission } = await c.req.json();
  const caseData = await c.env.DB.prepare('SELECT owner_id FROM cases WHERE id=?').bind(c.req.param('id')).first();
  if (!caseData || caseData.owner_id !== session.userId) return c.json({ error: 'Sin permiso' }, 403);
  const targetUser = await c.env.DB.prepare('SELECT id FROM users WHERE email=?').bind(email.toLowerCase()).first();
  if (!targetUser) return c.json({ error: 'Usuario no encontrado' }, 404);
  const shareId = crypto.randomUUID();
  await c.env.DB.prepare('INSERT OR REPLACE INTO case_shares (id,case_id,user_id,permission,shared_by) VALUES (?,?,?,?,?)')
    .bind(shareId, c.req.param('id'), targetUser.id, permission || 'read', session.userId).run();
  await logAudit(c.env.DB, session.userId, 'case_share', 'case', c.req.param('id'), { shared_with: email });
  return c.json({ ok: true });
});

// ============================================================
// DOCUMENTS ROUTES
// ============================================================

// GET /api/cases/:id/documents
app.get('/api/cases/:id/documents', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const docs = await c.env.DB.prepare('SELECT * FROM documents WHERE case_id=? ORDER BY created_at DESC').bind(c.req.param('id')).all();
  return c.json(docs.results);
});

// POST /api/cases/:id/documents
app.post('/api/cases/:id/documents', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const formData = await c.req.formData();
  const file = formData.get('file') as File;
  if (!file) return c.json({ error: 'Archivo requerido' }, 400);
  if (file.size > 50 * 1024 * 1024) return c.json({ error: 'Máximo 50 MB por archivo' }, 400);
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const typeMap: Record<string, string> = { pdf: 'pdf', txt: 'txt', png: 'img', jpg: 'img', jpeg: 'img', tiff: 'img', tif: 'img', bmp: 'img', webp: 'img', docx: 'docx' };
  const fileType = typeMap[ext];
  if (!fileType) return c.json({ error: 'Tipo de archivo no soportado' }, 400);
  const docId = crypto.randomUUID();
  const r2Key = `cases/${c.req.param('id')}/${docId}/${file.name}`;
  await c.env.R2.put(r2Key, await file.arrayBuffer());
  await c.env.DB.prepare('INSERT INTO documents (id,case_id,filename,file_type,file_size,r2_key) VALUES (?,?,?,?,?,?)')
    .bind(docId, c.req.param('id'), sanitize(file.name), fileType, file.size, r2Key).run();
  await logAudit(c.env.DB, session.userId, 'document_upload', 'document', docId, { case_id: c.req.param('id'), filename: file.name });
  return c.json({ id: docId, filename: file.name, file_type: fileType, file_size: file.size, status: 'pending' });
});

// ============================================================
// ORACLE ROUTE
// ============================================================

app.post('/api/cases/:id/oracle', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const { question } = await c.req.json();
  if (!question) return c.json({ error: 'Pregunta requerida' }, 400);
  // TODO: integrate with actual LLM
  return c.json({ answer: 'Funcionalidad del oráculo pendiente de integración con el motor LLM.', sources: 'N/A' });
});

// ============================================================
// ANALYZE ROUTE
// ============================================================

app.post('/api/cases/:id/analyze', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  // TODO: trigger analysis pipeline
  await logAudit(c.env.DB, session.userId, 'analyze_start', 'case', c.req.param('id'));
  return c.json({ ok: true, message: 'Análisis encolado' });
});

// ============================================================
// SUBSCRIPTION ROUTES
// ============================================================

// POST /api/subscribe/confirm — Register approved transaction
app.post('/api/subscribe/confirm', async (c) => {
  try {
    const session = await requireAuth(c);
    if (session instanceof Response) return session;

    const { plan, tx_id, source } = await c.req.json();
    if (!plan || !source) return c.json({ error: 'Plan y source requeridos' }, 400);

    const validPlans: Record<string, number> = { starter: 20, professional: 99, business: 249, enterprise: 500 };
    if (!validPlans[plan]) return c.json({ error: 'Plan inválido' }, 400);

    // Generate transaction hash
    const txData = `${session.userId}:${plan}:${tx_id}:${Date.now()}`;
    const txHash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(txData));
    const hashHex = Array.from(new Uint8Array(txHash)).map(b => b.toString(16).padStart(2, '0')).join('');

    // Check if this transaction was already registered
    const existing = await c.env.DB.prepare('SELECT id FROM transactions WHERE tx_hash=?').bind(hashHex).first();
    if (existing) return c.json({ ok: true, message: 'Transacción ya registrada' });

    const txId = crypto.randomUUID();
    const subId = crypto.randomUUID();
    const now = new Date();
    const expires = new Date(now);
    expires.setMonth(expires.getMonth() + 1);

    // Create subscription
    await c.env.DB.prepare(
      'INSERT OR REPLACE INTO subscriptions (id,user_id,plan,status,started_at,expires_at) VALUES (?,?,?,?,?,?)'
    ).bind(subId, session.userId, plan, 'active', now.toISOString(), expires.toISOString()).run();

    // Create transaction record
    await c.env.DB.prepare(
      'INSERT INTO transactions (id,user_id,subscription_id,plan,amount,provider,provider_tx_id,tx_hash,status,ip_address,user_agent,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)'
    ).bind(txId, session.userId, subId, plan, validPlans[plan], 'clip', tx_id || null, hashHex, 'approved', c.req.header('CF-Connecting-IP'), c.req.header('User-Agent'), now.toISOString()).run();

    // Create onboarding record
    await c.env.DB.prepare(
      'INSERT OR IGNORE INTO onboarding (user_id,tour_started_at) VALUES (?,?)'
    ).bind(session.userId, now.toISOString()).run();

    await logAudit(c.env.DB, session.userId, 'subscription_activated', 'subscription', subId, { plan, tx_id, hash: hashHex });

    return c.json({ ok: true, subscription_id: subId, expires_at: expires.toISOString(), tx_hash: hashHex });
  } catch (err: any) {
    console.error('Subscribe confirm error:', err);
    return c.json({ error: 'Error interno', detail: err.message }, 500);
  }
});

// POST /api/subscribe/declined — Register declined transaction
app.post('/api/subscribe/declined', async (c) => {
  try {
    const session = await requireAuth(c);
    if (session instanceof Response) return session;

    const { plan, code, reason } = await c.req.json();
    const txId = crypto.randomUUID();
    const txData = `${session.userId}:${plan}:declined:${Date.now()}`;
    const txHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(txData)))).map(b => b.toString(16).padStart(2, '0')).join('');

    await c.env.DB.prepare(
      'INSERT INTO transactions (id,user_id,plan,amount,provider,tx_hash,status,ip_address,user_agent) VALUES (?,?,?,?,?,?,?,?,?)'
    ).bind(txId, session.userId, plan || 'unknown', 0, 'clip', txHash, 'declined', c.req.header('CF-Connecting-IP'), c.req.header('User-Agent')).run();

    await logAudit(c.env.DB, session.userId, 'payment_declined', 'transaction', txId, { plan, code, reason });

    return c.json({ ok: true });
  } catch (err: any) {
    return c.json({ error: 'Error interno', detail: err.message }, 500);
  }
});

// GET /api/subscribe/status — Check subscription status
app.get('/api/subscribe/status', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;

  const sub = await c.env.DB.prepare(
    'SELECT * FROM subscriptions WHERE user_id=? AND status="active" ORDER BY expires_at DESC LIMIT 1'
  ).bind(session.userId).first();

  if (!sub) return c.json({ has_subscription: false, plan: null, expires_at: null });

  const expired = new Date(sub.expires_at as string) < new Date();
  if (expired) {
    await c.env.DB.prepare('UPDATE subscriptions SET status="expired" WHERE id=?').bind(sub.id).run();
    return c.json({ has_subscription: false, plan: sub.plan, expired: true, expires_at: sub.expires_at });
  }

  return c.json({ has_subscription: true, plan: sub.plan, expires_at: sub.expires_at, started_at: sub.started_at });
});

// POST /api/onboarding/complete — Mark tour as completed
app.post('/api/onboarding/complete', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  await c.env.DB.prepare('UPDATE onboarding SET tour_completed=1, tour_completed_at=datetime("now") WHERE user_id=?').bind(session.userId).run();
  return c.json({ ok: true });
});

// GET /api/onboarding/status — Check if tour was completed
app.get('/api/onboarding/status', async (c) => {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;
  const ob = await c.env.DB.prepare('SELECT tour_completed FROM onboarding WHERE user_id=?').bind(session.userId).first();
  return c.json({ completed: ob?.tour_completed === 1 });
});

// POST /api/contact — Contact form
app.post('/api/contact', async (c) => {
  const { name, email, subject, message } = await c.req.json();
  if (!name || !email || !message) return c.json({ error: 'Campos requeridos' }, 400);
  await logAudit(c.env.DB, null, 'contact_form', null, null, { name, email, subject, message: message.substring(0, 500) }, c.req.header('CF-Connecting-IP'));
  return c.json({ ok: true });
});

// ============================================================
// SUBSCRIPTION MIDDLEWARE — Block access without active subscription
// ============================================================

async function requireSubscription(c: any) {
  const session = await requireAuth(c);
  if (session instanceof Response) return session;

  // Admin bypass
  const user = await c.env.DB.prepare('SELECT role FROM users WHERE id=?').bind(session.userId).first();
  if (user?.role === 'admin') return session;

  const sub = await c.env.DB.prepare(
    'SELECT expires_at FROM subscriptions WHERE user_id=? AND status="active" ORDER BY expires_at DESC LIMIT 1'
  ).bind(session.userId).first();

  if (!sub) return c.json({ error: 'Suscripción requerida', code: 'NO_SUBSCRIPTION' }, 403);

  if (new Date(sub.expires_at as string) < new Date()) {
    await c.env.DB.prepare('UPDATE subscriptions SET status="expired" WHERE user_id=? AND status="active"').bind(session.userId).run();
    return c.json({ error: 'Suscripción expirada', code: 'SUBSCRIPTION_EXPIRED' }, 403);
  }

  return session;
}

// ============================================================
// HEALTH
// ============================================================

app.get('/api/health', (c) => c.json({ ok: true, env: c.env.ENVIRONMENT }));

// Global error handler
app.onError((err, c) => {
  console.error('Worker error:', err);
  return c.json({ error: 'Internal Server Error', message: err.message }, 500);
});

// ============================================================
// FALLBACK: serve static assets
// ============================================================

app.all('*', async (c) => {
  return c.json({ error: 'Not found' }, 404);
});

export default app;
