"""
User profile manager for Forge.

Stores the user's preferred tools and stack choices at:
  ~/.forge/profile.yaml

Profile is loaded by forge new to pre-fill interview defaults.
Can be created/edited via forge profile command.
"""

from datetime import datetime
from pathlib import Path

import yaml


PROFILE_CATEGORIES = [
    {
        "key": "framework",
        "label": "Frontend Framework",
        "suggestions": [
            "Next.js", "Remix", "SvelteKit", "Nuxt", "Astro",
            "React + Vite", "Vue", "Angular", "plain HTML",
        ],
    },
    {
        "key": "language",
        "label": "Language",
        "suggestions": [
            "TypeScript", "JavaScript", "Python", "Go", "Rust",
            "Ruby", "PHP", "Swift", "Kotlin",
        ],
    },
    {
        "key": "database",
        "label": "Database / Backend",
        "suggestions": [
            "Supabase", "Firebase", "PlanetScale", "Neon", "Postgres",
            "MySQL", "MongoDB", "SQLite", "Prisma", "Drizzle",
            "Django", "Rails", "FastAPI", "Express",
        ],
    },
    {
        "key": "auth",
        "label": "Authentication",
        "suggestions": [
            "Supabase Auth", "Clerk", "NextAuth", "Auth0",
            "Firebase Auth", "Lucia", "Better Auth", "custom",
        ],
    },
    {
        "key": "styling",
        "label": "Styling",
        "suggestions": [
            "Tailwind CSS", "shadcn/ui", "Styled Components",
            "CSS Modules", "Emotion", "Chakra UI", "MUI",
            "Mantine", "Vanilla CSS",
        ],
    },
    {
        "key": "package_manager",
        "label": "Package Manager",
        "suggestions": ["pnpm", "npm", "yarn", "bun"],
    },
    {
        "key": "testing",
        "label": "Testing",
        "suggestions": [
            "Vitest", "Jest", "Playwright", "Cypress",
            "pytest", "Go test", "RSpec",
        ],
    },
    {
        "key": "deployment",
        "label": "Deployment",
        "suggestions": [
            "Vercel", "Railway", "Fly.io", "Render", "AWS",
            "GCP", "Azure", "Netlify", "Cloudflare Pages",
            "DigitalOcean", "Heroku", "self-hosted",
        ],
    },
    {
        "key": "email",
        "label": "Email",
        "suggestions": [
            "Resend", "SendGrid", "Postmark", "Mailgun",
            "AWS SES", "Nodemailer",
        ],
    },
    {
        "key": "jobs",
        "label": "Background Jobs / Queues",
        "suggestions": [
            "Inngest", "Trigger.dev", "BullMQ", "Temporal",
            "Celery", "Sidekiq", "AWS SQS", "Cloudflare Queues",
        ],
    },
    {
        "key": "cms",
        "label": "CMS / Content",
        "suggestions": [
            "Sanity", "Contentful", "Strapi", "Payload",
            "Prismic", "Storyblok", "Notion API", "none",
        ],
    },
    {
        "key": "monitoring",
        "label": "Monitoring / Error Tracking",
        "suggestions": [
            "Sentry", "LogRocket", "Datadog", "PostHog",
            "Axiom", "Highlight", "none",
        ],
    },
    {
        "key": "payments",
        "label": "Payments",
        "suggestions": [
            "Stripe", "Lemon Squeezy", "Paddle",
            "Braintree", "PayPal", "none",
        ],
    },
    {
        "key": "other",
        "label": "Other tools you always use",
        "suggestions": [
            "Zod", "React Hook Form", "Zustand", "React Query",
            "tRPC", "GraphQL", "Axios", "date-fns", "Lodash",
        ],
        "multi": True,
        "hint": "(comma-separated, or 'skip')",
    },
    {
        "key": "design_direction",
        "label": "Design direction",
        "suggestions": None,
        "hint": 'e.g. "clean and minimal like Linear", "bold and colorful"',
    },
    {
        "key": "name",
        "label": "Your name (for git commits and docs)",
        "suggestions": None,
        "hint": None,
    },
]

# Core stack fields used for the one-line summary
_STACK_KEYS = ("framework", "language", "database", "styling", "package_manager")

# Display labels for --show (key -> short label)
_DISPLAY_LABELS = {
    "framework": "Framework",
    "language": "Language",
    "database": "Database",
    "auth": "Auth",
    "styling": "Styling",
    "package_manager": "Package mgr",
    "testing": "Testing",
    "deployment": "Deployment",
    "email": "Email",
    "jobs": "Jobs/Queues",
    "cms": "CMS",
    "monitoring": "Monitoring",
    "payments": "Payments",
    "other": "Other",
    "design_direction": "Design",
    "name": "Name",
}


def profile_path() -> Path:
    """Return path to ~/.forge/profile.yaml"""
    return Path.home() / ".forge" / "profile.yaml"


def load_profile() -> dict:
    """
    Load profile from ~/.forge/profile.yaml.
    Returns empty dict if profile does not exist.
    Never raises - returns {} on any error.
    """
    try:
        path = profile_path()
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_profile(profile: dict) -> Path:
    """
    Save profile to ~/.forge/profile.yaml.
    Creates ~/.forge/ directory if needed.
    Returns the path where the file was saved.
    """
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = datetime.utcnow().isoformat()
    if "created_at" not in profile:
        profile["created_at"] = profile["updated_at"]
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, default_flow_style=False, sort_keys=False)
    return path


def has_profile() -> bool:
    """Return True if a profile file exists and is non-empty."""
    try:
        path = profile_path()
        if not path.exists():
            return False
        data = load_profile()
        # Must have at least one meaningful key
        return any(data.get(k) for k in _DISPLAY_LABELS)
    except Exception:
        return False


def get_stack_summary(profile: dict) -> str:
    """
    Return a one-line summary of the profile's stack choices.
    Example: "Next.js · TypeScript · Supabase · Tailwind CSS · pnpm"
    Only includes the core stack fields. Skips fields with no value.
    """
    parts = []
    for key in _STACK_KEYS:
        val = profile.get(key, "")
        if val:
            parts.append(val)
    return " · ".join(parts)


def profile_to_claude_md_context(profile: dict) -> str:
    """
    Convert profile to a context string for CLAUDE.md generation.
    Returns a formatted string listing all non-empty profile fields
    that the orchestrator can inject into the CLAUDE.md generation prompt.
    """
    lines = []
    for key, label in _DISPLAY_LABELS.items():
        val = profile.get(key, "")
        if val:
            lines.append(f"  {label}: {val}")
    if not lines:
        return ""
    return "Developer Profile Preferences:\n" + "\n".join(lines)
