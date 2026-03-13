# REQUIREMENTS.md

STACKLOCAL
Software Requirements Document

AUTONOMOUS AGENT SYSTEM SPECIFICATION

Version 1.0  //  March 2026
CONFIDENTIAL -- ENGINEERING REFERENCE

This document serves as the authoritative specification for autonomous development agents building the StackLocal platform. Every requirement is written to be machine-parseable, unambiguous, and testable.
 
1. System Overview
1.1 Purpose
This Software Requirements Document (SRD) defines every component, interface, data model, workflow, and constraint required to build the StackLocal platform. It is written specifically to serve as the primary guidance artifact for autonomous AI development agents. Every requirement is structured to be unambiguous, deterministic, and independently verifiable.
An autonomous agent reading this document should be able to build the complete system without additional context, clarification, or human intervention beyond the scope of what is defined here. Where ambiguity exists, the agent should escalate to the designated human reviewer before proceeding.
1.2 System Summary
StackLocal is an AI-native, agent-operated marketing platform that delivers productized local marketing services to trades and service businesses via monthly subscription. The system is composed of:
•	A central orchestration engine (Conductor) that manages all agent workflows, task scheduling, and escalation routing
•	Ten specialized AI agents, each responsible for a defined functional domain
•	A client-facing web portal for onboarding, reporting, communication, and asset management
•	An internal operations dashboard for human operators to monitor system health, review escalations, and manage client relationships
•	A suite of external integrations connecting to ad platforms, SEO tools, CRM systems, email providers, and social media APIs
•	A data layer supporting client knowledge bases, performance analytics, and agent learning
1.3 Architecture Pattern
StackLocal follows an event-driven microservices architecture with the following core principles:
•	Service isolation: Each agent operates as an independent service with its own task queue, state management, and API surface. Agents communicate exclusively through the event bus and Conductor's task management API.
•	Event sourcing: All state changes are captured as immutable events. The current state of any entity (client, campaign, task) can be reconstructed from its event history. This provides complete auditability and enables replay for debugging.
•	CQRS (Command Query Responsibility Segregation): Write operations (commands) and read operations (queries) use separate data paths. Commands go through the event bus; queries hit read-optimized views.
•	Idempotency: Every agent action and API call must be idempotent. If an agent crashes mid-task and restarts, re-executing the same task must produce the same result without side effects.
•	Graceful degradation: If any single agent or external service is unavailable, the system continues operating with reduced capability. No single point of failure should take down client-facing services.
1.4 Technology Decisions

Component	Technology	Rationale
Runtime	Node.js 20 LTS (primary), Python 3.12 (ML/data)	Node for API services and orchestration; Python for data science pipelines and LLM tooling
Framework	Next.js 14 (App Router)	Server components for portal, API routes for internal services, edge runtime for latency-sensitive endpoints
Database (primary)	PostgreSQL 16 via Supabase	ACID transactions, JSONB for flexible schemas, Row Level Security for multi-tenancy, real-time subscriptions
Database (cache)	Redis 7 (Upstash)	Task queue backing, session cache, rate limiting, pub/sub for real-time events
Event Bus	Redis Streams + BullMQ	Reliable event delivery, consumer groups for agent task distribution, dead letter queues for failed tasks
Object Storage	Supabase Storage (S3-compatible)	Client assets, generated content, reports, creative files
LLM Primary	Claude API (Anthropic) -- claude-sonnet-4-20250514	Primary reasoning engine for all agents. Sonnet for routine tasks; Opus for complex strategy and analysis
LLM Secondary	GPT-4o (OpenAI)	Fallback for Anthropic API outages. Secondary opinion for quality scoring
Image Generation	FLUX (via Replicate API)	Ad creative and social media visual generation
Deployment	Vercel (frontend + API routes), Railway (worker services)	Vercel for edge-optimized web serving; Railway for long-running agent workers
Monitoring	Axiom (logs), Checkly (uptime), Sentry (errors)	Unified observability stack with structured logging
CI/CD	GitHub Actions	Automated testing, linting, deployment pipelines
Auth	Supabase Auth + NextAuth.js	Multi-provider auth for client portal, JWT-based service-to-service auth for internal APIs
1.5 Repository Structure
The system is organized as a monorepo using Turborepo:
stacklocal/
  apps/
    portal/              # Next.js client-facing portal
    ops/                 # Next.js internal operations dashboard
    api/                 # Express.js API gateway
  packages/
    db/                  # Prisma schema, migrations, seed scripts
    agents/              # Shared agent framework and base classes
    agents-scout/        # Scout agent implementation
    agents-closer/       # Closer agent implementation
    agents-intake/       # Intake agent implementation
    agents-forge/        # Forge agent implementation
    agents-pixel/        # Pixel agent implementation
    agents-signal/       # Signal agent implementation
    agents-beacon/       # Beacon agent implementation
    agents-pulse/        # Pulse agent implementation
    agents-relay/        # Relay Agent implementation
    agents-conductor/    # Conductor orchestration engine
    shared/              # Shared types, utils, constants
    queue/               # BullMQ queue definitions and workers
    integrations/        # External API client libraries
    knowledge/           # Knowledge base management
    quality/             # Quality scoring engine
    ui/                  # Shared React component library
  infrastructure/
    docker/              # Docker configs for local dev
    scripts/             # Database scripts, deployment helpers
    monitoring/          # Alert definitions, dashboard configs
  docs/                  # Architecture decision records (ADRs)
1.6 Naming Conventions
All development agents must follow these naming conventions without exception:

Entity	Convention	Example
Database tables	snake_case, plural	client_accounts, agent_tasks
Database columns	snake_case	created_at, client_id, quality_score
API endpoints	kebab-case, RESTful	/api/v1/client-accounts/:id/campaigns
TypeScript interfaces	PascalCase, prefixed with I for interfaces	IClientAccount, IAgentTask
TypeScript types	PascalCase	TaskStatus, AgentType
TypeScript enums	PascalCase, UPPER_SNAKE values	AgentType.SCOUT, TaskStatus.COMPLETED
Environment variables	UPPER_SNAKE_CASE	ANTHROPIC_API_KEY, SUPABASE_URL
Event names	dot.separated.lowercase	task.completed, client.onboarded
Queue names	kebab-case	scout-prospecting, forge-content-generation
File names	kebab-case	client-account.service.ts, quality-scorer.ts
React components	PascalCase	ClientDashboard.tsx, CampaignCard.tsx
CSS/Tailwind	Tailwind utility classes only	No custom CSS files; all styling via Tailwind
 
