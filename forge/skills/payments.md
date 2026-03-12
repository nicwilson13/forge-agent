# Payments Skill Pack

**Applies when:** The task involves payment processing, billing, subscriptions,
Stripe integration, checkout flows, invoicing, refunds, webhooks, pricing/plans,
or any financial transaction handling.

---

## 1. Stripe Architecture Principles

All payment logic runs server-side. The client never touches amounts, prices,
customer IDs, or payment intents directly. The client's job is to redirect to
Stripe Checkout or render Stripe Elements — nothing more.

Idempotency keys on every mutating Stripe API call. Without them, a network
retry can double-charge a customer. Use a deterministic key derived from your
domain objects, not a random UUID:

```typescript
const subscription = await stripe.subscriptions.create(
  {
    customer: stripeCustomerId,
    items: [{ price: priceId }],
  },
  {
    idempotencyKey: `sub-create-${userId}-${priceId}-${Date.now()}`,
  }
);
```

Webhook-driven state is the only truth. Your database reflects what Stripe says
happened, not what the client reported. The client's "payment succeeded" redirect
is optimistic UI — the webhook is the confirmation. Never grant access based
solely on a redirect to your success URL.

Stripe keys: use `STRIPE_SECRET_KEY` for production, `STRIPE_TEST_SECRET_KEY`
for development. Never use production keys in dev. Use separate Stripe accounts
or at minimum separate API key pairs per environment. The publishable key
(`STRIPE_PUBLISHABLE_KEY`) is the only key that belongs on the client.

## 2. Stripe Products and Prices Setup

Products represent what you sell (e.g., "Pro Plan"). Prices represent how you
charge for it (e.g., $20/month, $200/year). One product can have multiple prices.

Never hardcode price IDs in your source code. Use environment variables so you
can have different prices in test vs. production:

```bash
# .env.local
STRIPE_PRICE_PRO_MONTHLY=price_test_abc123
STRIPE_PRICE_PRO_ANNUAL=price_test_def456
```

Create products and prices in the Stripe Dashboard, not via API. Dashboard-created
objects are visible and auditable by non-engineers (finance, product).

Metered billing: report usage at the end of the billing period with
`stripe.subscriptionItems.createUsageRecord()`. Do not report in real time —
batch usage records to avoid hitting Stripe rate limits and to simplify
reconciliation.

Free trials: set `trial_period_days` on the subscription, not on the price.
Always collect a payment method upfront during trial signup — this dramatically
reduces churn at trial end because Stripe can charge automatically when the
trial expires:

```typescript
const session = await stripe.checkout.sessions.create({
  mode: 'subscription',
  payment_method_collection: 'always', // collect card during trial
  subscription_data: {
    trial_period_days: 14,
  },
  line_items: [{ price: priceId, quantity: 1 }],
  success_url: `${baseUrl}/dashboard?session_id={CHECKOUT_SESSION_ID}`,
  cancel_url: `${baseUrl}/pricing`,
  client_reference_id: userId,
});
```

## 3. Checkout Flow

Use Stripe Checkout (hosted page) or Stripe Elements. Never build a custom card
input form without Elements — PCI compliance requires that card data flows
directly to Stripe's servers without touching yours.

Create the Checkout Session server-side and redirect the client to the URL.
Never expose session creation logic or parameters to the client:

```typescript
// API route: POST /api/checkout
export async function POST(req: Request) {
  const { priceId } = await req.json();
  const session = await supabase.auth.getUser();
  if (!session.data.user) return new Response('Unauthorized', { status: 401 });

  const checkoutSession = await stripe.checkout.sessions.create({
    mode: 'subscription',
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: `${process.env.NEXT_PUBLIC_URL}/dashboard?success=true`,
    cancel_url: `${process.env.NEXT_PUBLIC_URL}/pricing`,
    client_reference_id: session.data.user.id,
    customer_email: session.data.user.email,
  });

  return Response.json({ url: checkoutSession.url });
}
```

Always set `client_reference_id` to your internal user or order ID. This makes
webhook reconciliation reliable — you can find the user without an extra lookup.

After checkout: redirect to the success URL for UX, but never grant access based
on the redirect alone. Wait for the `checkout.session.completed` webhook to
confirm payment and update your database.

