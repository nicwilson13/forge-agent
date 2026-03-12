# Deploy Skill Pack

**Applies when:** The task involves deployment, CI/CD, environment configuration,
Vercel setup, GitHub Actions, domain/DNS, monitoring, observability, rollback
strategy, or infrastructure for production readiness.

---

## 1. Vercel Deployment Principles

Connect your GitHub repo directly to Vercel. Every push to `main` deploys to
production. Every PR gets a preview deployment. This is the workflow — do not
fight it.

Never use `vercel --prod` from the CLI in a CI script. CLI deploys bypass
preview checks and the Git-based deployment pipeline. The Git integration is
the source of truth.

Project settings: set the framework preset (Next.js), root directory if you are
in a monorepo, and build command explicitly. Do not rely on auto-detection for
production. Auto-detection is fine for initial setup; explicit config prevents
surprises after a framework update.

Vercel regions: deploy to the region closest to your database. If your Supabase
instance is in `us-east-1`, set Vercel's function region to `iad1` (US East).
Every 100ms of database round-trip latency adds directly to your response time.

## 2. Environment Variables

Vercel has three environments: Production, Preview, Development. Set variables
per environment — never use the same Stripe key or Supabase service role key
across Preview and Production.

Naming conventions in Next.js:
- `NEXT_PUBLIC_*` — bundled into the client JavaScript. Safe to expose to the
  browser. Never put secrets here. If it starts with `NEXT_PUBLIC_`, assume
  the entire world can read it.
- Everything else — server-only. Only available in API routes, Server Components,
  and middleware. Verify with `typeof window === 'undefined'` if in doubt.

Required variables for a Next.js + Supabase + Stripe project:

```bash
# Client-safe (NEXT_PUBLIC_)
NEXT_PUBLIC_SUPABASE_URL=https://xyz.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_live_...
NEXT_PUBLIC_URL=https://yourdomain.com

# Server-only (NEVER prefix with NEXT_PUBLIC_)
SUPABASE_SERVICE_ROLE_KEY=eyJ...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
NEXTAUTH_SECRET=<openssl rand -base64 32>
NEXTAUTH_URL=https://yourdomain.com  # must match deployment URL exactly
```

Commit a `.env.example` with all variable names and no values. This documents
what the project needs. Never commit `.env.local` — it must be in `.gitignore`.

For local development, sync Vercel environment variables to your machine:
```bash
vercel env pull .env.local
```

## 3. Next.js Build Optimization

`next build` must pass with zero errors and zero TypeScript errors before any
deployment. Fix warnings that will become errors in the next Next.js version.

Image optimization: use `next/image` for every image. Configure `remotePatterns`
in `next.config.js` for external image sources:

```javascript
// next.config.js
module.exports = {
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: '*.supabase.co' },
      { protocol: 'https', hostname: 'avatars.githubusercontent.com' },
    ],
  },
};
```

Bundle analysis: run `ANALYZE=true next build` with `@next/bundle-analyzer`
before the first production deploy. Catch bloated bundles (moment.js, lodash
full import) before they ship.

Static vs dynamic: prefer static generation (`generateStaticParams`) for content
that does not change per-request. Dynamic rendering (`force-dynamic`) is for
personalized or real-time data. Static pages are served from the CDN edge with
zero compute cost.

Middleware runs on the edge — keep it lightweight. No heavy dependencies like
Prisma or the full Supabase client. Use JWT decode only (`jose` library) for
auth checks in middleware.

## 4. Vercel-Specific Next.js Patterns

API routes have timeout limits: 10 seconds on Hobby, 60 seconds on Pro. Set
`maxDuration` explicitly on any route that calls external services:

```typescript
// app/api/generate/route.ts
export const maxDuration = 30; // seconds

export async function POST(req: Request) {
  // Long-running AI generation, Stripe API call, etc.
}
```

Edge runtime for global low-latency routes:
```typescript
export const runtime = 'edge';
```
Not compatible with Node.js-only APIs (fs, crypto.subtle works but node:crypto
does not). Use for middleware, simple API routes, and redirects.

Streaming for AI routes: use `StreamingTextResponse` from the AI SDK or a raw
`Response` with a `ReadableStream`. Vercel handles chunked transfer encoding
correctly.

Cron jobs via `vercel.json`:
```json
{
  "crons": [{
    "path": "/api/cron/sync-usage",
    "schedule": "0 */6 * * *"
  }]
}
```
Secure the cron endpoint with a secret header — Vercel sends
`Authorization: Bearer <CRON_SECRET>`. Do not rely on the URL being unguessable.

ISR (Incremental Static Regeneration): set `revalidate` on pages with content
that changes infrequently. The page is served from cache and regenerated in the
background after the revalidation interval:

```typescript
// app/blog/[slug]/page.tsx
export const revalidate = 3600; // revalidate every hour
```

## 5. Preview Deployments

Every PR gets a unique preview URL (e.g., `your-app-git-feature-xyz.vercel.app`).
Use this for QA, design review, and stakeholder feedback before merging.

Preview deployments use the Preview environment variables. Set test/sandbox API
keys for all external services in the Preview environment — Stripe test keys,
Supabase test project, etc. A preview build with production keys is a liability.

Enable Vercel Comments for design review. PMs and designers can leave feedback
directly on the preview deployment without touching the codebase or opening
GitHub.

