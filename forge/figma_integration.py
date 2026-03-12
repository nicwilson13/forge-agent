"""
Figma API integration for Forge.

Fetches design tokens, component names, and frame exports from Figma
to ground task generation in the actual design rather than text descriptions.

Configuration: .forge/figma.json (project-level)
Token: ~/.forge/profile.yaml figma_token field (user-level)

All operations non-fatal. Build continues on any Figma API failure.
Uses Figma REST API v1: https://api.figma.com/v1/

This module imports only stdlib. No forge imports.
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class FigmaConfig:
    enabled: bool = False
    file_key: str = ""
    generate_tokens: bool = True
    export_frames: bool = False
    frame_ids: list[str] = field(default_factory=list)


def load_figma_config(project_dir: Path) -> FigmaConfig:
    """Load from .forge/figma.json. Never raises."""
    try:
        path = project_dir / ".forge" / "figma.json"
        if not path.exists():
            return FigmaConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return FigmaConfig()
        return FigmaConfig(
            enabled=data.get("enabled", False),
            file_key=data.get("file_key", ""),
            generate_tokens=data.get("generate_tokens", True),
            export_frames=data.get("export_frames", False),
            frame_ids=data.get("frame_ids", []),
        )
    except Exception:
        return FigmaConfig()


def save_figma_config(project_dir: Path, config: FigmaConfig) -> None:
    """Save to .forge/figma.json. Never raises."""
    try:
        forge_dir = project_dir / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        path = forge_dir / "figma.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def get_figma_token() -> str:
    """
    Read figma_token from ~/.forge/profile.yaml.
    Returns empty string if not set. Never raises.
    """
    try:
        import yaml
        path = Path.home() / ".forge" / "profile.yaml"
        if not path.exists():
            return ""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data.get("figma_token", "") or ""
        return ""
    except Exception:
        return ""


def _figma_request(endpoint: str, token: str) -> dict | None:
    """
    GET request to Figma API.
    Base URL: https://api.figma.com/v1
    Header: X-Figma-Token: {token}
    Timeout: 20s. Never raises.
    """
    try:
        url = f"https://api.figma.com/v1{endpoint}"
        headers = {
            "X-Figma-Token": token,
            "User-Agent": "Forge-Agent",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_file_components(
    config: FigmaConfig,
    token: str,
) -> list[dict]:
    """
    Fetch top-level components from the Figma file.

    GET /files/{file_key}?depth=1
    Returns list of component dicts with: id, name, description.
    Returns empty list on error or if disabled.
    """
    if not config.enabled or not token or not config.file_key:
        return []
    try:
        result = _figma_request(f"/files/{config.file_key}?depth=1", token)
        if not result or "document" not in result:
            return []

        components = []
        # Extract component names from the file's components map
        comp_map = result.get("components", {})
        for comp_id, comp_data in comp_map.items():
            if isinstance(comp_data, dict):
                components.append({
                    "id": comp_id,
                    "name": comp_data.get("name", ""),
                    "description": comp_data.get("description", ""),
                })

        # Also extract top-level frame names from the document
        document = result.get("document", {})
        for page in document.get("children", []):
            if isinstance(page, dict):
                for child in page.get("children", []):
                    if isinstance(child, dict) and child.get("type") in ("FRAME", "COMPONENT"):
                        name = child.get("name", "")
                        if name and not any(c["name"] == name for c in components):
                            components.append({
                                "id": child.get("id", ""),
                                "name": name,
                                "description": "",
                            })

        return components
    except Exception:
        return []


def get_design_variables(
    config: FigmaConfig,
    token: str,
) -> dict:
    """
    Fetch design variables (tokens) from the Figma file.

    GET /files/{file_key}/variables/local
    Returns dict with structure:
    {
        "colors": {"primary": "#6366f1", ...},
        "typography": {"heading": {"family": "Inter", "size": 32}, ...},
        "spacing": {"sm": 8, "md": 16, ...},
    }
    Returns empty dict on error or if API returns no variables.
    Never raises.
    """
    if not config.enabled or not token or not config.file_key:
        return {}
    try:
        result = _figma_request(
            f"/files/{config.file_key}/variables/local", token
        )
        if not result or "meta" not in result:
            return {}

        meta = result["meta"]
        variables = meta.get("variables", {})
        collections = meta.get("variableCollections", {})

        colors = {}
        typography = {}
        spacing = {}

        for var_id, var_data in variables.items():
            if not isinstance(var_data, dict):
                continue
            name = var_data.get("name", "")
            resolved_type = var_data.get("resolvedType", "")
            values = var_data.get("valuesByMode", {})

            # Get the first mode's value
            value = None
            for mode_val in values.values():
                value = mode_val
                break

            if value is None:
                continue

            # Normalize name: replace "/" with camelCase-friendly separator
            clean_name = name.replace("/", "_").replace(" ", "_").lower()

            if resolved_type == "COLOR" and isinstance(value, dict):
                r = round(value.get("r", 0) * 255)
                g = round(value.get("g", 0) * 255)
                b = round(value.get("b", 0) * 255)
                a = value.get("a", 1)
                if a < 1:
                    colors[clean_name] = f"rgba({r}, {g}, {b}, {a:.2f})"
                else:
                    colors[clean_name] = f"#{r:02x}{g:02x}{b:02x}"
            elif resolved_type == "FLOAT" and isinstance(value, (int, float)):
                # Heuristic: spacing values are typically small numbers
                if "space" in name.lower() or "gap" in name.lower() or "pad" in name.lower():
                    spacing[clean_name] = int(value)
                elif "size" in name.lower() or "font" in name.lower():
                    typography[clean_name] = {"size": int(value)}
                else:
                    spacing[clean_name] = int(value)
            elif resolved_type == "STRING" and isinstance(value, str):
                if "font" in name.lower() or "family" in name.lower():
                    typography[clean_name] = {"family": value}

        result_dict = {}
        if colors:
            result_dict["colors"] = colors
        if typography:
            result_dict["typography"] = typography
        if spacing:
            result_dict["spacing"] = spacing
        return result_dict
    except Exception:
        return {}


def generate_token_file(variables: dict, project_dir: Path) -> Path | None:
    """
    Generate src/lib/design-tokens.ts from extracted variables.

    Returns path to written file, or None if no variables to write.
    Creates src/lib/ directory if needed.
    Never raises.
    """
    if not variables:
        return None

    colors = variables.get("colors", {})
    typography = variables.get("typography", {})
    spacing = variables.get("spacing", {})

    if not colors and not typography and not spacing:
        return None

    try:
        lines = [
            "// Auto-generated by Forge from Figma design tokens",
            "// Do not edit manually - regenerate with: forge figma-sync",
            "",
        ]

        if colors:
            lines.append("export const colors = {")
            for name, value in colors.items():
                lines.append(f"  {name}: '{value}',")
            lines.append("} as const")
            lines.append("")

        if typography:
            lines.append("export const typography = {")
            for name, value in typography.items():
                if isinstance(value, dict):
                    parts = ", ".join(
                        f"{k}: {repr(v)}" if isinstance(v, str) else f"{k}: {v}"
                        for k, v in value.items()
                    )
                    lines.append(f"  {name}: {{ {parts} }},")
                else:
                    lines.append(f"  {name}: {repr(value)},")
            lines.append("} as const")
            lines.append("")

        if spacing:
            lines.append("export const spacing = {")
            for name, value in spacing.items():
                lines.append(f"  {name}: {value},")
            lines.append("} as const")
            lines.append("")

        # Type exports
        if colors:
            lines.append("export type Colors = typeof colors")
        if spacing:
            lines.append("export type Spacing = typeof spacing")
        if colors or spacing:
            lines.append("")

        target_dir = project_dir / "src" / "lib"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "design-tokens.ts"
        tmp = target.with_suffix(".ts.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        tmp.replace(target)
        return target
    except Exception:
        return None


def format_components_context(components: list[dict]) -> str:
    """
    Format component list as context for task generation.

    Returns markdown string:
    ## Figma Components (use these names in code)
    - Button
    - Card
    ...
    """
    if not components:
        return ""

    lines = ["## Figma Components (use these names in code)"]
    for comp in components:
        name = comp.get("name", "")
        if name:
            desc = comp.get("description", "")
            if desc:
                lines.append(f"- {name}: {desc}")
            else:
                lines.append(f"- {name}")

    return "\n".join(lines)


def run_figma_integration(
    project_dir: Path,
) -> tuple[str, list[dict]]:
    """
    Full Figma integration pipeline.

    1. Load config and token
    2. get_file_components()
    3. If generate_tokens: get_design_variables() + generate_token_file()
    4. Return (components_context, components_list)

    Returns ("", []) on any error or if disabled.
    Prints status to stdout.
    Never raises.
    """
    try:
        config = load_figma_config(project_dir)
        if not config.enabled:
            return ("", [])

        token = get_figma_token()
        if not token:
            print("  (Figma integration enabled but figma_token not set)")
            return ("", [])

        if not config.file_key:
            print("  (Figma integration enabled but file_key not set)")
            return ("", [])

        # Fetch components
        print(f"  [figma] Fetching components from file {config.file_key[:8]}...")
        components = get_file_components(config, token)
        if components:
            names_preview = ", ".join(
                c["name"] for c in components[:5]
            )
            suffix = ", ..." if len(components) > 5 else ""
            print(f"  [figma] {len(components)} components found ({names_preview}{suffix})")
        else:
            print("  [figma] No components found in file")

        # Generate design tokens
        if config.generate_tokens:
            print(f"  [figma] Fetching design tokens...")
            variables = get_design_variables(config, token)
            if variables:
                color_count = len(variables.get("colors", {}))
                type_count = len(variables.get("typography", {}))
                space_count = len(variables.get("spacing", {}))
                parts = []
                if color_count:
                    parts.append(f"{color_count} color tokens")
                if type_count:
                    parts.append(f"{type_count} type styles")
                if space_count:
                    parts.append(f"{space_count} spacing values")
                if parts:
                    print(f"  [figma] {', '.join(parts)}")

                token_path = generate_token_file(variables, project_dir)
                if token_path:
                    try:
                        rel = token_path.relative_to(project_dir)
                    except ValueError:
                        rel = token_path
                    print(f"  [figma] Generated: {rel}")
            else:
                print("  [figma] No design variables found")

        context = format_components_context(components)
        return (context, components)
    except Exception:
        return ("", [])