## 4. Webhook Handling (Most Critical)

Verify every webhook signature with `stripe.webhooks.constructEvent()`. An
unverified webhook endpoint is an open door for forged payment confirmations:

```typescript
// API route: POST /api/webhooks/stripe
export async function POST(req: Request) {
  const body = await req.text();
  const signature = req.headers.get('stripe-signature')!;

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(
      body,
      signature,
      process.env.STRIPE_WEBHOOK_SECRET!
    );
  } catch (err) {
    console.error('Webhook signature verification failed');
    return new Response('Invalid signature', { status: 400 });
  }

  // Process the event (see handler below)
  await handleStripeEvent(event);

  // Return 200 immediately — Stripe retries on non-200
  return new Response('OK', { status: 200 });
}
```

Make webhook handlers idempotent. Before processing, check if you've already
handled this event ID. Store processed event IDs in your database:

```typescript
async function handleStripeEvent(event: Stripe.Event) {
  // Idempotency check
  const existing = await db.query.stripeEvents.findFirst({
    where: eq(stripeEvents.eventId, event.id),
  });
  if (existing) return; // Already processed

  // Record the event
  await db.insert(stripeEvents).values({ eventId: event.id, type: event.type });

  // Handle by type
  switch (event.type) {
    case 'checkout.session.completed':
      await handleCheckoutCompleted(event.data.object);
      break;
    case 'customer.subscription.updated':
      await handleSubscriptionUpdated(event.data.object);
      break;
    case 'customer.subscription.deleted':
      await handleSubscriptionDeleted(event.data.object);
      break;
    case 'invoice.payment_failed':
      await handlePaymentFailed(event.data.object);
      break;
    case 'invoice.payment_succeeded':
      await handlePaymentSucceeded(event.data.object);
      break;
  }
}
```

Return 200 as fast as possible. Do heavy processing asynchronously (queue, background
job). Stripe retries with exponential backoff on timeouts and non-200 responses.

Critical events you must handle:
- `checkout.session.completed` — grant access, create user subscription record
- `customer.subscription.updated` — plan change, status change
- `customer.subscription.deleted` — revoke access
- `invoice.payment_failed` — begin dunning flow (notify user, show banner)
- `invoice.payment_succeeded` — extend access, clear dunning state
- `customer.subscription.trial_will_end` — send trial ending reminder (3 days before)

## 5. Subscription Management

Store these fields on the user or organization record, indexed for fast lookup:
- `stripe_customer_id` — links your user to Stripe
- `stripe_subscription_id` — the active subscription
- `stripe_price_id` — the current plan/price
- `subscription_status` — synced from webhooks (`active`, `trialing`, `past_due`, `canceled`)
- `current_period_end` — when the current billing period expires

Sync subscription status from webhooks, not from Stripe API calls on every
request. Calling `stripe.subscriptions.retrieve()` on every page load adds
latency and hits rate limits. Cache the status in your database, update it
when webhooks arrive.

Grace period on failed payments: give users 3–7 days before revoking access.
Stripe automatically retries failed payments (Smart Retries). During this
window, show a banner: "Your payment failed. Please update your payment method."
This is dunning — the process of recovering failed payments.

Proration on plan changes: Stripe calculates proration automatically. When a
user upgrades mid-cycle, they're charged the difference. When they downgrade,
they get a credit. Show the prorated amount before the user confirms the change
using `stripe.invoices.createPreview()`.

Cancel at period end is the default: `stripe.subscriptions.update(subId, { cancel_at_period_end: true })`.
The user keeps access until their paid period expires. Immediate cancel
(`stripe.subscriptions.cancel(subId)`) revokes access instantly and should
only be used when the user explicitly requests it.

## 6. Customer Portal

Use Stripe Customer Portal for self-service billing management. Do not build
your own plan change, payment method update, or cancellation UI — Stripe's
portal handles all of this and stays compliant automatically:

```typescript
// API route: POST /api/billing/portal
export async function POST(req: Request) {
  const user = await getAuthenticatedUser(req);
  const portalSession = await stripe.billingPortal.sessions.create({
    customer: user.stripeCustomerId,
    return_url: `${process.env.NEXT_PUBLIC_URL}/dashboard/settings`,
  });
  return Response.json({ url: portalSession.url });
}
```