2. Data Model
This section defines the complete database schema. All tables use UUID primary keys (generated via gen_random_uuid()), include created_at and updated_at timestamps (auto-managed), and implement soft deletion via a deleted_at nullable timestamp where specified. The schema is managed via Prisma ORM with PostgreSQL.
AGENT INSTRUCTION: When creating migrations, always generate both an up and down migration. Every migration must be reversible. Never use raw SQL for schema changes outside of Prisma migrations.
2.1 Core Entities
2.1.1 organizations
Top-level entity representing a StackLocal client business.
Column	Type	Constraints	Description
id	UUID	PK, DEFAULT gen_random_uuid()	Primary identifier
name	VARCHAR(255)	NOT NULL	Business name
slug	VARCHAR(100)	UNIQUE, NOT NULL	URL-safe identifier
industry	VARCHAR(100)	NOT NULL	Primary industry vertical (e.g., hvac, roofing, plumbing)
sub_industry	VARCHAR(100)	NULLABLE	Sub-vertical for refined targeting
subscription_tier	ENUM	NOT NULL, DEFAULT 'foundation'	foundation | growth | dominance
subscription_status	ENUM	NOT NULL, DEFAULT 'trialing'	trialing | active | paused | canceled | past_due
mrr_cents	INTEGER	NOT NULL, DEFAULT 0	Monthly recurring revenue in cents
onboarding_status	ENUM	NOT NULL, DEFAULT 'pending'	pending | in_progress | completed | stalled
onboarding_completed_at	TIMESTAMP	NULLABLE	When onboarding was marked complete
primary_contact_id	UUID	FK -> contacts.id, NULLABLE	Main point of contact
assigned_csm_id	UUID	FK -> internal_users.id, NULLABLE	Assigned Client Success Manager
timezone	VARCHAR(50)	NOT NULL, DEFAULT 'America/New_York'	Client timezone for scheduling
website_url	VARCHAR(500)	NULLABLE	Primary website
phone	VARCHAR(20)	NULLABLE	Primary phone number
address_line1	VARCHAR(255)	NULLABLE	Street address
address_city	VARCHAR(100)	NULLABLE	City
address_state	VARCHAR(50)	NULLABLE	State/province
address_zip	VARCHAR(20)	NULLABLE	ZIP/postal code
address_country	VARCHAR(2)	NOT NULL, DEFAULT 'US'	ISO 3166-1 alpha-2
metro_area	VARCHAR(100)	NULLABLE	Metro area for geographic targeting
service_radius_miles	INTEGER	NULLABLE	Service area radius
brand_guidelines	JSONB	DEFAULT '{}'	Structured brand guide (colors, fonts, tone, dos/donts)
competitor_ids	UUID[]	DEFAULT '{}'	Array of competitor organization IDs
metadata	JSONB	DEFAULT '{}'	Extensible metadata
churn_risk_score	DECIMAL(3,2)	DEFAULT 0.00	0.00-1.00 churn probability
health_score	DECIMAL(3,2)	DEFAULT 0.50	0.00-1.00 composite health score
stripe_customer_id	VARCHAR(255)	NULLABLE, UNIQUE	Stripe customer reference
created_at	TIMESTAMP	DEFAULT now()	Record creation
updated_at	TIMESTAMP	DEFAULT now()	Last update
deleted_at	TIMESTAMP	NULLABLE	Soft deletion marker
2.1.2 contacts
Individual people associated with an organization.
Column	Type	Constraints	Description
id	UUID	PK	Primary identifier
organization_id	UUID	FK -> organizations.id, NOT NULL	Parent organization
first_name	VARCHAR(100)	NOT NULL	First name
last_name	VARCHAR(100)	NOT NULL	Last name
email	VARCHAR(255)	NOT NULL	Email address
phone	VARCHAR(20)	NULLABLE	Phone number
role	VARCHAR(100)	NULLABLE	Role at the organization (e.g., Owner, Marketing Manager)
is_primary	BOOLEAN	DEFAULT false	Whether this is the primary contact
is_portal_user	BOOLEAN	DEFAULT false	Whether this contact has portal access
auth_user_id	UUID	FK -> auth.users.id, NULLABLE	Supabase auth reference
communication_preferences	JSONB	DEFAULT '{}'	Preferred channels, frequency, notification settings
created_at	TIMESTAMP	DEFAULT now()	
updated_at	TIMESTAMP	DEFAULT now()	
2.1.3 campaigns
Marketing campaigns managed for a client. A campaign is a container for related activities across channels.
Column	Type	Constraints	Description
id	UUID	PK	Primary identifier
organization_id	UUID	FK -> organizations.id, NOT NULL	Parent organization
name	VARCHAR(255)	NOT NULL	Campaign name
type	ENUM	NOT NULL	google_ads | meta_ads | seo | content | email | social | creator | multi_channel
status	ENUM	NOT NULL, DEFAULT 'draft'	draft | pending_approval | active | paused | completed | archived
objective	VARCHAR(500)	NULLABLE	Campaign objective description
budget_cents_monthly	INTEGER	DEFAULT 0	Monthly budget in cents (ad spend, not service fee)
start_date	DATE	NULLABLE	Campaign start
end_date	DATE	NULLABLE	Campaign end (null for ongoing)
platform_campaign_ids	JSONB	DEFAULT '{}'	External platform IDs (e.g., {google_ads: '123', meta: '456'})
performance_targets	JSONB	DEFAULT '{}'	KPI targets (e.g., {cpl_cents: 5000, ctr: 0.03})
last_optimized_at	TIMESTAMP	NULLABLE	Last time Signal agent optimized
agent_owner	ENUM	NOT NULL	Primary agent responsible (signal, forge, beacon, relay_agent)
created_at	TIMESTAMP	DEFAULT now()	
updated_at	TIMESTAMP	DEFAULT now()	
2.1.4 content_items
Individual pieces of content produced by agents.
Column	Type	Constraints	Description
id	UUID	PK	Primary identifier
organization_id	UUID	FK -> organizations.id, NOT NULL	Parent organization
campaign_id	UUID	FK -> campaigns.id, NULLABLE	Associated campaign
type	ENUM	NOT NULL	blog_post | social_post | social_story | email | ad_copy | ad_creative | landing_page | review_response | gbp_post
status	ENUM	NOT NULL, DEFAULT 'draft'	draft | in_review | approved | published | rejected | archived
title	VARCHAR(500)	NULLABLE	Content title/headline
body	TEXT	NULLABLE	Content body (Markdown for long-form, plain text for short)
rich_body	JSONB	NULLABLE	Structured content (e.g., email blocks, social media carousel slides)
media_urls	TEXT[]	DEFAULT '{}'	Associated media files (images, videos)
platform_post_ids	JSONB	DEFAULT '{}'	External platform post IDs after publishing
scheduled_publish_at	TIMESTAMP	NULLABLE	Scheduled publish time
published_at	TIMESTAMP	NULLABLE	Actual publish time
quality_score	DECIMAL(3,2)	NULLABLE	0.00-1.00 automated quality score
quality_dimensions	JSONB	DEFAULT '{}'	Breakdown of quality dimensions (relevance, tone, seo, engagement)
human_review_required	BOOLEAN	DEFAULT false	Flagged for human review
human_review_status	ENUM	NULLABLE	pending | approved | revision_requested | rejected
human_reviewer_id	UUID	FK -> internal_users.id, NULLABLE	Who reviewed
revision_notes	TEXT	NULLABLE	Notes from human reviewer
revision_count	INTEGER	DEFAULT 0	How many revisions
generating_agent	ENUM	NOT NULL	Agent that produced this (forge, pixel, beacon, signal)
generation_prompt_hash	VARCHAR(64)	NULLABLE	SHA-256 of the prompt used (for audit)
generation_model	VARCHAR(100)	NULLABLE	LLM model used
generation_tokens_input	INTEGER	DEFAULT 0	Input tokens consumed
generation_tokens_output	INTEGER	DEFAULT 0	Output tokens consumed
created_at	TIMESTAMP	DEFAULT now()	
updated_at	TIMESTAMP	DEFAULT now()	
2.1.5 agent_tasks
Central task ledger. Every unit of work performed by any agent is represented as a task.
Column	Type	Constraints	Description
id	UUID	PK	Primary identifier
organization_id	UUID	FK -> organizations.id, NULLABLE	Associated client (null for system tasks)
agent	ENUM	NOT NULL	scout | closer | intake | forge | pixel | signal | beacon | pulse | relay_agent | conductor
task_type	VARCHAR(100)	NOT NULL	Specific task type (e.g., generate_blog_post, optimize_bids, audit_gbp)
status	ENUM	NOT NULL, DEFAULT 'queued'	queued | in_progress | awaiting_dependency | awaiting_review | completed | failed | canceled | escalated
priority	INTEGER	NOT NULL, DEFAULT 5	1 (critical) to 10 (low). Used for queue ordering.
input_data	JSONB	NOT NULL, DEFAULT '{}'	Structured input for the task
output_data	JSONB	NULLABLE	Structured output from the task
error_data	JSONB	NULLABLE	Error details if failed
depends_on_task_ids	UUID[]	DEFAULT '{}'	Task IDs that must complete before this task can execute
parent_task_id	UUID	FK -> agent_tasks.id, NULLABLE	Parent task if this is a subtask
retry_count	INTEGER	DEFAULT 0	Number of retries attempted
max_retries	INTEGER	DEFAULT 3	Maximum retries before escalation
scheduled_for	TIMESTAMP	NULLABLE	Earliest execution time (for scheduled tasks)
started_at	TIMESTAMP	NULLABLE	When execution began
completed_at	TIMESTAMP	NULLABLE	When execution finished
duration_ms	INTEGER	NULLABLE	Execution duration in milliseconds
tokens_consumed	INTEGER	DEFAULT 0	Total LLM tokens consumed
cost_cents	INTEGER	DEFAULT 0	Estimated cost in cents
escalation_reason	TEXT	NULLABLE	Why this was escalated to human
escalated_to_user_id	UUID	FK -> internal_users.id, NULLABLE	Human escalation target
idempotency_key	VARCHAR(255)	UNIQUE, NULLABLE	Prevents duplicate execution
created_at	TIMESTAMP	DEFAULT now()	
updated_at	TIMESTAMP	DEFAULT now()	
2.1.6 agent_events
Immutable event log. Every meaningful action, state change, or observation is recorded here. This is the system's source of truth for audit, debugging, and agent learning.
Column	Type	Constraints	Description
id	UUID	PK	Primary identifier
event_type	VARCHAR(100)	NOT NULL, INDEXED	Dot-notation event type (e.g., task.completed, client.onboarded, quality.below_threshold)
agent	ENUM	NULLABLE	Agent that emitted the event
organization_id	UUID	FK, NULLABLE	Associated client
task_id	UUID	FK -> agent_tasks.id, NULLABLE	Associated task
payload	JSONB	NOT NULL, DEFAULT '{}'	Event-specific data
severity	ENUM	DEFAULT 'info'	debug | info | warning | error | critical
created_at	TIMESTAMP	DEFAULT now(), INDEXED	Event timestamp
2.1.7 Additional Core Tables
The following tables are also required. Schema follows the same conventions as above:

Table	Purpose	Key Columns
leads	Prospecting targets identified by Scout	organization_name, contact_email, contact_name, source, enrichment_data (JSONB), lead_score, status, assigned_closer_id, outreach_sequence_id
outreach_sequences	Email/LinkedIn outreach sequences	lead_id, sequence_type, steps (JSONB[]), current_step, status, engagement_signals (JSONB)
ad_accounts	Connected advertising accounts	organization_id, platform, platform_account_id, credentials_vault_ref, status, last_sync_at
ad_performance_daily	Daily aggregated ad metrics	organization_id, campaign_id, platform, date, impressions, clicks, conversions, spend_cents, cpc_cents, cpl_cents, ctr, conversion_rate
seo_metrics_weekly	Weekly SEO/visibility tracking	organization_id, date, organic_sessions, keyword_rankings (JSONB), gbp_views, gbp_actions, review_count, avg_rating
content_performance	Content engagement metrics	content_item_id, date, views, clicks, shares, conversions, engagement_rate
client_reports	Generated performance reports	organization_id, report_type (weekly|monthly), period_start, period_end, data (JSONB), pdf_url, delivered_at, opened_at
knowledge_entries	Knowledge base entries	scope (global|vertical|client), scope_id, category, title, content (TEXT), embedding (vector(1536)), metadata (JSONB)
quality_reviews	Human quality review records	content_item_id, reviewer_id, verdict, score, feedback, reviewed_at
internal_users	StackLocal team members	name, email, role (admin|csm|reviewer|closer|engineer), auth_user_id
billing_events	Stripe webhook events	organization_id, stripe_event_id, event_type, data (JSONB)
creator_profiles	Local creators/influencers	name, handle, platform, followers, engagement_rate, metro_area, verticals (TEXT[]), contact_email, performance_history (JSONB)
creator_activations	Creator campaign activations	organization_id, campaign_id, creator_profile_id, status, deliverables (JSONB), compensation_cents, performance (JSONB)
system_config	Runtime configuration	key (UNIQUE), value (JSONB), description, updated_by, updated_at
api_usage_log	External API call tracking	service, endpoint, method, status_code, latency_ms, tokens_used, cost_cents, agent, task_id, created_at
2.2 Indexes
The following indexes are required for query performance. Development agents must create these in the migration files:
•	agent_tasks: composite index on (agent, status, priority, scheduled_for) for queue polling
•	agent_tasks: index on (organization_id, status) for client task queries
•	agent_tasks: index on (idempotency_key) unique partial where idempotency_key IS NOT NULL
•	agent_events: composite index on (event_type, created_at DESC) for event queries
•	agent_events: index on (organization_id, created_at DESC) for client event timeline
•	content_items: composite index on (organization_id, type, status) for content management
•	content_items: index on (scheduled_publish_at) where status = 'approved' for publishing queue
•	ad_performance_daily: composite index on (organization_id, platform, date DESC) for reporting
•	leads: composite index on (status, lead_score DESC) for Scout prioritization
•	knowledge_entries: GIN index on embedding using ivfflat for vector similarity search
•	organizations: index on (subscription_status, churn_risk_score DESC) for retention monitoring
2.3 Row Level Security (RLS) Policies
All client-facing tables must implement RLS via Supabase. Policies enforce that portal users can only access data belonging to their organization:
•	Portal users: SELECT, INSERT, UPDATE only on rows where organization_id matches their JWT claim organization_id
•	Service role (agents): Full access via service_role key, bypassing RLS
•	Internal users: Access controlled via role-based policies (admin = all, csm = assigned clients, reviewer = content review tables)
AGENT INSTRUCTION: Never disable RLS on any table that contains client data. If a migration requires temporary RLS bypass, use the service_role connection and re-enable immediately after.
 
3. Agent Framework
All agents extend a shared base framework that provides common capabilities. This section defines the framework contracts that every agent must implement.
3.1 Base Agent Interface
Every agent must implement the following TypeScript interface:
interface IAgent {
  readonly agentType: AgentType;
  readonly version: string;

  // Lifecycle
  initialize(): Promise<void>;
  shutdown(): Promise<void>;
  healthCheck(): Promise<AgentHealthStatus>;

  // Task execution
  canHandle(task: IAgentTask): boolean;
  execute(task: IAgentTask): Promise<TaskResult>;
  estimateCost(task: IAgentTask): Promise<CostEstimate>;

  // Quality
  selfScore(output: any, task: IAgentTask): Promise<QualityScore>;

  // Learning
  incorporateFeedback(feedback: QualityReview): Promise<void>;
}
3.2 Task Lifecycle
Every task follows this state machine. Transitions that do not appear in this diagram are invalid and must be rejected:

From State	To State	Trigger	Side Effects
queued	in_progress	Agent picks up task from queue	Set started_at, emit task.started event
queued	canceled	Manual cancellation or dependency failure	Emit task.canceled event
in_progress	completed	Agent finishes successfully	Set completed_at, duration_ms, emit task.completed event
in_progress	failed	Unrecoverable error	Set error_data, emit task.failed event. If retry_count < max_retries, create retry task.
in_progress	awaiting_review	Output requires human review	Set human_review_required = true, emit task.awaiting_review event
in_progress	escalated	Agent cannot complete (ambiguity, policy violation, quality threshold)	Set escalation_reason, escalated_to_user_id, emit task.escalated event
awaiting_review	completed	Human approves	Set completed_at, emit task.review_approved event
awaiting_review	in_progress	Human requests revision	Increment revision_count, emit task.revision_requested event
awaiting_dependency	queued	All dependency tasks completed	Emit task.dependencies_resolved event
failed	queued	Retry scheduled	Increment retry_count, emit task.retrying event
3.3 LLM Interaction Layer
All agents access LLMs through a shared abstraction layer that enforces consistent behavior:
3.3.1 Model Selection
Task Complexity	Model	Max Tokens	Temperature
Routine (status checks, data formatting, simple queries)	claude-sonnet-4-20250514	2048	0.2
Standard (content drafts, analysis, recommendations)	claude-sonnet-4-20250514	4096	0.4
Complex (strategy docs, multi-step reasoning, novel situations)	claude-opus-4-6	8192	0.3
Creative (ad copy, social content, blog posts)	claude-sonnet-4-20250514	4096	0.7
Fallback (Anthropic API unavailable)	gpt-4o	4096	Matches original
3.3.2 Prompt Management
All prompts must be stored as versioned templates in the knowledge base, not hardcoded in agent source code. The prompt management system provides:
•	Template interpolation with typed variables
•	A/B testing support (multiple prompt variants with traffic splitting)
•	Performance tracking (which prompt version produces the highest quality scores)
•	Automatic rollback if a new prompt version degrades quality below threshold
Prompt templates use the following structure:
interface IPromptTemplate {
  id: string;
  agent: AgentType;
  task_type: string;
  version: number;
  system_prompt: string;
  user_prompt_template: string;  // Handlebars-style {{variable}} interpolation
  output_schema?: JSONSchema;     // Expected output structure for parsing
  quality_threshold: number;      // Minimum quality score to accept output
  is_active: boolean;
  performance_stats: {
    invocations: number;
    avg_quality_score: number;
    avg_tokens: number;
    avg_latency_ms: number;
  };
}
3.3.3 Output Parsing and Validation
Every LLM call must specify an expected output schema. The framework provides:
1.	Structured output extraction: LLM responses are parsed against the defined JSON schema. If the response does not conform, the framework retries with a corrective prompt (max 2 retries).
2.	Content safety filtering: All generated content is checked against a blocklist of prohibited terms, competitor names (per client brand guide), and off-brand language patterns.
3.	PII detection: Outputs are scanned for accidental PII leakage (phone numbers, emails, addresses not belonging to the client). Detected PII triggers immediate task escalation.
4.	Token tracking: Every LLM call logs input tokens, output tokens, model used, and estimated cost to the api_usage_log table.
3.4 Error Handling
All agents must implement the following error handling hierarchy:

