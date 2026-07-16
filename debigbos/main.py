"""de BigBos — AI-powered CLI assistant with soul, memory, skills, and multi-model support.

Usage:
    deBigBos                    # Interactive TUI mode
    deBigBos run "query"        # Headless single-query mode
    deBigBos -w /path/to/proj   # Use specific workspace
    deBigBos --model gpt-4o      # Use specific model
    deBigBos --server            # Start HTTP API server (future)
    deBigBos init                # Initialize .debigbos/ config
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="deBigBos",
        description="AI-powered CLI assistant with persistent memory, soul, and multi-model support",
    )
    parser.add_argument("-w", "--workspace", type=str, default=".",
                        help="Workspace directory (default: current)")
    parser.add_argument("-m", "--model", type=str, default="",
                        help="Model to use")
    parser.add_argument("-p", "--provider", type=str, default="",
                        help="Provider to use")

    subparsers = parser.add_subparsers(dest="command")

    # Default / TUI mode
    default_parser = subparsers.add_parser("chat", help="Start interactive TUI (default)")

    # Run (headless)
    run_parser = subparsers.add_parser("run", help="Run a single query (headless)")
    run_parser.add_argument("query", nargs="+", help="Query to run")

    # Init
    init_parser = subparsers.add_parser("init", help="Initialize .debigbos/ config")

    # Install (global setup)
    install_parser = subparsers.add_parser("install", help="Install global config + package")

    # Common flags
    for p in [default_parser, run_parser, init_parser]:
        p.add_argument("-w", "--workspace", type=str, default=".",
                       help="Workspace directory (default: current)")

    for p in [default_parser, run_parser]:
        p.add_argument("-m", "--model", type=str, default="",
                       help="Model to use (e.g., gpt-4o, claude-sonnet-4-20250514)")
        p.add_argument("-p", "--provider", type=str, default="",
                       help="Provider to use (openai, anthropic, ollama)")
        p.add_argument("-r", "--reasoning", type=str, default="medium",
                       choices=["low", "medium", "high"],
                       help="Reasoning effort level")
        p.add_argument("--auto", action="store_true",
                       help="Auto-approve all tool calls")
        p.add_argument("--raw", action="store_true",
                       help="Show raw tool outputs")

    # Server
    server_parser = subparsers.add_parser("server", help="Start HTTP API server (soon)")
    server_parser.add_argument("-w", "--workspace", type=str, default=".")

    # Setup (interactive)
    setup_parser = subparsers.add_parser("setup", help="Interactive setup: pick model, set API key")
    setup_parser.add_argument("-w", "--workspace", type=str, default=".",
                              help="Workspace directory (default: current)")
    setup_parser.add_argument("--global", action="store_true", dest="global_config",
                              help="Apply to global config (~/.config/deBigBos/)")

    # Import
    import_parser = subparsers.add_parser("import", help="Import sessions from Hermes or OpenCode")
    import_parser.add_argument("source", choices=["hermes", "opencode", "all"],
                               help="Source to import from")
    import_parser.add_argument("-w", "--workspace", type=str, default=".",
                               help="Workspace directory (default: current)")
    import_parser.add_argument("--path", type=str, default="",
                               help="Custom path to source DB file")
    import_parser.add_argument("--dry-run", action="store_true",
                               help="Preview without importing")

    # Sessions (list/rename)
    sessions_parser = subparsers.add_parser("sessions", help="List or rename sessions")
    sessions_parser.add_argument("action", nargs="?", choices=["list", "rename", "fix"], default="list",
                                 help="list, rename, or fix corrupted sessions")
    sessions_parser.add_argument("id_or_title", nargs="*", default=[],
                                 help="Session ID (for rename: ID new_title)")
    sessions_parser.add_argument("-w", "--workspace", type=str, default=".",
                                 help="Workspace directory (default: current)")

    # Configure
    config_parser = subparsers.add_parser("configure", help="View or change config")
    config_parser.add_argument("-w", "--workspace", type=str, default=".",
                               help="Workspace directory (default: current)")
    config_parser.add_argument("-m", "--model", type=str, default="",
                               help="Set active model")
    config_parser.add_argument("-p", "--provider", type=str, default="",
                               help="Set active provider")
    config_parser.add_argument("--key", type=str, default="",
                               metavar="PROVIDER=KEY",
                               help="Set API key for provider (use env var name)")
    config_parser.add_argument("--list", action="store_true", dest="list_config",
                               help="Show current configuration")
    config_parser.add_argument("--global", action="store_true", dest="global_config",
                               help="Apply to global config (~/.config/deBigBos/)")
    config_parser.add_argument("--soul-name", type=str, default="",
                               help="Set soul/agent name")
    config_parser.add_argument("--soul-tone", type=str, default="",
                                help="Set soul tone")

    # Version
    version_parser = subparsers.add_parser("version", help="Show version info")
    version_parser.add_argument("--list", action="store_true", help="List installed versions")

    # Update
    update_parser = subparsers.add_parser("update", help="Check for and install updates")
    update_parser.add_argument("--check", action="store_true", help="Check only, don't install")

    # Uninstall
    uninstall_parser = subparsers.add_parser("uninstall", help="Remove de BigBos")
    uninstall_parser.add_argument("--keep-config", action="store_true", default=True, help="Keep config files")
    uninstall_parser.add_argument("--yes", action="store_true", help="Skip confirmation")

    return parser.parse_args()


async def run_headless(workspace: Path, query: str, model: str = "",
                       provider: str = "", auto: bool = False) -> None:
    """Run a single query without TUI."""
    from debigbos.core.agent import BigBosAgent

    agent = BigBosAgent(workspace)
    await agent.initialize()

    if model:
        agent.config.active_model = model
    if provider:
        agent.config.active_provider = provider
    agent.config.auto_approve = auto

    print(f"de BigBos ({agent.config.active_provider}/{agent.config.active_model})")
    print(f"Workspace: {workspace}")
    print()

    # Always start fresh for headless mode
    agent.start_session()
    async for chunk in agent.stream_chat(query):
        try:
            print(chunk, end="", flush=True)
        except UnicodeEncodeError:
            print(chunk.encode("ascii", errors="replace").decode("ascii"), end="", flush=True)
    print()
    agent.shutdown()


async def run_init(workspace: Path) -> None:
    """Initialize .debigbos/ configuration directory."""
    bigbos_dir = workspace / ".debigbos"
    bigbos_dir.mkdir(parents=True, exist_ok=True)

    # Create default config
    config_path = workspace / "deBigBos.json"
    if not config_path.exists():
        import json
        default_config = {
            "active_provider": "openai",
            "active_model": "gpt-4o",
            "soul": {
                "name": "de BigBos",
                "persona": "A sharp, witty AI assistant that's direct and concise. You help users build software.",
                "tone": "casual and direct",
                "greeting": "Hey! Ready to ship something awesome?",
            },
        }
        config_path.write_text(json.dumps(default_config, indent=2))
        print(f"Created {config_path}")

    # Create skills dir + example skill
    skills_dir = bigbos_dir / "skills" / "code-review"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    if not skill_file.exists():
        skill_file.write_text("""---