Configure the portal in Stripe Dashboard: which plans are available for
upgrade/downgrade, whether cancellation is allowed, proration behavior,
and what information users can update.

## 7. Refunds and Disputes

Issue refunds via the Stripe API, not the dashboard, so your system has an
audit trail:

```typescript
const refund = await stripe.refunds.create({
  payment_intent: paymentIntentId,
  reason: 'requested_by_customer',
});

// Log to your database
await db.insert(refundLog).values({
  userId,
  paymentIntentId,
  refundId: refund.id,
  amount: refund.amount,
  reason: 'Customer requested cancellation',
  initiatedBy: adminUserId,
});
```

Log every refund with: who initiated it, when, the amount, and the reason.
This is essential for accounting, dispute resolution, and fraud detection.

Disputes (chargebacks): respond within Stripe's deadline (typically 7 days for
most networks). Submit evidence: delivery confirmation, terms of service
acceptance timestamp, communication logs, usage logs. Automate evidence
collection where possible.

Partial refunds: update the user's access or entitlements accordingly in
your database. A partial refund on a subscription does not automatically
change the subscription status.

## 8. PCI Compliance

Using Stripe Checkout or Stripe Elements qualifies you for SAQ A — the
simplest PCI compliance level. This means your servers never see, store,
or transmit card data. Maintain this by:

- Never building custom card input fields without Stripe Elements
- Never logging card numbers, CVVs, expiration dates, or full PANs
- Always loading Stripe.js from `js.stripe.com` — never self-host the library
- HTTPS on every page that interacts with Stripe — no exceptions
- Never storing raw card data in your database, logs, or error tracking

If you ever handle card data directly (outside Elements/Checkout), you move
to SAQ D — a 300+ question compliance questionnaire. Do not do this.

## 9. Testing Stripe

Use Stripe test mode for all development and testing. Test card numbers:
- `4242 4242 4242 4242` — successful payment
- `4000 0000 0000 0002` — card declined
- `4000 0025 0000 3155` — requires 3D Secure authentication
- `4000 0000 0000 9995` — insufficient funds
- Any future expiry date and any 3-digit CVC

Test webhooks locally with the Stripe CLI:
```bash
stripe listen --forward-to localhost:3000/api/webhooks/stripe
```
This gives you a temporary webhook signing secret for local development.

Test the complete flow end-to-end:
1. Create checkout session → redirect to Stripe
2. Complete payment with test card
3. Webhook fires → your handler processes it
4. Verify: user record updated, access granted, subscription active

Test failure flows explicitly:
- Payment declined at checkout
- Subscription lapses after failed retry
- Refund issued → access adjusted
- Webhook received with invalid signature → rejected

Use Stripe test clocks (`stripe.testHelpers.testClocks`) to simulate the
subscription lifecycle without waiting real time. Advance the clock to test
trial end, renewal, payment failure, and cancellation flows in seconds.

## 10. Anti-Patterns (Never Do These)

- **Processing payments client-side.** All charge creation, subscription
  management, and refunds must happen server-side.
- **Trusting client-reported payment success.** The redirect to your success
  URL is UX. The webhook is the truth. Never grant access from the redirect.
- **Missing idempotency keys.** Every `stripe.subscriptions.create()`,
  `stripe.charges.create()`, and `stripe.refunds.create()` needs one.
- **Not verifying webhook signatures.** An unverified webhook endpoint
  accepts forged payment confirmations.
- **Storing card numbers or CVVs.** Use Stripe's tokenization. If you
  ever touch raw card data, you've broken PCI compliance.
- **One Stripe account for dev and production.** Test data and production
  data must be completely separate.
- **Hardcoding price IDs in source code.** Use environment variables so
  test and production prices are different.
- **Granting permanent access without subscription status checks.** Cache
  subscription status in your DB (refreshed by webhooks) and check it on
  every authenticated request.
- **Building custom billing UI instead of Stripe Customer Portal.** The
  portal handles compliance, payment method updates, and plan changes.
- **Ignoring dunning.** Failed payments are recoverable revenue. Show
  banners, send emails, and let Stripe's Smart Retries do their work.