Error Category	Behavior	Example
Transient (network timeout, rate limit, 5xx)	Retry with exponential backoff: 1s, 4s, 16s, 64s. Max 4 retries.	Anthropic API 429, Google Ads API 503
Data validation (invalid input, schema mismatch)	Fail the task immediately. Log error_data with validation details. Do not retry.	Missing required field in task input, malformed API response
Quality threshold (output below minimum quality)	Retry with refined prompt (max 2 attempts). If still below threshold, escalate to human.	Blog post quality score 0.45 against 0.60 threshold
External service unavailable (extended outage)	Mark task as failed with retry scheduled for +1 hour. After 3 hourly retries, escalate.	Meta API down for maintenance
Budget/rate limit exceeded	Pause all tasks for the affected service. Notify Conductor. Resume when budget/limit resets.	Monthly API budget exceeded, daily rate limit hit
Unknown/unhandled	Fail task, log full error with stack trace, emit critical event, notify on-call engineer via PagerDuty.	Unexpected exception not matching any known category
3.5 Agent Communication Protocol
Agents never communicate directly with each other. All inter-agent communication flows through one of two channels:
•	Task creation: An agent that needs work done by another agent creates a new agent_task record with the target agent specified. Conductor manages the queue and delivery.
•	Event emission: An agent that needs to broadcast information (e.g., 'client onboarding complete') emits an event to the event bus. Other agents subscribe to relevant event types and react accordingly.
This strict separation ensures that agents remain independently deployable, testable, and replaceable. No agent holds a reference to another agent's internal state or methods.
 