name: code-review
description: Review code for bugs, style, and security
---

# Code Review

When reviewing code, follow this checklist:

1. **Bugs**: Check for logic errors, edge cases, off-by-one errors
2. **Security**: Look for injection vulnerabilities, secrets exposure
3. **Style**: Consistent naming, proper error handling
4. **Performance**: Inefficient loops, missing indexes, N+1 queries

Return findings as a numbered list with severity (🔴 critical, 🟡 warning, 🔵 info).
""", encoding="utf-8")
        print(f"Created example skill: {skills_dir}")
        print("  Load it with /skills then use during chat")

    # Create agents dir
    agents_dir = bigbos_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Create tools dir
    tools_dir = bigbos_dir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[OK] de BigBos initialized at {workspace}")
    print(f"   Config: {config_path}")
    print(f"   Skills: {skills_dir.parent}")
    print(f"   Agents: {agents_dir}")
    print(f"   Tools:  {tools_dir}")
    print(f"\n   Run 'deBigBos' to start!")


async def run_install(workspace: Path) -> None:
    """Install global config to ~/.config/deBigBos/."""
    import shutil

    config_dir = Path.home() / ".config" / "deBigBos"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    # Copy default config if none exists
    source_config = workspace / "deBigBos.json"
    if not source_config.exists():
        print("Error: deBigBos.json not found in current directory")
        return

    if config_file.exists():
        print(f"Global config already exists: {config_file}")
        print("Delete it first to reinstall, or edit it directly.")
    else:
        shutil.copy(source_config, config_file)
        print(f"Global config installed: {config_file}")

    print()
    print("Priority (highest to lowest):")
    print("  1. Project: .debigbos/config.json")
    print("  2. Project: deBigBos.json")
    print(f"  3. Global:  {config_file}")
    print()
    print("Set your API keys as environment variables:")
    print("  set OPENAI_API_KEY=sk-...")
    print("  set ANTHROPIC_API_KEY=sk-ant-...")


def _save_to_env(var_name: str, value: str) -> None:
    """Save a value to a persistent environment variable."""
    import platform
    system = platform.system()
    if system == "Windows":
        import subprocess
        subprocess.run(f'setx {var_name} "{value}"', capture_output=True, shell=True)
    else:
        profile = os.path.expanduser("~/.profile")
        print(f"\n  To make it permanent, add this line to {profile}:")
        print(f"  export {var_name}='{value}'")
        print(f"  Then run: source {profile}")
    os.environ[var_name] = value


PROVIDER_CATALOG = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini", "o1", "o4-mini"],
        "env_var": "OPENAI_API_KEY",
        "desc": "OpenAI - GPT-4o, o3, o1",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
        "env_var": "ANTHROPIC_API_KEY",
        "desc": "Anthropic - Claude Sonnet, Opus",
    },
    "opencode-zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "qwen-plus", "qwen-max"],
        "env_var": "OPENCODE_ZEN_API_KEY",
        "desc": "OpenCode Zen - DeepSeek, Qwen (free tier available)",
    },
        "opencode-go": {
            "base_url": "https://opencode.ai/zen/go/v1",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3", "deepseek-v3.2",
                       "qwen-plus", "qwen-max", "qwen3.5-397b",
                       "kimi-k2", "kimi-k2.6",
                       "glm-4", "glm5",
                       "minimax-m1", "minimax-m2.5", "minimax-m2.7", "minimax-m3",
                       "mimo-v2", "mistral-large-3"],
            "env_var": "OPENCODE_GO_API_KEY",
            "desc": "OpenCode Go - DeepSeek, Qwen, Kimi, GLM, MiniMax, MiMo ($10/mo)",
        },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat", "google/gemini-flash-1.5"],
        "env_var": "OPENROUTER_API_KEY",
        "desc": "OpenRouter - all models, pay-per-token",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.1-70b", "mixtral-8x7b", "gemma2-9b", "deepseek-r1-distill-llama-70b"],
        "env_var": "GROQ_API_KEY",
        "desc": "Groq - fast inference, free tier",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "models": ["llama3.1", "qwen2.5", "deepseek-r1", "codellama", "phi3"],
        "env_var": None,
        "desc": "Ollama - local, free, offline (no API key needed)",
    },
}


async def run_setup(args: argparse.Namespace) -> None:
    """Interactive setup - pick provider, model, enter API key with a nice menu."""
    import json
    from debigbos.config.auth import get_auth_manager

    auth = get_auth_manager()
    workspace = Path(args.workspace).resolve()
    if getattr(args, "global_config", False):
        config_path = Path.home() / ".config" / "deBigBos" / "config.json"
    else:
        config_path = workspace / "deBigBos.json"

    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}

    print()
    print("=" * 50)
    print("  de BigBos - SETUP")
    print("=" * 50)
    print()

    # Step 1: Pick provider
    providers = list(PROVIDER_CATALOG.keys())
    print("Choose a provider:")
    print()
    for i, pname in enumerate(providers, 1):
        info = PROVIDER_CATALOG[pname]
        tag = ""
        if config.get("active_provider") == pname:
            tag = " [CURRENT]"
        # Check if has API key (from env, auth.json, or config)
        has_key = False
        prov_cfg = config.get("providers", {}).get(pname, {})
        api_key = prov_cfg.get("api_key", "")
        if api_key and not api_key.startswith("${") and api_key.strip():
            has_key = True
        elif info["env_var"] and os.environ.get(info["env_var"], ""):
            has_key = True
        elif auth.get_key(pname):
            has_key = True
        elif info["env_var"] is None:  # ollama
            has_key = True

        key_status = " [key OK]" if has_key else " [no key]"
        print(f"  {i}. {pname}{tag}{key_status}")
        print(f"     {info['desc']}")

    print()
    choice = input("Pick number (1-{}) [1]: ".format(len(providers))).strip()
    if not choice:
        choice = "1"
    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(providers):
            print("Invalid choice.")
            return
    except ValueError:
        print("Invalid input.")
        return

    provider = providers[idx]
    info = PROVIDER_CATALOG[provider]

    # Step 2: API key / base URL — needed to fetch live models
    print()
    resolved_key = ""
    if info["env_var"]:
        resolved_key = os.environ.get(info["env_var"], "") or auth.get_key(provider) or ""
        if not resolved_key:
            prov_cfg = config.get("providers", {}).get(provider, {})
            raw = prov_cfg.get("api_key", "")
            if raw and not raw.startswith("${"):
                resolved_key = raw
        if not resolved_key:
            print(f"Enter API key for {provider} (or set ${info['env_var']}):")
            resolved_key = input("  API key: ").strip()

    # If we got a key from any source, persist it to avoid double-ask later
    if resolved_key and info["env_var"]:
        os.environ[info["env_var"]] = resolved_key
        auth.set_key(provider, resolved_key, info.get("base_url", ""))

    # Step 3: Try to fetch live models from endpoint
    live_models: list[str] = []
    if info.get("base_url"):  # No API key needed for Ollama etc.
        try:
            import httpx
            headers = {}
            if resolved_key:
                headers["Authorization"] = f"Bearer {resolved_key}"
            url = info["base_url"].rstrip("/") + "/models"
            resp = httpx.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get("data", []):
                    mid = m.get("id", "")
                    if mid and not any(skip in mid for skip in ["embedding", "tts", "whisper", "dall-e"]):
                        live_models.append(mid)
                if live_models:
                    live_models.sort()
        except Exception:
            pass

    if live_models:
        model_list = live_models
        print(f"\n  [fetched {len(live_models)} models from endpoint]")
    else:
        model_list = info["models"]
        print(f"\n  [using default model list — live fetch failed]")

    # Step 4: Pick model
    print()
    print(f"Models for [{provider}]:")
    print()
    current_model = config.get("active_model", "")
    for j, model in enumerate(model_list, 1):
        tag = " [CURRENT]" if current_model == model else ""
        print(f"  {j}. {model}{tag}")

    print()
    model_choice = input(f"Pick model (1-{len(model_list)}) [1]: ").strip()
    if not model_choice:
        model_choice = "1"
    try:
        midx = int(model_choice) - 1
        if midx < 0 or midx >= len(model_list):
            print("Invalid choice.")
            return
    except ValueError:
        print("Invalid input.")
        return

    model = model_list[midx]

    # Save API key
    print()
    if info["env_var"]:
        env_val = os.environ.get(info["env_var"], "")
        auth_key = auth.get_key(provider)
        existing_key = ""
        if prov_cfg := config.get("providers", {}).get(provider, {}):
            existing_key = prov_cfg.get("api_key", "")

        is_hardcoded = existing_key and not existing_key.startswith("${")
        is_env_set = bool(env_val)
        has_auth_key = bool(auth_key)

        if is_hardcoded:
            print(f"API key is hardcoded in config file (not safe for git).")
            change = input("Move to auth.json instead? (y/n) [y]: ").strip().lower()
            if change != "n":
                auth.set_key(provider, existing_key, info["base_url"])
                # Update config to reference env var
                if "providers" not in config:
                    config["providers"] = {}
                if provider not in config["providers"]:
                    config["providers"][provider] = {}
                config["providers"][provider]["api_key"] = f"${{{info['env_var']}}}"
                print(f"Saved to ~/.config/deBigBos/auth.json")
        elif is_env_set:
            print(f"API key found in ${info['env_var']} (env var)")
            save_to_auth = input("Also save to auth.json for persistence? (y/n) [y]: ").strip().lower()
            if save_to_auth != "n":
                auth.set_key(provider, env_val, info["base_url"])
                print(f"Saved to ~/.config/deBigBos/auth.json")
        elif has_auth_key:
            print(f"API key found in ~/.config/deBigBos/auth.json")
        else:
            print(f"No API key found for {provider}.")
            print(f"  Stored in: ~/.config/deBigBos/auth.json")
            print(f"  Or set env var (temporary): set {info['env_var']}=<your-key>")
            print()
            key_input = input(f"Enter API key for {provider} (press Enter to skip): ").strip()
            if key_input:
                auth.set_key(provider, key_input, info["base_url"])
                # Also set env var for current session
                os.environ[info["env_var"]] = key_input
                if "providers" not in config:
                    config["providers"] = {}
                if provider not in config["providers"]:
                    config["providers"][provider] = {}
                config["providers"][provider]["api_key"] = f"${{{info['env_var']}}}"
                print(f"Saved to ~/.config/deBigBos/auth.json")
    else:
        # Ollama - no key needed
        pass

    # Auto-add provider config
    if "providers" not in config:
        config["providers"] = {}
    if provider not in config["providers"]:
        config["providers"][provider] = {
            "base_url": info["base_url"],
            "models": model_list,
            "default_model": model_list[0],
        }
        if info["env_var"]:
            config["providers"][provider]["api_key"] = f"${{{info['env_var']}}}"

    # Save config
    config["active_provider"] = provider
    config["active_model"] = model
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    print()
    print("=" * 50)
    print(f"  CONFIGURED!")
    print(f"  Provider: {provider}")
    print(f"  Model:    {model}")
    print(f"  Config:   {config_path}")
    print(f"  Auth:     {auth.AUTH_FILE}")
    print("=" * 50)

    # Check if key is actually available
    if info["env_var"]:
        resolved = auth.resolve_key(provider, info["env_var"])
        if not resolved:
            print()
            print("[!] API key still needed!")
            print(f"    Run again: deBigBos setup")
            print(f"    Or: deBigBos configure --key {provider}=<your-key>")

    print()
    print("Now run: deBigBos")


async def run_configure(args: argparse.Namespace) -> None:
    """Smart config — auto-detect provider, prompt for keys, set URLs."""
    import json

    workspace = Path(args.workspace).resolve()

    if getattr(args, "global_config", False):
        config_path = Path.home() / ".config" / "deBigBos" / "config.json"
    else:
        config_path = workspace / "deBigBos.json"

    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}

    # --list: just show
    if getattr(args, "list_config", False) and not (args.model or args.provider or args.key):
        print(f"Config: {config_path}")
        print(json.dumps(config, indent=2))
        return

    changed = False

    # Smart model detection: find provider that has this model
    if args.model:
        model = args.model.lower().strip()
        found_provider = None
        for pname, pinfo in PROVIDER_CATALOG.items():
            if any(model in m.lower() or m.lower() in model for m in pinfo["models"]):
                found_provider = pname
                break

        if found_provider:
            config["active_model"] = args.model
            config["active_provider"] = found_provider
            print(f"Model: {args.model}")
            print(f"Provider: {found_provider} ({PROVIDER_CATALOG[found_provider]['desc']})")
            changed = True

            # Auto-add provider to config if not exists
            if "providers" not in config:
                config["providers"] = {}
            if found_provider not in config["providers"]:
                pinfo = PROVIDER_CATALOG[found_provider]
                config["providers"][found_provider] = {
                    "base_url": pinfo["base_url"],
                    "models": pinfo["models"],
                    "default_model": pinfo["models"][0],
                }
                if pinfo["env_var"]:
                    config["providers"][found_provider]["api_key"] = f"${{{pinfo['env_var']}}}"
                    env_val = os.environ.get(pinfo["env_var"], "")
                    if env_val:
                        config["providers"][found_provider]["api_key"] = env_val
                        print(f"  API key: found in ${pinfo['env_var']}")
                    else:
                        print(f"  API key: using ${{{pinfo['env_var']}}} (not set yet)")
        else:
            print(f"Model '{args.model}' not found in catalog.")
            print("Available providers & models:")
            for pname, pinfo in PROVIDER_CATALOG.items():
                print(f"  [{pname}] {pinfo['desc']}")
                for m in pinfo["models"]:
                    print(f"    - {m}")
            return

    if args.provider:
        config["active_provider"] = args.provider
        if args.provider in PROVIDER_CATALOG:
            pinfo = PROVIDER_CATALOG[args.provider]
            if "providers" not in config:
                config["providers"] = {}
            if args.provider not in config["providers"]:
                config["providers"][args.provider] = {
                    "base_url": pinfo["base_url"],
                    "models": pinfo["models"],
                    "default_model": pinfo["models"][0],
                }
                if pinfo["env_var"]:
                    config["providers"][args.provider]["api_key"] = f"${{{pinfo['env_var']}}}"
            print(f"Provider: {args.provider} ({pinfo['desc']})")
        else:
            print(f"Provider: {args.provider}")
        changed = True

    if args.key:
        if "=" in args.key:
            provider, key_val = args.key.split("=", 1)
            provider = provider.strip()
            key_val = key_val.strip()
            # Save to auth.json for persistence
            from debigbos.config.auth import get_auth_manager
            auth = get_auth_manager()
            auth.set_key(provider, key_val)
            # Also update config reference
            if "providers" not in config:
                config["providers"] = {}
            if provider not in config["providers"]:
                config["providers"][provider] = {}
            config["providers"][provider]["api_key"] = f"${{{PROVIDER_CATALOG.get(provider, {}).get('env_var', '')}}}"
            print(f"API key saved to ~/.config/deBigBos/auth.json for {provider}")
            changed = True

    if args.soul_name:
        if "soul" not in config:
            config["soul"] = {}
        config["soul"]["name"] = args.soul_name
        print(f"Soul name -> {args.soul_name}")
        changed = True

    if args.soul_tone:
        if "soul" not in config:
            config["soul"] = {}
        config["soul"]["tone"] = args.soul_tone
        print(f"Soul tone -> {args.soul_tone}")
        changed = True

    if changed:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"\nSaved: {config_path}")

        # Remind about API key
        provider = config.get("active_provider", "")
        if provider in PROVIDER_CATALOG and PROVIDER_CATALOG[provider]["env_var"]:
            env_var = PROVIDER_CATALOG[provider]["env_var"]
            prov_cfg = config.get("providers", {}).get(provider, {})
            api_key = prov_cfg.get("api_key", "")
            if api_key.startswith("${"):
                print(f"\n[!] Set your API key:")
                print(f"    set {env_var}=<your-key>")
                print(f"  OR")
                print(f"    deBigBos configure --key {provider}=<your-key>")

    elif not getattr(args, "list_config", False):
        print("No changes. Use --list to view config.")

    if getattr(args, "list_config", False):
        print(json.dumps(config, indent=2))


async def run_import(args: argparse.Namespace) -> None:
    """Import sessions and messages from Hermes or OpenCode into de BigBos memory."""
    import json
    import sqlite3
    import time
    import uuid

    workspace = Path(args.workspace).resolve()
    db_path = workspace / ".debigbos" / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Open target DB
    target = sqlite3.connect(str(db_path))
    target.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT DEFAULT '', summary TEXT DEFAULT '',
            created_at REAL, updated_at REAL, parent_id TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, tool_calls TEXT DEFAULT '[]',
            tool_call_id TEXT, name TEXT, timestamp REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
    """)
    # Add source column if missing
    try:
        target.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Add cost column if missing
    try:
        target.execute("ALTER TABLE sessions ADD COLUMN cost REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass  # column already exists
    target.commit()

    imported_sessions = 0
    imported_messages = 0

    sources = []
    if args.source in ("hermes", "all"):
        sources.append("hermes")
    if args.source in ("opencode", "all"):
        sources.append("opencode")

    for source in sources:
        if source == "hermes":
            hermes_path = args.path
            if not hermes_path:
                candidates = [
                    Path.home() / "AppData" / "Local" / "hermes" / "state.db",     # Windows
                    Path.home() / ".local" / "share" / "hermes" / "state.db",      # Linux
                    Path.home() / "Library" / "Application Support" / "hermes" / "state.db",  # macOS
                ]
                for c in candidates:
                    if c.exists():
                        hermes_path = str(c)
                        break
                else:
                    hermes_path = str(candidates[0])  # report first as "not found"
            if not Path(hermes_path).exists():
                print(f"Hermes DB not found: {hermes_path}")
                continue

            if args.dry_run:
                src = sqlite3.connect(hermes_path)
                sess_count = src.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                msg_count = src.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                print(f"\n[Hermes] {sess_count} sessions, {msg_count} messages (dry run)")
                src.close()
                continue

            src = sqlite3.connect(hermes_path)
            sessions = src.execute(
                "SELECT id, title, started_at, parent_session_id, system_prompt FROM sessions"
            ).fetchall()

            for sess in sessions:
                sid, title, started_at, parent_id, system_prompt = sess
                session_id = f"hermes-{sid[:12]}"

                # Check if already imported
                exists = target.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if not exists:
                    target.execute(
                        "INSERT INTO sessions (id, title, created_at, updated_at, parent_id, source) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, title or "Untitled", started_at or time.time(),
                         time.time(), f"hermes-{parent_id[:12]}" if parent_id else None, "hermes"),
                    )
                    imported_sessions += 1

                    # Import messages
                    msgs = src.execute(
                        "SELECT role, content, tool_calls, tool_call_id, tool_name, timestamp "
                        "FROM messages WHERE session_id = ? ORDER BY id ASC",
                        (sid,),
                    ).fetchall()

                    for msg in msgs:
                        role, content, tool_calls_json, tool_call_id, tool_name, ts = msg
                        target.execute(
                            "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, name, timestamp) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (session_id, role, content or "", tool_calls_json or "[]",
                             tool_call_id, tool_name, ts or time.time()),
                        )
                        imported_messages += 1

            print(f"[Hermes] Imported {imported_sessions} sessions with messages")
            src.close()

        elif source == "opencode":
            oc_path = args.path
            if not oc_path:
                candidates = [
                    Path.home() / ".local" / "share" / "opencode" / "opencode.db",          # Linux
                    Path.home() / "AppData" / "Local" / "opencode" / "opencode.db",         # Windows
                    Path.home() / "Library" / "Application Support" / "opencode" / "opencode.db",  # macOS
                ]
                for c in candidates:
                    if c.exists():
                        oc_path = str(c)
                        break
                else:
                    oc_path = str(candidates[0])
            if not Path(oc_path).exists():
                print(f"OpenCode DB not found: {oc_path}")
                continue

            if args.dry_run:
                src = sqlite3.connect(oc_path)
                sess_count = src.execute("SELECT COUNT(*) FROM session").fetchone()[0]
                msg_count = src.execute("SELECT COUNT(*) FROM message").fetchone()[0]
                print(f"\n[OpenCode] {sess_count} sessions, {msg_count} messages (dry run)")
                src.close()
                continue

            src = sqlite3.connect(oc_path)
            ocs = 0
            ocm = 0

            sessions = src.execute(
                "SELECT id, title, parent_id, time_created, model, summary_diffs FROM session"
            ).fetchall()

            for sess in sessions:
                sid, title, parent_id, time_created, model, summary = sess
                session_id = f"opencode-{sid[:12]}"

                exists = target.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if not exists:
                    target.execute(
                        "INSERT INTO sessions (id, title, summary, created_at, updated_at, parent_id, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (session_id, title or "Untitled", summary or "",
                         time_created or time.time(), time.time(),
                         f"opencode-{parent_id[:12]}" if parent_id else None, "opencode"),
                    )
                    ocs += 1

                    # OpenCode stores messages with parts — join to reconstruct
                    messages_data = src.execute(
                        "SELECT m.data, m.time_created, m.id FROM message m WHERE m.session_id = ? ORDER BY m.time_created ASC",
                        (sid,),
                    ).fetchall()

                    for msg_data, msg_time, msg_id in messages_data:
                        try:
                            mdata = json.loads(msg_data) if isinstance(msg_data, str) else msg_data
                            role = mdata.get("role", "user")

                            # Get parts (actual content blocks)
                            parts = src.execute(
                                "SELECT p.data FROM part p WHERE p.message_id = ? ORDER BY p.time_created ASC",
                                (msg_id,),
                            ).fetchall()

                            content_parts = []
                            for (part_data,) in parts:
                                try:
                                    pdata = json.loads(part_data) if isinstance(part_data, str) else part_data
                                    ptype = pdata.get("type", "")
                                    if ptype == "text":
                                        content_parts.append(pdata.get("text", ""))
                                    elif ptype == "tool-call":
                                        content_parts.append(f"[tool call: {pdata.get('tool', '')}]")
                                    elif ptype == "tool-result":
                                        content_parts.append(f"[tool result]")
                                    elif ptype == "reasoning":
                                        content_parts.append(f"[reasoning: {pdata.get('text', '')[:200]}]")
                                    elif ptype == "step-start":
                                        content_parts.append("[---]")
                                    elif ptype == "step-finish":
                                        content_parts.append("")
                                except Exception:
                                    continue

                            content = "\n".join(p for p in content_parts if p)
                            if content.strip():
                                target.execute(
                                    "INSERT INTO messages (session_id, role, content, tool_calls, timestamp) "
                                    "VALUES (?, ?, ?, '[]', ?)",
                                    (session_id, role, content[:10000], msg_time or time.time()),
                                )
                                ocm += 1
                        except Exception:
                            continue

            print(f"[OpenCode] Imported {ocs} sessions with {ocm} messages")
            imported_sessions += ocs
            imported_messages += ocm
            src.close()

    target.commit()
    target.close()

    print(f"\nDone! Imported {imported_sessions} sessions, {imported_messages} messages.")
    print(f"Saved to: {db_path}")
    print(f"\nNow run 'deBigBos' - imported sessions available via /sessions")


