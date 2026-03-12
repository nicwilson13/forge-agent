# UI Components Skill Pack

**Applies when:** The task involves building UI components — buttons, forms,
modals, tables, layouts, navigation, or any interactive interface element.
Also applies when working with shadcn/ui, Radix UI, Tailwind CSS, accessibility,
dark mode, or responsive design patterns.

This skill covers component architecture, accessibility, and patterns.
It complements the Frontend Design skill (which covers visual aesthetics).

---

## 1. shadcn/ui Principles

shadcn/ui components live in `components/ui/` — they are owned by you, not
hidden in `node_modules`. You can and should customize them directly.

Install components individually as needed:
```bash
npx shadcn@latest add button
npx shadcn@latest add dialog
npx shadcn@latest add form
```
Never install the entire library at once. Only add what you use.

Always use the `cn()` utility from `lib/utils.ts` for className merging.
Never concatenate classNames with template literals or string addition:

```typescript
import { cn } from '@/lib/utils';

// CORRECT
<div className={cn('p-4 rounded-lg', isActive && 'bg-primary', className)} />

// WRONG — breaks when classes conflict
<div className={`p-4 rounded-lg ${isActive ? 'bg-primary' : ''} ${className}`} />
```

Use `cva()` (class-variance-authority) for component variants. Define all
visual variants in the component file, not scattered across usage sites:

```typescript
import { cva, type VariantProps } from 'class-variance-authority';

const buttonVariants = cva(
  'inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline: 'border border-input bg-background hover:bg-accent',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 px-3 text-sm',
        lg: 'h-11 px-8 text-lg',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  }
);
```

Extend shadcn components by wrapping, not forking. Create a wrapper component
that adds project-specific behavior while preserving the original API:

```typescript
// components/ui/loading-button.tsx
import { Button, ButtonProps } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';

interface LoadingButtonProps extends ButtonProps {
  isLoading?: boolean;
}

export function LoadingButton({ isLoading, children, disabled, ...props }: LoadingButtonProps) {
  return (
    <Button disabled={disabled || isLoading} {...props}>
      {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
      {children}
    </Button>
  );
}
```

## 2. Tailwind Architecture

Define design tokens in `tailwind.config.ts`. Extend `colors`, `spacing`,
`fontSize` with project-specific values instead of using magic numbers:

```typescript
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        brand: { 50: '#f0f7ff', 500: '#3b82f6', 900: '#1e3a5f' },
      },
      spacing: { 18: '4.5rem', 88: '22rem' },
    },
  },
};
```

Use CSS variables for theming. shadcn/ui sets up `--background`, `--foreground`,
`--primary`, `--muted`, etc. in `globals.css`. Reference them in Tailwind as
`bg-background`, `text-foreground`, `bg-primary`.

`@apply` in CSS: use sparingly and only for truly repeated utility patterns.
Prefer component abstractions over `@apply` sprawl. If you find yourself
writing `@apply` more than 3 times for the same pattern, extract a component.

Responsive prefix order: write mobile-first, then layer breakpoint overrides:
```html
<!-- base (mobile) → sm → md → lg → xl -->
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
```

Dark mode: use the `dark:` prefix with the `class` strategy in Tailwind config.
Use `next-themes` for the Next.js dark mode toggle — it handles SSR hydration
correctly and prevents flash of wrong theme.

Avoid arbitrary values (`w-[347px]`, `mt-[13px]`) for anything that should be
systematic. If you need a custom value repeatedly, add it to the Tailwind config
as a design token. Arbitrary values are acceptable for one-off precise positioning.

## 3. Component Architecture

Single responsibility: one component does one thing. A `UserCard` renders user
data — it does not fetch it. A `LoginForm` handles login submission — it does
not manage auth state. Data fetching happens in the parent or via React Query.

Every component needs an explicit TypeScript props interface. Never use `any`.
For components wrapping native HTML elements, extend the element's attributes:

```typescript
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  description?: string;
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ title, description, className, children, ...props }, ref) => (
    <div ref={ref} className={cn('rounded-lg border p-6', className)} {...props}>
      <h3 className="font-semibold">{title}</h3>
      {description && <p className="text-muted-foreground">{description}</p>}
      {children}
    </div>
  )
);
Card.displayName = 'Card';
```

Use `React.forwardRef` for any component that wraps a native DOM element. This
is required for shadcn/ui compatibility, tooltip triggers, dropdown triggers,
and any component that needs to be used as a child of Radix UI primitives.