4. Agent Specifications
This section provides the detailed specification for each of the ten agents. For each agent, the following is defined: purpose, task types it handles, input/output schemas, decision logic, quality criteria, escalation triggers, and integration dependencies.
AGENT INSTRUCTION: Each agent specification is self-contained. An autonomous development agent should be able to build any single agent by reading only Section 3 (Agent Framework) and the relevant agent subsection below, without needing to reference other agent specifications.
4.1 Scout (Prospecting & Lead Generation)
Purpose
Scout identifies, enriches, scores, and initiates outreach to potential client businesses. It is the top-of-funnel engine that feeds the sales pipeline.
Task Types
Task Type	Trigger	Input	Output
prospect_search	Scheduled (daily) or manual trigger	{metro_area, industry, min_revenue_estimate, max_existing_clients_in_area}	Array of raw prospect records with basic firmographic data
prospect_enrich	Automatically follows prospect_search	{prospect_id}	Enriched record: website audit score, Google review count/avg, social presence, estimated ad spend, tech stack
lead_score	Automatically follows prospect_enrich	{prospect_id, enrichment_data}	Numeric score 0-100 with scoring dimension breakdown
generate_outreach_sequence	Lead score >= 60 threshold	{lead_id, contact_info, enrichment_data, sequence_template_id}	Personalized multi-step outreach sequence (3-5 emails)
send_outreach_step	Scheduled per sequence cadence	{outreach_sequence_id, step_number}	Delivery confirmation, platform message ID
process_engagement_signal	Webhook from email platform	{outreach_sequence_id, signal_type (open|click|reply|bounce)}	Updated engagement score, next action recommendation
Lead Scoring Model
Scout uses a weighted scoring model with the following dimensions:
Dimension	Weight	Data Source	Scoring Logic
Website Quality	15%	Lighthouse audit via Puppeteer	0-25: Score = (performance + accessibility + seo + best_practices) / 4
Review Presence	20%	Google Places API	0-25: Based on review count (>50 = max) and average rating (>4.5 = max)
Social Activity	10%	Platform APIs / scraping	0-25: Active posting (last 30 days), follower count relative to vertical median
Estimated Ad Spend	15%	SEMrush API / SpyFu	0-25: Higher spend indicates marketing awareness. $0 = 0, $1k+/mo = max
Competitive Density	10%	Google Maps API	0-25: More competitors in area = higher opportunity for differentiation
Business Size Signals	15%	Apollo.io enrichment	0-25: Employee count, revenue estimate, years in business
Existing Marketing Gaps	15%	Website + GBP audit	0-25: Missing GBP optimization, no blog, no email capture, broken pages
Outreach Sequence Templates
Scout maintains a library of outreach sequence templates organized by approach. Each template defines the number of steps, timing between steps, and a prompt framework for personalizing each message. The two founding templates are:
•	Trusted Resource (relationship-first): 5-step sequence over 21 days. Opens with a genuine observation about the prospect's business. Offers a free insight (e.g., 'I noticed your Google Business Profile is missing service-area pages'). Builds credibility through local knowledge. Soft CTA for a 15-minute call.
•	Insight-Led (data-first): 4-step sequence over 14 days. Opens with a specific, verifiable data point about the prospect's market (e.g., 'Roofers in your area are averaging a $45 cost-per-lead on Google Ads'). Demonstrates expertise. Direct CTA for a strategy session.
Integration Dependencies
•	Apollo.io API: Contact discovery, firmographic enrichment
•	Hunter.io API: Email verification
•	Google Places API: Review data, business information
•	Google PageSpeed Insights API: Website audit
•	SEMrush/SpyFu API: Competitive intelligence, ad spend estimation
•	SendGrid API or Instantly.ai: Email delivery for outreach
Escalation Triggers
•	Lead replies with negative sentiment or opt-out request: Immediately stop sequence, flag for human review
•	Lead replies with complex question or objection: Route to Closer with full context
•	Outreach bounce rate exceeds 10% for a batch: Pause sending, alert for email list quality review
•	Lead score model accuracy drops below 70% (measured by conversion correlation): Alert engineering for model recalibration
4.2 Closer (Sales Enablement)
Purpose
Closer monitors engagement signals from Scout's outreach, prepares human sales representatives with contextual intelligence, and manages the proposal-to-close workflow. Closer does not replace human salespeople; it makes them dramatically more effective.
Task Types
Task Type	Trigger	Input	Output
prepare_lead_brief	Lead engagement score crosses threshold	{lead_id, engagement_signals, enrichment_data}	Structured brief: business summary, competitive position, pain points, recommended approach, talking points
generate_proposal	Human closer requests proposal	{organization_id, tier, custom_requirements}	Branded proposal document (PDF) with scope, pricing, timeline, case studies
draft_follow_up	Post-call action item	{lead_id, call_notes, next_action}	Draft follow-up email with personalized next steps
track_pipeline	Continuous	{all active leads}	Updated pipeline status, stale lead alerts, velocity metrics
generate_onboarding_packet	Deal closed	{organization_id, signed_contract_data}	Onboarding welcome email, account setup checklist, intake questionnaire link
Escalation Triggers
•	Lead requests pricing outside standard tiers: Escalate to founder for custom quote
•	Lead mentions competitor by name: Include competitive positioning notes in brief
•	Deal value exceeds $4,000/month: Flag for founder involvement in close
•	Lead has been in pipeline for >30 days without progression: Alert assigned closer
4.3 Intake (Client Onboarding)
Purpose
Intake manages the end-to-end onboarding process for new clients, from initial data collection through baseline audit to strategy brief generation. A smooth onboarding is the single highest-leverage moment for client retention.
Task Types
Task Type	Trigger	Input	Output
send_onboarding_questionnaire	Deal marked as closed	{organization_id, primary_contact}	Branded questionnaire delivered via portal + email with unique link
process_questionnaire_response	Client submits questionnaire	{organization_id, response_data}	Parsed and validated responses stored in organization record and brand_guidelines
audit_web_presence	Questionnaire received or manual trigger	{organization_id, website_url}	Comprehensive audit: site speed, SEO health, mobile responsiveness, content gaps, schema markup, accessibility
audit_google_business_profile	Questionnaire received	{organization_id, gbp_id}	GBP completeness score, missing fields, photo quality, posting frequency, review summary
audit_ad_accounts	Ad account credentials provided	{organization_id, ad_account_ids}	Historical performance summary, wasted spend areas, structure recommendations
audit_social_profiles	Questionnaire received	{organization_id, social_handles}	Per-platform audit: posting frequency, engagement rate, follower quality, content mix
generate_competitive_analysis	All audits complete	{organization_id, competitor_ids}	Competitive landscape: where client is ahead, behind, and gaps to exploit
generate_baseline_report	All audits complete	{organization_id}	Unified onboarding report: current state, benchmark comparison, opportunity matrix
generate_strategy_brief	Baseline report approved	{organization_id, baseline_report_id}	90-day strategic roadmap: priorities, KPI targets, resource allocation, campaign plan
Onboarding SLA
Intake must complete the full onboarding workflow within the following time constraints:
•	Questionnaire delivery: Within 1 hour of deal close
•	All audits initiated: Within 4 hours of questionnaire submission
•	Baseline report generated: Within 48 hours of questionnaire submission
•	Strategy brief generated: Within 72 hours of questionnaire submission
•	First content deliverable live: Within 14 days of contract signature
Escalation Triggers
•	Client does not submit questionnaire within 72 hours: Escalate to CSM for personal follow-up
•	Ad account credentials invalid or restricted: Escalate to CSM to coordinate with client
•	Audit reveals serious website issues (e.g., site down, malware, critical SEO penalties): Immediate alert to CSM and founder
•	Client's competitive landscape shows market saturation (>10 direct competitors with active marketing): Flag for founder strategic review
4.4 Forge (Content Production)
Purpose
Forge is the content engine. It produces all written content across all channels: blog posts, social media content, email campaigns, ad copy, Google Business Profile posts, and review responses. Forge operates against client-specific brand guidelines and vertical content frameworks.
Task Types
Task Type	Trigger	Input	Output
generate_blog_post	Content calendar schedule or manual request	{organization_id, topic, keywords, target_length, tone_profile}	Markdown blog post with title, meta description, body, internal link suggestions, CTA
generate_social_post	Content calendar schedule	{organization_id, platform, content_theme, media_requirements}	Platform-formatted post with copy, hashtags, media brief for Pixel
generate_email_campaign	Campaign schedule or manual trigger	{organization_id, campaign_objective, audience_segment, email_count}	Email sequence: subject lines, preview text, body (HTML blocks), CTA, send timing
generate_ad_copy	Signal requests new creative	{organization_id, platform, campaign_objective, audience, existing_performance_data}	Ad copy variants (3-5) with headlines, descriptions, CTAs optimized per platform specs
generate_review_response	New review detected by Beacon	{organization_id, review_text, rating, reviewer_name}	Drafted response matching brand tone. Positive reviews: grateful, specific callback. Negative: empathetic, offers resolution.
generate_gbp_post	Weekly schedule	{organization_id, post_type (update|offer|event)}	GBP post with image brief, copy, CTA button selection
revise_content	Human review requests revision	{content_item_id, revision_notes}	Revised content addressing all notes, tracked changes where applicable
Content Quality Dimensions
Forge self-scores every piece of content across five dimensions, each weighted 0.00 to 1.00:
Dimension	Weight	Evaluation Criteria
Relevance	25%	Does the content directly serve the client's business objectives and target audience? Is it specific to their service area and verticals?
Brand Alignment	20%	Does tone, vocabulary, and style match the client's brand_guidelines? Are there any off-brand terms or competitor mentions?
SEO Optimization	20%	For applicable content: keyword density (1-2%), header structure, meta description quality, internal linking, readability score (Flesch-Kincaid 8th grade target)
Engagement Potential	20%	Is the content compelling? Does it have a clear hook, value proposition, and CTA? For social: is it scroll-stopping? For email: is the subject line strong?
Factual Accuracy	15%	Are all claims verifiable? Are statistics cited? Are local references accurate (street names, neighborhoods, landmarks)?
The minimum quality threshold for auto-publication is 0.70 composite. Content scoring between 0.55 and 0.70 is queued for human review. Content below 0.55 is rejected and regenerated.
Content Calendar System
Forge operates against a per-client content calendar that is generated during onboarding and maintained by Conductor:
•	Blog posts: 1 per week, published Tuesday or Thursday mornings (client timezone)
•	Social posts: 3-4 per week, distributed across Monday/Wednesday/Friday/Saturday
•	Email campaigns: 2 per month (Foundation tier), 4 per month (Growth/Dominance)
•	GBP posts: 1 per week, published Monday mornings
•	Review responses: Within 4 hours of review detection (during business hours)
The calendar is stored in the system_config table per organization and can be adjusted by CSMs or via client portal preferences.
4.5 Pixel (Creative & Design)
Purpose
Pixel generates all visual assets: ad creatives, social media graphics, email header images, blog featured images, and brand templates. It maintains visual consistency across all client touchpoints.
Task Types
Task Type	Trigger	Input	Output
generate_ad_creative	Signal requests new creatives	{organization_id, platform, dimensions, copy_text, brand_guidelines, creative_direction}	Image file(s) in required platform dimensions, stored in Supabase Storage
generate_social_graphic	Forge completes social post	{organization_id, post_copy, platform, content_theme}	Platform-sized graphic with text overlay, brand colors, imagery
generate_email_header	Forge completes email campaign	{organization_id, email_subject, campaign_theme}	600px wide email header image
generate_blog_featured_image	Forge completes blog post	{organization_id, post_title, post_summary}	1200x630px featured image
create_brand_template_set	Onboarding complete	{organization_id, brand_guidelines}	Template set: social post templates (3-5 variants), ad templates, email header template
resize_creative	Multi-platform campaign	{source_image_url, target_dimensions[]}	Resized variants maintaining visual hierarchy and legibility
Design Constraints
•	All generated images must use the client's brand colors as defined in organization.brand_guidelines
•	Text on images must pass WCAG AA contrast ratio (4.5:1 for normal text, 3:1 for large text)
•	No AI-generated human faces (to avoid uncanny valley and potential legal issues)
•	All images must be generated at 2x resolution for retina display support
•	Platform dimension requirements: Google Display (300x250, 728x90, 160x600, 336x280), Meta Feed (1080x1080, 1200x628), Meta Stories (1080x1920), Instagram (1080x1080, 1080x1350)
4.6 Signal (Paid Media Management)
Purpose
Signal manages all paid advertising campaigns across Google Ads and Meta Ads. It handles campaign structure, bid optimization, audience management, creative rotation, and budget pacing.
Task Types
Task Type	Trigger	Input	Output
setup_campaign	Onboarding or new campaign request	{organization_id, platform, objective, budget, targeting, ad_copy, creatives}	Live campaign on platform with proper structure, tracking, and initial bids
optimize_bids	Daily scheduled (2am client timezone)	{campaign_id, performance_data_last_7d}	Bid adjustments applied: keyword bids, audience bids, placement bids, dayparting
optimize_audiences	Weekly scheduled	{campaign_id, conversion_data, audience_performance}	Audience expansions, exclusions, lookalike updates, demographic adjustments
rotate_creatives	Performance-triggered (CTR < threshold or frequency > 3)	{campaign_id, current_creatives_performance}	Paused underperformers, activated new variants, requested new creatives from Pixel if inventory low
manage_budget_pacing	Daily scheduled	{organization_id, monthly_budget, spend_to_date, days_remaining}	Daily budget adjustments to hit monthly target within +/- 5%
detect_anomaly	Continuous monitoring (hourly)	{campaign_id, metrics_last_24h, metrics_baseline}	Anomaly alert if spend, CPC, or conversion rate deviates >2 standard deviations from 14-day baseline
generate_optimization_report	Weekly scheduled	{organization_id, all_campaign_performance}	Structured optimization summary: actions taken, results, recommendations for next period
Optimization Rules
Signal operates within strict guardrails to prevent runaway spend or disruptive changes:
•	Maximum single bid adjustment: 20% increase or 30% decrease per optimization cycle
•	Budget changes require Conductor approval if they exceed 15% of the original monthly budget
•	Campaigns with fewer than 50 conversions in 30 days do not trigger automated audience changes (insufficient data)
•	No campaign is paused automatically without explicit human approval, except for policy violations flagged by the platform
•	New keyword additions are limited to 10 per optimization cycle to prevent portfolio dilution
•	Negative keyword additions have no limit (protecting budget is always allowed)
4.7 Beacon (Local SEO & Visibility)
Purpose
Beacon manages all aspects of local search visibility: Google Business Profile optimization, citation management, review monitoring and response, local keyword tracking, and answer engine optimization for AI-powered search.
Task Types
Task Type	Trigger	Input	Output
optimize_gbp	Weekly scheduled	{organization_id, gbp_data, competitor_gbp_data}	GBP updates: categories, attributes, service areas, photos, business description
monitor_reviews	Continuous polling (every 30 min)	{organization_id, gbp_id, last_check_timestamp}	New reviews detected, sentiment classified, Forge notified for response drafting
build_citations	Onboarding + quarterly refresh	{organization_id, nap_data}	Citation submissions to top 50 local directories, consistency audit
track_keywords	Weekly scheduled	{organization_id, target_keywords}	Keyword ranking report: positions, changes, SERP feature presence, AI Overview inclusion
audit_aeo	Monthly scheduled	{organization_id, target_queries}	Answer engine optimization audit: is the business cited in AI Overviews, ChatGPT, Perplexity for target queries? Recommendations for improvement.
monitor_competitors	Weekly scheduled	{organization_id, competitor_ids}	Competitor movement report: new reviews, ranking changes, new content, ad activity
4.8 Pulse (Analytics & Reporting)
Purpose
Pulse is the intelligence layer. It aggregates data from all channels, generates reports, detects anomalies, calculates health and churn risk scores, and surfaces actionable recommendations.
Task Types
Task Type	Trigger	Input	Output
sync_performance_data	Daily scheduled (6am UTC)	{organization_id, connected_platforms}	Updated ad_performance_daily, seo_metrics_weekly, content_performance records
generate_weekly_summary	Weekly scheduled (Monday 8am client TZ)	{organization_id, week_date_range}	Internal weekly scorecard: KPIs, week-over-week changes, alerts
generate_monthly_report	Monthly (1st of month, 9am client TZ)	{organization_id, month_date_range}	Client-facing PDF report: executive summary, channel performance, ROI analysis, next month plan
calculate_health_score	Daily	{organization_id}	Updated health_score on organization record. Composite of: performance vs targets (40%), engagement (20%), content output (20%), platform health (20%)
calculate_churn_risk	Weekly	{organization_id}	Updated churn_risk_score. Signals: declining performance, reduced portal logins, support ticket volume, payment issues, low engagement with reports
detect_anomaly	Continuous (hourly)	{organization_id, metrics_window}	Anomaly events for metrics deviating >2 std dev from trailing 14-day average
generate_recommendation	Triggered by anomaly or monthly cycle	{organization_id, performance_data, anomalies}	Structured recommendation: problem statement, root cause hypothesis, recommended action, expected impact, confidence level
4.9 Relay Agent (Creator Marketing)
Purpose
Relay Agent identifies, vets, and activates local creators and micro-influencers for client campaigns. It integrates with the Relay platform to manage the creator network.
Task Types
Task Type	Trigger	Input	Output
discover_creators	Onboarding or campaign request	{metro_area, verticals, min_followers, max_followers, platform}	Ranked list of creator candidates with audience analysis
draft_outreach	Creator selected for activation	{creator_profile_id, campaign_brief, compensation_offer}	Personalized outreach message via email or DM template
coordinate_deliverables	Creator accepts activation	{activation_id, deliverable_requirements, timeline}	Deliverable schedule, content briefs, approval workflow
track_activation_performance	Content published	{activation_id, content_urls}	Performance metrics: reach, engagement, click-throughs, attributed conversions
4.10 Conductor (Orchestration Engine)
Purpose
Conductor is the central nervous system. It manages task scheduling, dependency resolution, agent coordination, escalation routing, system health monitoring, and client lifecycle automation. It does not produce client-facing work directly.
Core Responsibilities
•	Task scheduling and queue management: Conductor maintains the master task queue. It polls for queued tasks, checks dependency resolution, assigns tasks to agent-specific queues based on priority, and monitors execution throughput.
•	Dependency graph resolution: When a task has depends_on_task_ids, Conductor monitors those dependencies and transitions the task from awaiting_dependency to queued when all dependencies are met.
•	Content calendar execution: Conductor maintains per-client content calendars and creates the appropriate Forge/Pixel tasks on schedule. It adjusts for holidays, client-requested pauses, and seasonal patterns.
•	Escalation routing: When any agent escalates a task, Conductor determines the appropriate human recipient based on escalation type, client assignment, and human operator availability.
•	System health monitoring: Conductor tracks agent throughput, error rates, queue depths, and latency. It emits alerts when any metric exceeds defined thresholds.
•	Client lifecycle automation: Conductor triggers lifecycle events: 30/60/90-day check-ins, renewal preparation, upsell opportunity identification, and churn intervention workflows.
Scheduling Rules
•	Maximum concurrent tasks per agent: 10 (configurable per agent type)
•	Maximum concurrent tasks per organization: 5 (prevents single client from monopolizing resources)
•	Task timeout: 5 minutes for routine tasks, 15 minutes for content generation, 30 minutes for audits and reports
•	Dead letter queue: Tasks that fail after max_retries are moved to a dead letter queue for human investigation. Alert emitted immediately.
•	Priority override: Tasks created by human operators always receive priority 1 (critical)
 
