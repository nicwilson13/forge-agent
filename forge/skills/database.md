# Database Skill Pack

**Applies when:** The task involves database work — schema design, migrations,
queries, ORM setup, seeding, indexing, or any interaction with PostgreSQL,
MySQL, SQLite, MongoDB, Redis, Supabase, or an ORM (Drizzle, Prisma, TypeORM,
Sequelize, Mongoose).

---

## 1. Schema Design Principles

Use UUIDs as primary keys, not auto-incrementing integers. Auto-increment leaks
row count, creates merge conflicts across environments, and breaks distributed
systems. Generate UUIDs at the application layer or use `gen_random_uuid()` in
Postgres.

Every table must have these three columns at minimum:

```sql
CREATE TABLE users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

For any user-owned data, use soft deletes with a `deleted_at` column instead of
hard deletes. This preserves audit trails, enables undo, and prevents cascading
data loss:

```sql
ALTER TABLE posts ADD COLUMN deleted_at TIMESTAMPTZ;
-- Queries should filter: WHERE deleted_at IS NULL
```

Timestamp rules:
- Always `TIMESTAMPTZ` (timezone-aware), never `TIMESTAMP` (ambiguous)
- Store everything in UTC; convert to local time in the application layer
- Name columns `created_at`, `updated_at`, `deleted_at`, `published_at` — not
  `createdAt` or `creation_date`

Column naming conventions:
- `snake_case` for all columns and tables — no exceptions
- Be descriptive: `subscription_expires_at` not `sub_exp`
- Booleans: always prefix with `is_` or `has_` (`is_active`, `has_verified_email`)
- Foreign keys: `<referenced_table_singular>_id` (e.g., `user_id`, `team_id`)

## 2. Migration Patterns

Every schema change must be a migration. Never hand-edit the database or modify
an existing migration file after it has been applied.

Migration filenames follow: `YYYYMMDDHHMMSS_descriptive_name.sql`
(e.g., `20240315143000_add_posts_table.sql`).

Every migration must be reversible — always include a DOWN migration:

```sql
-- UP
ALTER TABLE users ADD COLUMN bio TEXT;

-- DOWN
ALTER TABLE users DROP COLUMN bio;
```

Two-phase column drops are mandatory for production systems:
1. **Deploy 1:** Stop reading/writing the column in application code
2. **Deploy 2:** Drop the column in a migration

Never drop a column and remove its usage in the same deployment — this causes
downtime if the migration runs before the new code is fully deployed.

Test migrations against realistic data volumes. A migration that runs in 50ms on
10 rows can lock a table for minutes on 10M rows. Use `CREATE INDEX CONCURRENTLY`
for large tables in Postgres.

## 3. Indexing Strategy

Every foreign key column must have an index. Postgres does not auto-index foreign
keys (unlike MySQL). Missing FK indexes cause slow JOINs and cascade deletes.

Composite indexes: column order matters. Put the most selective column first (the
one that filters out the most rows). An index on `(user_id, created_at)` serves
queries filtering by `user_id` alone, but not queries filtering only by `created_at`.

Use partial indexes for soft-delete patterns to avoid indexing deleted rows:

```sql
CREATE INDEX idx_posts_active ON posts (user_id, created_at)
  WHERE deleted_at IS NULL;
```

Do not over-index. Every index slows down INSERT/UPDATE/DELETE operations and
consumes disk space. Before adding an index, check with `EXPLAIN ANALYZE` whether
the query actually needs it. If a table has more indexes than columns, something
is wrong.

For full-text search, use the database's native capabilities before reaching for
external tools:
- Postgres: `tsvector` + `tsquery` for structured FTS, `pg_trgm` for fuzzy/LIKE
- Only reach for Elasticsearch or Typesense when database FTS cannot meet latency
  requirements at your data volume

## 4. Row Level Security (RLS) — Supabase Specific

Enable RLS on every user-facing table immediately after creation. The default
without RLS is that any authenticated user can read/write all rows — this is a
security disaster waiting to happen.

Start with default deny — enable RLS with no policies, then add policies
explicitly:

```sql
ALTER TABLE posts ENABLE ROW LEVEL SECURITY;

-- Users can only read their own posts
CREATE POLICY "Users read own posts"
  ON posts FOR SELECT
  USING (auth.uid() = user_id);

-- Users can only insert posts as themselves
CREATE POLICY "Users insert own posts"
  ON posts FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Users can only update their own posts
CREATE POLICY "Users update own posts"
  ON posts FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
```

The service role bypasses RLS entirely. Use it only for admin operations and
background jobs — never expose it to client-side code. Document every place the
service role is used with a comment explaining why.

Test RLS policies with actual JWT tokens from different users. Do not rely on
testing with the service role and assuming policies work — verify that user A
cannot see user B's data.

## 5. Query Patterns

Never use `SELECT *` in production code. Name every column explicitly. `SELECT *`
breaks when columns are added/removed, fetches unnecessary data, and makes it
impossible to know what data the query actually needs.

Use parameterized queries everywhere — never interpolate values into SQL strings:

```typescript
// WRONG — SQL injection vulnerability
const result = await db.query(`SELECT * FROM users WHERE id = '${userId}'`);