Composition over configuration: prefer multiple focused components over one
component with 20 props. Instead of `<Card variant="user" showAvatar showBio
showActions editMode />`, compose `<Card><UserAvatar /><UserBio /><CardActions /></Card>`.

Named exports for all components (`export function Button`). Default exports
only for Next.js pages and route files where the framework requires it.

## 4. Accessibility (Non-Negotiable)

Every interactive element must have: a visible focus ring, keyboard operability,
and an appropriate ARIA role or label. This is not optional.

shadcn/ui components are built on Radix UI, which provides accessibility
primitives (focus trapping, keyboard navigation, screen reader announcements).
Use them. Do not replace Radix Dialog with a custom `<div onClick>` implementation
— you will lose all the accessibility work Radix provides.

Color contrast: minimum 4.5:1 ratio for normal text, 3:1 for large text
(18px+ or 14px+ bold). Test with browser dev tools accessibility panel or
the axe browser extension.

Images: every `<img>` and `next/image` must have meaningful `alt` text that
describes the image's content or purpose. Decorative images use `alt=""` — not
a missing alt attribute, but an explicitly empty one.

Form accessibility is critical:
- Every input needs a visible `<label>` with `htmlFor` + `id` pairing
- Never use `placeholder` as the only label — it disappears when the user types
- Error messages: connect to the input with `aria-describedby`
- Required fields: use `aria-required="true"` or the `required` attribute

```typescript
<div>
  <Label htmlFor="email">Email</Label>
  <Input
    id="email"
    aria-describedby={errors.email ? 'email-error' : undefined}
    aria-invalid={!!errors.email}
  />
  {errors.email && (
    <p id="email-error" className="text-sm text-destructive" role="alert">
      {errors.email.message}
    </p>
  )}
</div>
```

Focus management: when a modal opens, focus the first interactive element inside
it. When it closes, return focus to the trigger element that opened it. Radix UI
handles this automatically — do not fight it with manual `autoFocus` hacks.

## 5. Form Patterns with React Hook Form

Use `react-hook-form` + `zod` for all forms. This is the standard stack:

```typescript
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';

const loginSchema = z.object({
  email: z.string().email('Invalid email address'),
  password: z.string().min(12, 'Password must be at least 12 characters'),
});

type LoginValues = z.infer<typeof loginSchema>;

function LoginForm({ onSubmit }: { onSubmit: (data: LoginValues) => Promise<void> }) {
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField control={form.control} name="email" render={({ field }) => (
          <FormItem>
            <FormLabel>Email</FormLabel>
            <FormControl><Input {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? 'Signing in...' : 'Sign In'}
        </Button>
      </form>
    </Form>
  );
}
```

Schema-first: define the Zod schema, infer the TypeScript type from it, and
pass the resolver to `useForm`. This colocates all validation logic in one place.

The shadcn/ui `<Form>` component wraps react-hook-form with accessible error
messaging out of the box. Use it instead of wiring up `aria-describedby` and
error messages manually.

react-hook-form is uncontrolled by default (more performant — fewer re-renders).
Use the `Controller` wrapper only for custom or third-party inputs that require
controlled behavior.

Always disable the submit button during submission and show a loading state.
Handle both success and error cases — a form that submits but shows no feedback
on failure is worse than no form at all.

## 6. Data Display Patterns

Tables: use shadcn/ui DataTable built on TanStack Table for sortable, filterable,
paginated data display. Never build table sorting/filtering/pagination from
scratch — TanStack Table handles edge cases (multi-sort, stable sort, cursor
pagination) that custom implementations miss.

Loading states: use skeleton components that match the shape of the content
they replace. A skeleton card should have the same height and layout as a real
card. Not a spinner centered on the page — that tells the user nothing about
what's loading.

Empty states: display a meaningful message with a clear call to action.
"No projects yet. Create your first project." — not just "No data" or a blank
white space.

Error states: show a friendly error message with a retry action. Log the actual
error to Sentry or the console for debugging. Never show raw error messages or
stack traces to users.

Pagination vs infinite scroll: prefer pagination for data that changes
frequently (admin tables, order lists). Use infinite scroll for content feeds
(social feeds, activity logs) where the user browses linearly.

## 7. State Management Patterns

Server state (data from APIs): use TanStack Query (React Query) or SWR. These
handle caching, refetching, loading/error states, and stale-while-revalidate.
Never use `useEffect` + `useState` + `fetch` for data fetching — this pattern
is error-prone (race conditions, missing cleanup, no caching):