5. Client Portal
The client portal is the primary interface for client interaction. It must provide a professional, intuitive experience that reinforces the perception of a high-quality marketing team.
5.1 Authentication & Authorization
•	Authentication: Supabase Auth with email/password + magic link. Google OAuth as optional SSO.
•	Multi-user support: Multiple contacts per organization can have portal access with role-based permissions.
•	Roles: Owner (full access, billing), Manager (full access, no billing), Viewer (read-only dashboards and reports).
•	Session: JWT-based, 7-day refresh token, 1-hour access token.
5.2 Portal Pages

Page	Route	Description
Dashboard	/dashboard	Overview: health score, key metrics (leads, traffic, spend, ROI), recent activity feed, upcoming deliverables
Performance	/performance	Detailed analytics: filterable by channel, date range, campaign. Charts for trends. Comparison to prior period and benchmarks.
Content	/content	Content library: all blog posts, social posts, emails, ads. Filter by type, status, date. Preview and approval workflow for pending items.
Campaigns	/campaigns	Campaign overview: active campaigns by channel, budget utilization, performance summary. Drill-down to individual campaign details.
Reports	/reports	Archive of all weekly and monthly reports. PDF download. Historical trend view.
Reviews	/reviews	Review monitoring: new reviews across platforms, drafted responses pending approval, response history.
Brand Guide	/brand-guide	Client's brand guidelines as stored in the system. Editable fields for colors, fonts, tone description, approved/prohibited terms.
Settings	/settings	Organization settings, notification preferences, connected accounts, user management, billing (Stripe portal link).
Messages	/messages	Threaded communication between client and StackLocal team. Supports text, file attachments, and @mentions.
Onboarding	/onboarding	Onboarding wizard: questionnaire, account connections, brand guide setup. Only visible during onboarding phase.
5.3 Approval Workflows
Clients on Growth and Dominance tiers can optionally enable approval workflows for specific content types. When enabled:
5.	Forge/Pixel produces content and it enters 'pending_approval' status
6.	Client receives a notification (email + portal) with preview
7.	Client can approve, request revision (with notes), or reject from the portal
8.	Approved content is scheduled for publication per the content calendar
9.	If no action is taken within 48 hours, the content auto-publishes (configurable threshold)
Foundation tier clients do not have approval workflows. All content auto-publishes after passing the quality threshold.
5.4 Real-Time Features
•	Dashboard metrics update in real-time via Supabase Realtime subscriptions
•	New review alerts appear as toast notifications within 5 minutes of detection
•	Message thread supports real-time delivery (no page refresh required)
•	Content approval status changes reflect immediately across all connected sessions
5.5 Mobile Responsiveness
The portal must be fully responsive and functional on mobile devices (minimum viewport: 320px). The dashboard, content approval, review responses, and messaging must all work on mobile without degraded functionality. Use Tailwind responsive utilities for all layout decisions.
 
6. Internal Operations Dashboard
The ops dashboard is the command center for StackLocal's human team. It provides system-wide visibility and intervention capabilities.
6.1 Dashboard Views

