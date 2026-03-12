# Auth Skill Pack

**Applies when:** The task involves authentication, authorization, login/signup
flows, session management, token handling, OAuth, permissions, RBAC, or
integration with auth providers (Supabase Auth, NextAuth, Clerk, Auth0, Lucia).

---

## 1. Auth Architecture Principles

Never build authentication from scratch. The attack surface is enormous —
timing attacks, session fixation, token leakage, CSRF, account enumeration,
credential stuffing. Use a proven library: Supabase Auth, NextAuth, Clerk,
Lucia, or Auth0. Your job is to integrate it correctly, not reinvent it.

Auth state is server-authoritative. The client displays what the server says
is true. A client-side `if (!user) return <Login />` is UX, not security —
the server must reject unauthorized requests independently.

Defense in depth: every request must pass at least two gates:
1. **Route-level**: middleware or API route guard verifies the token/session
2. **Data-level**: RLS policies or equivalent ensure the user can only access
   their own data

Never rely on a single layer. If middleware has a bug, RLS catches it. If RLS
has a gap, middleware catches it.

Every authenticated endpoint must verify the token server-side on every request.
Do not cache auth decisions beyond the token's TTL. A revoked user with a cached
"authorized" decision is a breach.

## 2. Session and Token Management

Session cookies must be set with these flags — no exceptions:

```typescript
// Next.js API route example
res.setHeader('Set-Cookie', serialize('session', token, {
  httpOnly: true,    // JavaScript cannot read it (XSS protection)
  secure: true,      // HTTPS only (prevents network sniffing)
  sameSite: 'lax',   // CSRF protection (use 'strict' for sensitive ops)
  path: '/',
  maxAge: 60 * 60 * 24 * 7, // 7 days
}));
```

JWT access tokens: 15-minute TTL maximum. Refresh tokens: 7–30 days, stored
as an HttpOnly cookie — never in localStorage. XSS can read localStorage; it
cannot read HttpOnly cookies.

Token rotation: when a refresh token is used, invalidate it immediately and
issue a new refresh token alongside the new access token. If a refresh token
is used twice, assume it was stolen and invalidate all sessions for that user.

On logout, invalidation must happen server-side:
1. Delete the server-side session or add the refresh token to a deny list
2. Clear cookies with `Set-Cookie: session=; Max-Age=0; HttpOnly; Secure`
3. Client-side `localStorage.clear()` alone is not a logout — the token is
   still valid until it expires

## 3. Password Handling

Never store passwords in plain text, MD5, or SHA-1. Use bcrypt with cost
factor 12+ or Argon2id. These are intentionally slow algorithms that make
brute force infeasible:

```typescript
import bcrypt from 'bcryptjs';

// Hashing (on registration)
const hash = await bcrypt.hash(password, 12);

// Verification (on login)
const valid = await bcrypt.compare(submittedPassword, storedHash);
```

Password requirements that actually matter:
- Minimum 12 characters (length is the strongest factor)
- Check against the HaveIBeenPwned API (compromised password database)
- Do NOT require uppercase/special characters — this pushes users toward
  predictable patterns like `Password1!`

Password reset flow:
1. Generate a cryptographically random token (`crypto.randomBytes(32)`)
2. Hash the token and store it with a 1-hour expiry
3. Send the unhashed token to the user's verified email
4. On reset: verify token, hash new password, invalidate ALL existing sessions
5. The token is single-use — delete it after successful reset

Rate limit login attempts: 5 failures within 15 minutes triggers a lockout.
Apply rate limiting on both IP address AND account (prevents distributed
brute force and targeted account attacks).

## 4. Supabase Auth Patterns

Use `supabase.auth.getUser()` on the server, not `getSession()`. The
`getSession()` method trusts the client-supplied JWT without re-verifying
it against Supabase — a tampered JWT will pass. `getUser()` makes a server
call that validates the token:

```typescript
// WRONG — trusts client JWT, can be tampered
const { data: { session } } = await supabase.auth.getSession();

// CORRECT — validates against Supabase servers
const { data: { user }, error } = await supabase.auth.getUser();
if (error || !user) {
  redirect('/login');
}
```

Enable email confirmation before allowing login in Supabase dashboard settings.
Without this, anyone can sign up with any email and immediately access the app.

The service role key (`SUPABASE_SERVICE_ROLE_KEY`) bypasses RLS entirely. It
must never appear in client-side code. Never prefix it with `NEXT_PUBLIC_` in
Next.js. Use it only in server-side API routes and background jobs.

Magic links and OTP: set expiry to 10 minutes in Supabase dashboard. The
default is often too generous.

Auth state in Next.js with Supabase: use middleware to check the session and
redirect unauthenticated users before the page renders. Do not rely on
client-side checks that flash the protected content before redirecting:

```typescript
// middleware.ts
import { createMiddlewareClient } from '@supabase/auth-helpers-nextjs';
import { NextResponse } from 'next/server';

export async function middleware(req) {
  const res = NextResponse.next();
  const supabase = createMiddlewareClient({ req, res });
  const { data: { session } } = await supabase.auth.getSession();

  if (!session && req.nextUrl.pathname.startsWith('/dashboard')) {
    return NextResponse.redirect(new URL('/login', req.url));
  }
  return res;
}
```

## 5. NextAuth Patterns

Prefer database sessions over JWT sessions when possible. Database sessions
allow server-side invalidation — if a user is compromised, you can delete
their session row immediately. JWT sessions remain valid until expiry.

Always set `NEXTAUTH_SECRET` from an environment variable. Generate it with
`openssl rand -base64 32`. Never hardcode it:

```typescript
// auth.ts
export const authOptions: NextAuthOptions = {
  secret: process.env.NEXTAUTH_SECRET,
  session: { strategy: 'database' },
  // ...
};
```

Restrict OAuth callback URLs in every provider's settings (Google, GitHub, etc.)
to your exact domain. Open callback URLs enable token theft via redirect.

Extend the session type in TypeScript properly — never use `session.user as any`:

```typescript
// types/next-auth.d.ts
declare module 'next-auth' {
  interface Session {
    user: {
      id: string;
      role: string;
      email: string;
    };
  }
}
```

Use `getServerSession(authOptions)` in Server Components and API routes. Use
`useSession()` from `next-auth/react` in Client Components. Never import
`getSession()` from `next-auth/react` for server-side use — it makes an
unnecessary HTTP round-trip.

## 6. Authorization (Not Just Authentication)

Authentication answers "who are you?" Authorization answers "what can you do?"
They are separate concerns — never conflate them.

RBAC implementation: store roles in the database, check them server-side. Never
trust a `role` claim from a JWT or client request without server verification:

```typescript
// Centralized permission check
function can(user: User, action: string, resource: string): boolean {
  const permissions = ROLE_PERMISSIONS[user.role];
  if (!permissions) return false;
  return permissions.includes(`${action}:${resource}`);
}

// Usage in API route
if (!can(user, 'delete', 'posts')) {
  return new Response('Forbidden', { status: 403 });
}
```

Centralize permission logic in a single `can(user, action, resource)` function.
Scattered `if (user.role === 'admin')` checks throughout the codebase are
unmaintainable and will drift out of sync.

Admin routes need two locks: middleware guard (checks role before the handler
runs) AND RLS policy (prevents data access even if middleware is bypassed).

Audit logging: record who performed what action on which resource, with a
timestamp. This is not optional for any operation that modifies user data,
changes permissions, or accesses sensitive records.

## 7. Common Attack Vectors to Prevent

**CSRF (Cross-Site Request Forgery):** Use `SameSite=Lax` cookies as the
baseline. For state-changing forms, use a CSRF token or double-submit cookie
pattern. Next.js Server Actions include CSRF protection automatically — prefer
them over raw API calls for form submissions.

**XSS (Cross-Site Scripting):** Never render user input as raw HTML. React
escapes by default, but `dangerouslySetInnerHTML` bypasses this — the name
is a warning. If you must render HTML, sanitize with DOMPurify first.