Branch protection in GitHub: require the Vercel deployment status check to pass
before merging. This ensures broken builds never reach `main`:
`Settings > Branches > Require status checks > Vercel`.

## 6. CI/CD with GitHub Actions

Run tests and type checks in CI before Vercel deploys. Vercel builds should be
the last step, not the only check.

Minimal CI workflow for a Next.js project:

```yaml
name: CI
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'pnpm'
      - run: pnpm install --frozen-lockfile
      - run: pnpm type-check
      - run: pnpm lint
      - run: pnpm test --passWithNoTests
```

`--frozen-lockfile` (pnpm) or `--ci` (npm): always use in CI. This ensures
the lockfile is respected exactly — no silent dependency updates. If the lockfile
is out of sync, CI fails, which is the correct behavior.

Secrets in GitHub Actions: `Settings > Secrets and variables > Actions`. Reference
as `${{ secrets.SECRET_NAME }}`. Never echo secrets in workflow logs. If a step
might print a secret, use `::add-mask::` to redact it.

Cache dependencies aggressively. The `actions/setup-node` action with `cache: 'pnpm'`
handles this. Cold installs on every push waste minutes.

## 7. Domain and DNS

Custom domain: add in Vercel Dashboard under your project's Domains settings.
Vercel provides the DNS records to add (CNAME or A record). SSL certificate
provisioning is automatic and free via Let's Encrypt.

DNS propagation: allow 24–48 hours for full global propagation after changing
records. Test with `dig yourdomain.com` to check the current state from your
machine.

`www` redirect: configure either `www.example.com` → `example.com` (apex) or
`example.com` → `www.example.com` as canonical. Add both domains in Vercel; it
handles the redirect automatically. Pick one and be consistent.

Email sending from your domain (Resend, SendGrid, Postmark): add SPF, DKIM, and
DMARC DNS records before sending any email. Without these, your emails land in
spam. Each provider gives you the exact records to add.

## 8. Monitoring and Observability

Vercel Analytics: enable for Core Web Vitals (LCP, FID, CLS) and page-level
performance metrics. Zero config for Next.js — install `@vercel/analytics` and
add the component:

```typescript
// app/layout.tsx
import { Analytics } from '@vercel/analytics/react';

export default function RootLayout({ children }) {
  return (
    <html>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
```

Sentry for error tracking: add `@sentry/nextjs`. Captures unhandled errors on
both client and server, with source maps for readable stack traces. Set
`SENTRY_DSN` and `SENTRY_AUTH_TOKEN` in Vercel environment variables.

Structured logging: use a JSON logger (pino is the best for Next.js — fast and
lightweight). Vercel's log drain can forward structured logs to Datadog, Logtail,
Axiom, or any log aggregator.

Uptime monitoring: set up an external health check (Better Uptime, Checkly,
UptimeRobot) that hits your `/api/health` endpoint every minute:

```typescript
// app/api/health/route.ts
export async function GET() {
  return Response.json({ status: 'ok', timestamp: Date.now() });
}
```

This endpoint should be fast and dependency-free. If you want to check database
connectivity, do it in a separate `/api/health/deep` endpoint.

## 9. Rollback Strategy

Vercel instant rollback: from the Deployments tab, click any previous deployment
and promote it to production. Takes approximately 10 seconds. Use this for
production incidents — do not try to fix forward under pressure.

Database migration safety: never deploy an irreversible schema migration and
application code in the same deployment. The safe sequence is:
1. Deploy the migration (backward-compatible: add column, add table)
2. Verify the migration succeeded
3. Deploy the application code that uses the new schema
4. (Later) Deploy a cleanup migration to drop the old column/table

Feature flags: use flags (LaunchDarkly, Statsig, Vercel Edge Config, or even a
simple database boolean) to decouple deployment from release. Deploy the code to
production with the feature disabled, then enable it when ready. This lets you
roll back a feature without rolling back a deployment.

Runbook: document rollback steps before going to production. Write them when you
are calm, because you will not think clearly during a 2am incident. Include:
what to roll back, how, who to notify, and how to verify the rollback succeeded.

## 10. Anti-Patterns (Never Do These)

- **Committing `.env.local`** or any file with real secrets. Check your
  `.gitignore` includes `.env*.local` before the first commit.
- **Using production API keys in preview environments.** Preview builds
  are semi-public (URL is guessable). Use test/sandbox keys.
- **Deploying without running `pnpm type-check` and `pnpm lint` first.**
  TypeScript errors caught after deploy are embarrassing and preventable.
- **Hardcoding `process.env.NODE_ENV === 'development'`** in components.
  Use proper environment variables for feature toggling.
- **Not setting `maxDuration` on API routes** that call external services.
  The default timeout will kill long-running requests silently.
- **Deploying database migrations and code atomically** when the migration
  is not backward-compatible. This creates a window where the old code
  runs against the new schema (or vice versa).
- **Using `// @ts-ignore` to make the build pass.** Fix the type error.
  Every `@ts-ignore` is a bug waiting to happen.
- **Skipping the lockfile in CI** (`pnpm install` without `--frozen-lockfile`).
  This means CI might install different versions than local, causing
  "works on my machine" failures.
- **No health check endpoint.** Without `/api/health`, you discover outages
  from user complaints instead of monitoring alerts.
- **No rollback plan.** If you cannot explain how to undo a deployment in
  under 60 seconds, you are not ready to deploy.