View	Route	Description
System Overview	/ops	Real-time: active tasks by agent, queue depths, error rates, throughput (tasks/hour), active clients, system alerts
Client List	/ops/clients	All clients: health score, churn risk, subscription tier, MRR, onboarding status. Sortable, filterable, searchable.
Client Detail	/ops/clients/:id	Deep dive: all metrics, task history, content library, campaign performance, communication thread, notes, action log
Task Queue	/ops/tasks	All tasks: filterable by agent, status, priority, organization. Ability to manually retry, cancel, reassign, or escalate.
Escalations	/ops/escalations	Active escalations requiring human action. Grouped by urgency. Each shows full context, agent recommendation, and action buttons.
Content Review	/ops/review	Content items awaiting human quality review. Side-by-side view: agent output + quality scores + brand guide reference.
Pipeline	/ops/pipeline	Sales pipeline: leads by stage, conversion rates, velocity metrics, upcoming follow-ups, revenue forecast
Financial	/ops/financial	MRR, ARR, churn rate, LTV, CAC, margin analysis, AI spend tracking, per-client profitability
Agent Health	/ops/agents	Per-agent metrics: task throughput, avg completion time, error rate, quality scores, token usage, cost
System Config	/ops/config	Runtime configuration management: feature flags, thresholds, scheduling rules, API keys (masked)
6.2 Escalation Management
The escalation interface must provide:
•	One-click context loading: Full client history, relevant task chain, agent reasoning, and recommended action
•	Action buttons: Approve agent recommendation, override with manual input, reassign to different human, dismiss escalation
•	Time tracking: SLA timer showing time since escalation. Yellow at 2 hours, red at 4 hours.
•	Bulk operations: Ability to approve/dismiss multiple similar escalations simultaneously
•	Escalation analytics: Volume by type, resolution time, agent accuracy (did human agree with agent recommendation?)
 
7. Quality Assurance System
Quality is the brand. The QA system operates at three levels: automated self-scoring, peer scoring, and human review.
7.1 Automated Self-Scoring
Every agent self-scores its output immediately after generation. The self-score uses a dedicated LLM call (separate from the generation call) with a standardized evaluation prompt. The evaluation prompt includes:
•	The original task input and requirements
•	The generated output
•	The client's brand guidelines
•	The relevant quality dimensions and their definitions
•	Scoring instructions with calibration examples (few-shot)
Self-scoring must use a different model instance than generation to reduce self-confirmation bias. If the primary generation model is claude-sonnet-4-20250514, the self-scorer uses gpt-4o, and vice versa.
7.2 Peer Scoring (Cross-Agent Validation)
For high-stakes content (blog posts, client reports, ad copy entering rotation), Conductor triggers a peer scoring step where a second agent evaluates the output:
•	Forge content is peer-scored by a separate Forge instance with a 'reviewer' system prompt
•	Signal optimization recommendations are peer-scored by Pulse (which has performance data context)
•	Intake strategy briefs are peer-scored by Forge (for content feasibility) and Signal (for paid media feasibility)
7.3 Human Review Triggers
Content is routed to human review when any of the following conditions are met:
•	Automated quality score is between 0.55 and 0.70 (below auto-publish threshold)
•	Self-score and peer-score disagree by more than 0.15 points
•	Content mentions pricing, guarantees, legal claims, or health/safety topics
•	Content is for a client flagged as 'high-touch' (Dominance tier or at-risk)
•	The content type is new for this client (first blog post, first ad campaign)
•	Random sampling: 10% of auto-approved content is randomly selected for human review (quality audit)
7.4 Quality Feedback Loop
Human review decisions feed back into the agent learning system:
10.	When a human approves agent output, the quality score is confirmed and the generation prompt version is reinforced.
11.	When a human requests revisions, the revision notes are stored and used to refine future prompt templates.
12.	When a human rejects output, the task is flagged for root cause analysis: was the prompt inadequate, the brand guide incomplete, or the task input malformed?
13.	Weekly quality audits compare agent self-scores against human scores. If self-scoring accuracy drops below 80% correlation, the self-scoring prompts are recalibrated.
 
8. External Integrations
This section defines every external service integration. For each integration, the specification includes: authentication method, rate limits, data sync frequency, error handling, and the agent(s) that depend on it.
AGENT INSTRUCTION: All external API interactions must go through the integrations package. Never make raw HTTP calls from agent code. Every integration client must implement circuit breaker pattern, retry logic, and rate limit awareness.

Service	Auth Method	Rate Limit	Sync Frequency	Dependent Agents	Critical Path?
Anthropic API	API key (header)	Tier-dependent, auto-retry on 429	Real-time (per task)	All agents	Yes (primary LLM)
OpenAI API	API key (header)	Tier-dependent	Real-time (fallback)	All agents (fallback)	No (secondary)
Google Ads API	OAuth 2.0 (refresh token)	15,000 requests/day	Daily sync + real-time for optimizations	Signal, Pulse	Yes (paid media)
Meta Marketing API	OAuth 2.0 (long-lived token)	200 calls/hour/ad account	Daily sync + real-time for optimizations	Signal, Pulse, Pixel	Yes (paid media)
Google Business Profile API	OAuth 2.0	Varies by endpoint	Every 30 min (reviews), daily (metrics)	Beacon, Forge	Yes (local SEO)
Google PageSpeed API	API key	25,000 queries/day	On demand (audits)	Intake, Scout	No
Google Places API	API key	Contact quota limit	On demand (enrichment)	Scout, Intake	No
Apollo.io API	API key	Tier-dependent	On demand (prospecting)	Scout	No
Hunter.io API	API key	Tier-dependent	On demand (verification)	Scout	No
SEMrush API	API key	Tier-dependent	Weekly	Scout, Beacon	No
SendGrid API	API key	Tier-dependent	Real-time (sends) + webhooks (events)	Scout (outreach), Forge (email campaigns)	Yes (email delivery)
Stripe API	API key (secret)	100 reads/sec	Webhooks (events)	Billing system	Yes (payments)
Replicate API (FLUX)	API key	Tier-dependent	On demand (image gen)	Pixel	No
Supabase (self-hosted services)	Service role key	N/A (self-managed)	Real-time	All	Yes (database)
8.1 Circuit Breaker Configuration
Every external integration must implement a circuit breaker with the following parameters:
•	Failure threshold: 5 consecutive failures or >50% failure rate in a 60-second window
•	Open state duration: 30 seconds (no requests sent)
•	Half-open: Allow 1 probe request. If successful, close circuit. If failed, return to open state for 60 seconds.
•	When circuit is open: Tasks depending on this integration are moved to awaiting_dependency with a retry scheduled for circuit close time.
8.2 Credential Management
All API credentials are stored in environment variables, never in code or database. Client-specific OAuth tokens (Google Ads, Meta, GBP) are stored in a Supabase vault table with encryption at rest. Access to credentials is restricted to the specific integration service that needs them.
 
9. Monitoring & Observability
9.1 Structured Logging
All log entries must follow this structure:
{
  timestamp: ISO-8601,
  level: 'debug' | 'info' | 'warn' | 'error' | 'fatal',
  service: string,          // e.g., 'agent-forge', 'api-gateway'
  agent?: AgentType,
  task_id?: string,
  organization_id?: string,
  message: string,
  metadata?: Record<string, any>,
  error?: { message: string, stack: string, code?: string },
  duration_ms?: number,
  tokens_used?: number
}
All logs are shipped to Axiom via structured JSON. No console.log in production code.
9.2 Metrics
The following metrics must be collected and available in the ops dashboard:

Metric	Collection	Alert Threshold
Task throughput (tasks/hour per agent)	Counter, aggregated per minute	< 50% of trailing 24h average
Task error rate (% failed per agent)	Ratio, rolling 1h window	> 10% for any agent
Task latency (p50, p95, p99 per task type)	Histogram	p95 > 2x trailing 7-day p95
Queue depth (per agent queue)	Gauge, sampled every 30s	> 100 tasks for any queue
LLM token usage (per agent, per model)	Counter, aggregated daily	> 120% of daily budget
API external call latency (per service)	Histogram	p95 > 5s for any service
API external error rate (per service)	Ratio, rolling 1h	> 5% for critical path services
Active client count	Gauge	N/A (business metric)
MRR	Gauge, updated on billing events	N/A (business metric)
Content quality scores (avg per agent)	Gauge, rolling 7-day	< 0.65 average for any agent
Client health scores (distribution)	Histogram, daily	> 20% of clients below 0.40
Churn risk (distribution)	Histogram, weekly	> 15% of clients above 0.70 risk
9.3 Alerting
Alerts are routed based on severity:
•	Critical (system down, data loss risk): PagerDuty to on-call engineer, immediate
•	Error (agent failures, integration outages): Slack #ops-alerts channel, <5 min
•	Warning (threshold approaching, quality degradation): Slack #ops-warnings, batched every 15 min
•	Info (notable events, milestone achievements): Slack #ops-general, batched hourly
 