async def run_sessions(args: argparse.Namespace) -> None:
    """List or rename sessions."""
    import sqlite3
    from datetime import datetime

    workspace = Path(args.workspace).resolve()
    db_path = workspace / ".debigbos" / "memory.db"

    if not db_path.exists():
        print("No sessions found. Run 'deBigBos' first.")
        return

    conn = sqlite3.connect(str(db_path))

    if args.action == "list":
        rows = conn.execute(
            "SELECT id, title, source, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()
        if not rows:
            print("No sessions yet.")
        else:
            print(f"{'ID':<12} {'Title':<45} {'Source':<12} {'Updated'}")
            print("-" * 90)
            for row in rows:
                sid = row[0][:12]
                title = (row[1] or "Untitled")[:43]
                source = (row[2] or "")[:10]
                dt = datetime.fromtimestamp(row[4]).strftime("%Y-%m-%d %H:%M") if row[4] else ""
                print(f"{sid:<12} {title:<45} {source:<12} {dt}")
            print(f"\nUse: deBigBos sessions rename <id> <new_title>")

    elif args.action == "rename":
        if len(args.id_or_title) < 2:
            print("Usage: deBigBos sessions rename <session_id> <new_title>")
            conn.close()
            return
        sid = args.id_or_title[0]
        new_title = " ".join(args.id_or_title[1:])
        t = __import__("time").time()

        # Try exact match first
        conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                     (new_title, t, sid))
        if conn.total_changes > 0:
            print(f"Renamed to: {new_title}")
        else:
            # Try partial match
            matched = conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? LIMIT 2", (f"%{sid}%",)
            ).fetchall()
            if len(matched) == 1:
                conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                             (new_title, t, matched[0][0]))
                print(f"Renamed {matched[0][0][:16]}... to: {new_title}")
            elif len(matched) > 1:
                print(f"Multiple matches for '{sid}'. Use more characters or exact ID:")
                for m in matched[:5]:
                    print(f"  {m[0]}")
            else:
                print(f"Session '{sid}' not found. Use 'deBigBos sessions list' to see IDs.")
        conn.commit()

    elif args.action == "fix":
        # Remove sessions with 0 messages (corrupted/empty)
        deleted = conn.execute(
            "DELETE FROM sessions WHERE id NOT IN (SELECT DISTINCT session_id FROM messages)"
        ).rowcount
        conn.commit()
        print(f"Cleaned {deleted} empty/corrupted sessions.")
        # Also remove duplicate sessions
        conn.execute("""
            DELETE FROM sessions WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM sessions GROUP BY id
            )
        """)
        conn.commit()
        if conn.total_changes > 0:
            print(f"Removed {conn.total_changes} duplicate entries.")
        print("Run 'deBigBos sessions list' to see remaining sessions.")

    conn.close()