```typescript
// CORRECT — TanStack Query
const { data: users, isLoading, error } = useQuery({
  queryKey: ['users'],
  queryFn: () => fetch('/api/users').then(r => r.json()),
});

// WRONG — manual useEffect
const [users, setUsers] = useState([]);
const [loading, setLoading] = useState(true);
useEffect(() => {
  fetch('/api/users').then(r => r.json()).then(setUsers).finally(() => setLoading(false));
}, []); // Missing error handling, race conditions, no caching
```

Client state (UI-only state): `useState` for simple toggles and local state.
`useReducer` for complex state with multiple related values. Zustand for global
client state that multiple components share. Context for values that rarely
change (theme, locale, auth user).

URL state: filter, sort, search, and pagination state belongs in the URL via
`useSearchParams`. This enables sharing links, browser back button, and
bookmarking specific views.

Derived state: compute from existing state during render. Never store a value
in state that can be calculated from other state — this creates sync bugs.

## 8. Performance Patterns

`React.memo`: wrap components that receive object or array props and re-render
frequently. Profile with React DevTools first — optimize only what the profiler
shows is slow, not what you guess might be slow.

`useMemo` / `useCallback`: use for expensive computations and stable callback
references passed to memoized children. Do not wrap every value — the overhead
of memoization can exceed the cost of re-computation for simple operations.

Code splitting with `next/dynamic` for heavy components:
```typescript
import dynamic from 'next/dynamic';

const Chart = dynamic(() => import('@/components/chart'), {
  loading: () => <Skeleton className="h-[300px] w-full" />,
  ssr: false, // Charts don't need server rendering
});
```
Use for chart libraries, rich text editors, map components, and anything over
50KB that is not needed on initial page load.

Image optimization: always use `next/image`. Set `priority` on above-the-fold
hero images. Always set explicit `width` and `height` to prevent layout shift.

Virtualization with `@tanstack/react-virtual` for lists exceeding 100 items.
The browser cannot efficiently render 10,000 DOM nodes — virtualize them so
only visible items exist in the DOM.

## 9. Component File Organization

```
components/
  ui/              # shadcn/ui base components (Button, Dialog, Input)
  layout/          # App shell: Header, Footer, Sidebar, PageWrapper
  features/        # Feature-specific: UserCard, OrderList, ProjectGrid
  forms/           # Form components: LoginForm, CheckoutForm, SettingsForm
  shared/          # Cross-feature reusables: LoadingButton, ConfirmDialog
```

Co-locate tests with components: `button.test.tsx` lives next to `button.tsx`.
Do not put all tests in a separate `__tests__/` directory tree.

Use barrel exports (`components/ui/index.ts`) for clean imports:
```typescript
export { Button } from './button';
export { Input } from './input';
export { Dialog } from './dialog';
```
But avoid barrel files that create circular dependencies — if two feature
directories import from each other's barrel, extract the shared component to
`shared/`.

## 10. Anti-Patterns (Never Do These)

- **Inline styles** (`style={{ marginTop: 13 }}`) for anything Tailwind can
  handle. Inline styles bypass the design system and cannot be responsive.
- **`useEffect` for data fetching.** Use TanStack Query or SWR. The useEffect
  pattern has race conditions, no caching, and no loading/error state management.
- **Storing server state in Redux or Zustand.** Server data belongs in a
  server-state library (React Query/SWR) that handles caching and invalidation.
- **`any` type in component props.** Define explicit TypeScript interfaces.
  `any` disables type checking — the whole point of TypeScript.
- **Array index as `key`** for dynamic lists where items can be reordered,
  added, or removed. Use a stable unique identifier (ID from database).
- **Click handlers on `<div>` or `<span>`** without `role="button"`,
  `tabIndex={0}`, and keyboard event handlers. Use a `<button>` instead.
- **Modals without Escape key support.** Radix UI Dialog handles this. If
  you build a custom modal, add `onKeyDown` for Escape.
- **Forms without validation or error states.** Every form needs schema
  validation (Zod) and visible error messages for every field.
- **Hard-coded color values** (`text-[#3b82f6]`) instead of design tokens
  (`text-primary`). Hard-coded values break when the theme changes.
- **Placeholder-only labels.** Placeholders disappear on input focus —
  users lose context about what the field expects. Always use a `<Label>`.