10. Security Requirements
10.1 Authentication
•	Client portal: Supabase Auth, bcrypt password hashing, optional TOTP 2FA
•	Internal ops: Supabase Auth with mandatory 2FA for all internal users
•	Service-to-service: JWT tokens signed with RS256, rotated every 24 hours
•	External webhooks: HMAC-SHA256 signature verification on all incoming webhooks
10.2 Data Protection
•	Encryption at rest: Supabase manages PostgreSQL encryption. Supabase Storage encrypts objects at rest.
•	Encryption in transit: TLS 1.3 on all connections. No exceptions.
•	PII handling: Client contact information, billing data, and OAuth tokens are classified as PII. PII fields are never logged. PII is never included in LLM prompts unless required for the specific task (e.g., personalizing an email with the client's business name).
•	Data retention: Agent events retained for 2 years. Task records retained for 1 year after completion. Client data retained for 90 days after account cancellation, then permanently deleted.
•	Backup: Supabase automated daily backups with 30-day retention. Point-in-time recovery enabled.
10.3 API Security
•	Rate limiting: All public endpoints rate-limited at 100 requests/minute per authenticated user, 20 requests/minute for unauthenticated
•	Input validation: All API inputs validated against JSON schemas using Zod. No raw user input reaches database queries.
•	SQL injection: Prevented via Prisma ORM parameterized queries. No raw SQL in application code.
•	XSS: All user-generated content sanitized before rendering. React's built-in XSS protection for portal. DOMPurify for any dangerouslySetInnerHTML usage.
•	CORS: Strict origin whitelist. Only portal and ops domains allowed.
 
11. Testing Requirements
11.1 Test Coverage Targets
•	Unit tests: Minimum 80% line coverage for all packages
•	Integration tests: Every external integration must have mock-based integration tests
•	Agent tests: Each agent must have a test suite covering all task types with fixture data
•	E2E tests: Critical user flows in the portal (onboarding, content approval, report viewing) must have Playwright E2E tests
•	Quality scoring tests: Quality scoring prompts must have a calibration test suite with 50+ examples and expected score ranges
11.2 Test Infrastructure
•	Framework: Vitest for unit/integration tests, Playwright for E2E
•	CI: All tests run on every PR. Merge blocked on test failure.
•	Fixtures: Shared test fixtures in packages/shared/test-fixtures/ for consistent test data
•	Mocks: All external API clients must expose a mock implementation for testing
•	Seed data: A comprehensive seed script (packages/db/seed.ts) that populates a development database with realistic test organizations, contacts, campaigns, content, and tasks
 
12. Performance Requirements

Requirement	Target	Measurement
Portal page load (Time to Interactive)	< 2 seconds on 4G connection	Lighthouse CI on every deployment
API response time (p95)	< 500ms for read endpoints, < 2s for write endpoints	Checkly synthetic monitoring
Task queue latency (time from queued to in_progress)	< 30 seconds for priority 1-3, < 5 minutes for priority 4-7	Custom metric in ops dashboard
Content generation (blog post)	< 90 seconds from task start to quality-scored output	Agent task duration_ms
Review response generation	< 120 seconds from review detection to drafted response	Agent task duration_ms
Report generation (monthly PDF)	< 5 minutes from task start to PDF available	Agent task duration_ms
Search queries (portal)	< 200ms for content/campaign search	Application-level timing
Concurrent users (portal)	Support 500 concurrent sessions without degradation	Load test with k6
Database query performance	No query > 100ms at p95 under normal load	pg_stat_statements monitoring
Uptime SLA	99.9% monthly (excludes scheduled maintenance)	Checkly uptime monitoring
 
13. Deployment & Infrastructure
13.1 Environments

Environment	Purpose	Infrastructure
Local	Developer workstation. Full system via Docker Compose.	Docker Compose: PostgreSQL, Redis, MinIO (S3-compatible), Mailhog (email capture)
Staging	Pre-production validation. Full integration testing.	Vercel preview (frontend), Railway staging (workers), Supabase staging project
Production	Live system.	Vercel production (frontend), Railway production (workers), Supabase production project
13.2 CI/CD Pipeline
14.	Push to feature branch: Lint (ESLint + Prettier), type check, unit tests, build check
15.	PR to main: All of above + integration tests + Playwright E2E against staging
16.	Merge to main: Automatic deployment to staging. Smoke tests. If passed, promote to production with canary (10% traffic for 15 minutes, then full rollout).
17.	Database migrations: Run automatically on deployment via Prisma migrate. Rollback plan documented for every migration.
13.3 Scaling Strategy
•	Frontend (Vercel): Auto-scales. No manual intervention required.
•	Worker services (Railway): Horizontal scaling per agent. Each agent type runs as an independent service with configurable replica count.
•	Database: Supabase Pro plan with connection pooling (PgBouncer). Read replicas added when query load exceeds 1,000 queries/second.
•	Queue (Redis): Upstash serverless Redis with auto-scaling. No capacity planning required.
 
14. Development Phases
This section defines the build sequence. Each phase must be completed and validated before proceeding to the next.

Phase	Duration	Deliverables	Validation Criteria
Phase 0: Foundation	2 weeks	Monorepo setup, database schema + migrations, auth system, base agent framework, BullMQ queue infrastructure, basic ops dashboard shell	Database migrations run cleanly. Auth flow works. One dummy agent can pick up and complete a task from the queue.
Phase 1: Core Agents	4 weeks	Forge, Beacon, Pulse agents fully operational. Content generation, GBP management, and reporting working end-to-end for one test client.	Forge produces a blog post that passes quality scoring. Beacon audits a GBP. Pulse generates a weekly summary report.
Phase 2: Client Portal	3 weeks	Full client portal: dashboard, content library, reports, reviews, brand guide, settings. Onboarding wizard.	A test client can log in, view dashboard, approve content, download reports, update brand guide.
Phase 3: Sales Agents	3 weeks	Scout, Closer, Intake agents. Prospecting pipeline, outreach sequences, onboarding workflow.	Scout identifies and scores 50 leads. Outreach sequence sends without errors. Intake completes full onboarding audit for test client.
Phase 4: Paid Media	3 weeks	Signal agent. Google Ads and Meta Ads integration. Campaign setup, optimization, budget pacing.	Signal creates a campaign via Google Ads API, runs one optimization cycle, and generates an optimization report.
Phase 5: Creative & Creators	2 weeks	Pixel agent, Relay Agent. Image generation, creator discovery and activation.	Pixel generates ad creatives in correct dimensions. Relay Agent identifies creators in a target metro.
Phase 6: Conductor & Integration	3 weeks	Full Conductor orchestration. All agents coordinated. End-to-end client lifecycle from prospect to steady-state.	A simulated client moves through entire lifecycle: discovered by Scout, closed, onboarded by Intake, receiving content from Forge, ads from Signal, reports from Pulse. No manual intervention required.
Phase 7: Hardening	2 weeks	Load testing, security audit, monitoring setup, documentation, error scenario testing, chaos engineering.	System handles 100 concurrent clients. All alert thresholds tested. Recovery from agent failure demonstrated.
 
15. Document Governance
This SRD is a living document. It is the authoritative source of truth for all system requirements. When conflicts arise between this document and any other source (code comments, Slack messages, verbal agreements), this document takes precedence.
Changes to this document require review and approval from the system architect (founder). All changes must be tracked via version history with clear rationale for each modification.
Autonomous development agents should treat every MUST, NEVER, and ALWAYS in this document as a hard constraint. Requirements using SHOULD are strong preferences that can be overridden with documented justification. Requirements using MAY are optional enhancements.

This specification defines a system that is ambitious in scope but disciplined in execution. Every requirement exists because it serves the core mission: delivering world-class local marketing to trades businesses through an agent-operated system that runs with precision, learns from every interaction, and scales without proportional human cost. Build it right.

END OF SPECIFICATION