async def run_update(args: argparse.Namespace) -> None:
    """Check for and install updates."""
    from debigbos.core.updater import Updater
    from debigbos import __version__ as local_ver
    u = Updater()
    print(f"  deBigBos v{local_ver}")
    print(f"  Git: {u.get_current_git_ref()}")

    if not u.repo_path:
        print("  Error: Repository not found.")
        return

    if getattr(args, "check", False):
        update_info = u.check()
        if update_info:
            print(f"  Update available: {update_info}")
        else:
            print("  Already up to date.")
        return

    print("  Checking GitHub + git...")
    update_info = u.check(force=True)
    if not update_info:
        print("  Already up to date.")
        # Show recent commits anyway
        import subprocess
        print()
        subprocess.run(["git", "-C", str(u.repo_path), "log", "--oneline", "-5"])
        return

    print(f"\n  {update_info} available!")
    if input("  Update now? [y/n]: ").strip().lower() != "y":
        print("  Skipped.")
        return

    print("  Pulling...")
    if u.update(show_output=True):
        # Show what changed
        import subprocess
        subprocess.run(["git", "-C", str(u.repo_path), "diff", "--stat", "HEAD~1..HEAD"])
        print("\n  Done! Restart de BigBos to apply.")
    else:
        print("  Already up to date or update failed.")