**Open redirect after login:** Never redirect to a URL taken from query
parameters (`?redirect=/`) without validating it against an allowlist of your
own domains. An attacker can craft a login link that redirects to a phishing
page after successful authentication:

```typescript
// WRONG — open redirect
const redirect = searchParams.get('redirect') || '/dashboard';
router.push(redirect); // could be https://evil.com

// CORRECT — validate against allowlist
const redirect = searchParams.get('redirect') || '/dashboard';
const allowed = redirect.startsWith('/') && !redirect.startsWith('//');
router.push(allowed ? redirect : '/dashboard');
```

**Account enumeration:** Return the same error message for "email not found"
and "wrong password" — both should say "Invalid credentials." Use constant-time
comparison for password checks (bcrypt does this by default).

**Brute force:** Rate limit all auth endpoints, not just login. Include signup
(prevents mass account creation), password reset (prevents email flooding), and
email verification (prevents token guessing).

## 8. Environment Variables and Secrets

Generate auth secrets with sufficient entropy:

```bash
openssl rand -base64 32
```

Minimum 32 bytes (256 bits) for any secret used in signing or encryption.

Never commit secrets to version control. Check your history:
```bash
git log --all --full-history -p -- '.env*' '*.env'
```
If a secret has ever touched git, rotate it immediately — even if you
force-pushed to remove the commit, it may exist in forks, reflog, or backups.

Use separate secrets per environment. The same `NEXTAUTH_SECRET` in dev and
production means a dev leak compromises production sessions.

`NEXTAUTH_URL` must match the exact deployment URL (including protocol and
port). Auth callbacks will break silently if this is wrong.

## 9. Testing Auth

Test the unhappy paths — they are where security lives:
- Expired token → should return 401, not cached data
- Invalid/malformed token → should return 401, not crash
- Missing token → should return 401, not a default user
- Wrong role → should return 403, not 200 with hidden UI elements
- Revoked session → should return 401 on next request

Integration tests should use real tokens from a test user, not mocked auth:

```typescript
// Test helper: get a real session token
async function getTestUserToken(): Promise<string> {
  const { data } = await supabase.auth.signInWithPassword({
    email: 'test@example.com',
    password: process.env.TEST_USER_PASSWORD!,
  });
  return data.session!.access_token;
}

// Integration test
const token = await getTestUserToken();
const res = await fetch('/api/posts', {
  headers: { Authorization: `Bearer ${token}` },
});
expect(res.status).toBe(200);
```

RLS tests: verify with actual user JWTs that user A cannot read user B's data.
Do not test RLS with the service role — it bypasses all policies.

Test middleware redirects with actual HTTP requests, not by mocking the
middleware function.

## 10. Anti-Patterns (Never Do These)

- **Storing JWT in localStorage.** XSS can read it. Use HttpOnly cookies.
- **Using `role` from JWT payload without server verification.** JWTs can
  be tampered with if the secret is weak or the verification is skipped.
- **Trusting client-supplied user IDs in API routes.** Always derive the
  user ID from the verified token: `auth.uid()`, `session.user.id`.
- **Same session secret across environments.** A dev leak compromises prod.
- **HTTP (not HTTPS) for any auth endpoint.** Tokens sent over HTTP are
  visible to anyone on the network.
- **Logging auth tokens or passwords** — even in development. Logs get
  shipped to observability platforms, searched by support teams, and
  retained for months.
- **Client-side-only route protection.** `if (!user) return <Login />`
  in a React component is UX, not auth. The server must independently
  reject unauthorized requests.
- **Skipping email verification.** Unverified emails enable account
  takeover via typo-squatting and make password reset flows dangerous.
- **Hardcoded secrets in source code.** Even in "temporary" dev code,
  secrets in source end up in git history forever.
- **Using `getSession()` instead of `getUser()` server-side in Supabase.**
  `getSession()` trusts the client-supplied JWT without re-verification.