// CORRECT — parameterized
const result = await db.query('SELECT id, name, email FROM users WHERE id = $1', [userId]);
```

N+1 query prevention: never query inside a loop. If you need related data for
multiple rows, use a JOIN or batch the IDs into a single query:

```typescript
// WRONG — N+1: one query per post
for (const post of posts) {
  const author = await db.query('SELECT name FROM users WHERE id = $1', [post.user_id]);
}

// CORRECT — single query with IN clause
const authorIds = posts.map(p => p.user_id);
const authors = await db.query('SELECT id, name FROM users WHERE id = ANY($1)', [authorIds]);
```

Pagination: use cursor-based (keyset) pagination for any table that could grow
large. OFFSET-based pagination gets slower as the page number increases because
the database must scan and discard all preceding rows:

```sql
-- WRONG — slow on page 1000
SELECT * FROM posts ORDER BY created_at DESC LIMIT 20 OFFSET 20000;

-- CORRECT — cursor-based, consistently fast
SELECT id, title, created_at FROM posts
  WHERE created_at < $1  -- cursor from previous page's last row
  ORDER BY created_at DESC
  LIMIT 20;
```

Use transactions for multi-table writes. If one operation fails, everything must
roll back — partial writes cause data corruption:

```typescript
await db.transaction(async (tx) => {
  await tx.insert(orders).values(order);
  await tx.update(inventory).set({ stock: sql`stock - ${quantity}` }).where(eq(inventory.id, itemId));
});
```

## 6. ORM-Specific Patterns

### Drizzle (preferred for Next.js + Supabase)

Define schema in `db/schema.ts` with explicit column types. Generate migrations
with `drizzle-kit generate` — never write migration SQL by hand when using Drizzle:

```typescript
import { pgTable, uuid, text, timestamp, boolean } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: text('email').notNull().unique(),
  name: text('name').notNull(),
  isActive: boolean('is_active').notNull().default(true),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});
```

Declare relations separately from the schema in a `.relations.ts` file — this
keeps the schema clean and the relations composable.

Always use `db.transaction()` for multi-table operations. Never rely on
sequential awaits without a transaction boundary.

### Prisma

Schema lives in `prisma/schema.prisma`, migrations via `prisma migrate dev`.

Always use `select` or `include` to avoid over-fetching — Prisma returns all
scalar fields by default:

```typescript
// WRONG — fetches every column
const user = await prisma.user.findUnique({ where: { id } });

// CORRECT — only what you need
const user = await prisma.user.findUnique({
  where: { id },
  select: { id: true, email: true, name: true },
});
```

Implement soft deletes via Prisma middleware rather than filtering `deleted_at`
in every query — this prevents accidental exposure of deleted records.

For serverless environments (Vercel, AWS Lambda), use connection pooling via
PgBouncer or Prisma Accelerate. Serverless functions open a new connection per
invocation — without pooling, you will exhaust the database connection limit.

## 7. Seeding and Test Data

Seed files must be idempotent — running them twice should not create duplicates
or fail:

```sql
INSERT INTO roles (id, name) VALUES
  ('admin', 'Administrator'),
  ('user', 'Regular User')
ON CONFLICT (id) DO NOTHING;
```

Use realistic data volumes in development. If production has 1M rows, seeding
5 rows will hide performance problems, missing indexes, and pagination bugs.
Use a factory pattern to generate test data at scale.

Use a factory pattern for test data instead of hardcoded fixtures. Factories are
composable and make tests self-documenting:

```typescript
function createUser(overrides?: Partial<User>): User {
  return {
    id: randomUUID(),
    email: `user-${randomUUID()}@test.com`,
    name: 'Test User',
    isActive: true,
    createdAt: new Date(),
    updatedAt: new Date(),
    ...overrides,
  };
}
```

Never seed production with development data. Seed scripts should check the
environment and refuse to run in production.

## 8. Common Anti-Patterns (Never Do These)

- **JSON blobs instead of columns.** If you query or filter by a field, it
  belongs in its own column. JSON columns are for truly unstructured data
  (user preferences, metadata) — not for structured domain data.
- **`varchar(255)` everywhere.** In Postgres, `TEXT` has identical performance
  to `VARCHAR(n)` with no length limit. Use `TEXT` for strings; only use
  `VARCHAR(n)` when a hard length constraint is a business rule.
- **Nullable foreign keys** when the relationship is required. If every post
  must have an author, `user_id` must be `NOT NULL`. Nullable FKs are only
  for genuinely optional relationships.
- **Missing `updated_at` triggers.** Adding an `updated_at` column without a
  trigger means it never updates — it becomes a second `created_at`. Always
  pair the column with a trigger or ORM hook.
- **N+1 queries.** Querying in a loop is the single most common database
  performance problem. Always join or batch.
- **Storing secrets in plaintext.** Passwords must be hashed (bcrypt/argon2).
  API tokens must be hashed (SHA-256 of the token stored, not the token itself).
  Never store reversible encrypted secrets in the database unless you have a
  proper key management system.