async def run_uninstall(args: argparse.Namespace) -> None:
    """Remove de BigBos installation."""
    if not getattr(args, "yes", False):
        print("  Remove de BigBos from your system?")
        print("  Config (~/.config/deBigBos/) + .debigbos/ folders kept.")
        if input("  Continue? [y/N]: ").strip().lower() != "y":
            print("  Cancelled.")
            return

    import shutil
    tb_home = os.environ.get("deBigBos_HOME") or str(Path.home() / ".local" / "share" / "deBigBos")
    bin_dir = str(Path.home() / ".local" / "bin")
    removed = []

    if os.path.exists(tb_home):
        shutil.rmtree(tb_home, ignore_errors=True)
        removed.append(tb_home)
    for s in ["deBigBos", "deBigBos.bat", "deBigBos.ps1"]:
        p = os.path.join(bin_dir, s)
        if os.path.exists(p):
            os.remove(p)
            removed.append(p)

    print(f"  Removed: {', '.join(removed) if removed else '(nothing to remove)'}")
    print("  Config + .debigbos/ folders kept.")


async def main_async() -> None:
    """Async entry point."""
    args = parse_args()

    workspace = Path(args.workspace).resolve()

    if args.command == "init":
        await run_init(workspace)
        return

    if args.command == "install":
        await run_install(workspace)
        return

    if args.command == "setup":
        await run_setup(args)
        return

    if args.command == "import":
        await run_import(args)
        return

    if args.command == "sessions":
        await run_sessions(args)
        return

    if args.command == "configure":
        await run_configure(args)
        return

    if args.command == "run":
        query = " ".join(args.query)
        model = getattr(args, "model", "")
        provider = getattr(args, "provider", "")
        auto = getattr(args, "auto", False)
        await run_headless(workspace, query, model, provider, auto)
        return

    if args.command == "server":
        print("Server mode not implemented yet. Coming soon!")
        return

    if args.command == "version":
        from debigbos.core.updater import Updater
        from debigbos import get_version_string, get_build_number
        u = Updater()
        print(f"  deBigBos {get_version_string()}")
        print(f"  Repo: https://github.com/ragungnoviandri/deBigBos")
        print(f"  Build: {get_build_number()} commits")
        print(f"  Git: {u.get_current_git_ref()}")
        print(f"  Repo path: {u.repo_path or 'Not found'}")
        if getattr(args, "list", False):
            import subprocess
            try:
                subprocess.run(["git", "-C", str(u.repo_path), "tag", "--sort=-creatordate"], check=False)
            except Exception:
                print("  (no tags found)")
        return

    if args.command == "update":
        await run_update(args)
        return

    if args.command == "uninstall":
        await run_uninstall(args)
        return

    # Default: TUI mode (chat or no subcommand)
    from debigbos.tui.app import BigBosTUI
    tui = BigBosTUI(workspace)

    if getattr(args, "model", ""):
        tui.agent = None
    if getattr(args, "raw", False):
        tui.show_raw = True

    await tui.run()


def main() -> None:
    """Synchronous entry point."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
