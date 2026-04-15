#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time
from typing import Any


DEFAULT_SERVER_NAME = "minecraft-ops"
DEFAULT_SKILL_NAME = "minecraft-ops-runbook"


def main() -> int:
    args = parse_args()
    mcp_repo = args.mcp_repo.resolve()
    skill_repo = args.skill_repo.resolve()
    codex_home = args.codex_home.expanduser().resolve()
    codex_bin = shutil.which(args.codex_bin) if not os.path.isabs(args.codex_bin) else args.codex_bin

    env = build_env(args, codex_home)
    validate_paths(mcp_repo, skill_repo)
    if args.write:
        validate_required_env(env)

    if args.print_json:
        print(json.dumps(build_mcp_json(args.server_name, mcp_repo, env, args.python, args.show_secrets), indent=2, ensure_ascii=False))
        if not args.write:
            return 0

    print_plan(args, mcp_repo, skill_repo, codex_home, env, bool(codex_bin))

    if not args.write:
        print("\nDry run only. Re-run with --write to install the skill and register the MCP server.")
        return 0

    if args.install_editable:
        run([args.python, "-m", "pip", "install", "-e", str(mcp_repo)])

    if not args.skip_skill:
        install_skill(skill_repo, codex_home / "skills" / args.skill_name, args.skill_mode, args.replace)

    if not args.skip_mcp:
        if not codex_bin:
            raise SystemExit("codex CLI not found. Install Codex or pass --codex-bin.")
        env_file = args.env_file.expanduser().resolve() if args.env_file else codex_home / "minecraft-ops-mcp.env"
        launcher = args.launcher.expanduser().resolve() if args.launcher else codex_home / "bin" / "minecraft-ops-mcp-launch"
        write_env_file(env_file, env, args.replace)
        write_launcher(launcher, env_file, mcp_repo, args.python, args.replace)
        register_codex_mcp(codex_bin, args.server_name, launcher, codex_home, args.replace, env)

    print("\nDone. Restart Codex to pick up newly installed skills and MCP server changes.")
    return 0


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    default_skill_repo = root.parent / DEFAULT_SKILL_NAME
    parser = argparse.ArgumentParser(
        description="Configure minecraft-ops-mcp and install the minecraft-ops-runbook skill for Codex.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--write", action="store_true", help="Actually write files and run codex mcp add. Omit for dry-run.")
    parser.add_argument("--replace", action="store_true", help="Replace existing MCP server, env/launcher files, or installed skill after making backups.")
    parser.add_argument("--skip-mcp", action="store_true", help="Do not register the MCP server.")
    parser.add_argument("--skip-skill", action="store_true", help="Do not install the skill.")
    parser.add_argument("--print-json", action="store_true", help="Print a generic MCP client JSON snippet.")
    parser.add_argument("--show-secrets", action="store_true", help="Include secret values in --print-json output.")
    parser.add_argument("--install-editable", action="store_true", help="Run python -m pip install -e <mcp-repo> before registering.")

    parser.add_argument("--mcp-repo", type=Path, default=root, help="Path to the minecraft-ops-mcp repository.")
    parser.add_argument("--skill-repo", type=Path, default=default_skill_repo, help="Path to the minecraft-ops-runbook skill repository.")
    parser.add_argument("--skill-name", default=DEFAULT_SKILL_NAME, help="Destination skill directory name.")
    parser.add_argument("--skill-mode", choices=("copy", "symlink"), default="copy", help="Install skill by copy or symlink.")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", "~/.codex")), help="Codex home directory.")
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI executable.")
    parser.add_argument("--server-name", default=DEFAULT_SERVER_NAME, help="Codex MCP server name.")
    parser.add_argument("--python", default=sys.executable or "python3", help="Python executable used to launch the MCP server.")
    parser.add_argument("--env-file", type=Path, help="Path for the generated 0600 env file.")
    parser.add_argument("--launcher", type=Path, help="Path for the generated launcher script.")

    parser.add_argument("--mcp-transport", choices=("stdio", "sse", "streamable-http"), default=os.environ.get("MINECRAFT_OPS_MCP_TRANSPORT", "stdio"))
    parser.add_argument("--mcp-http-host", default=os.environ.get("MINECRAFT_OPS_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--mcp-http-port", default=os.environ.get("MINECRAFT_OPS_MCP_PORT", "8000"))
    parser.add_argument("--mcp-sse-path", default=os.environ.get("MINECRAFT_OPS_MCP_SSE_PATH", "/sse"))
    parser.add_argument("--mcp-message-path", default=os.environ.get("MINECRAFT_OPS_MCP_MESSAGE_PATH", "/messages/"))
    parser.add_argument("--mcp-streamable-http-path", default=os.environ.get("MINECRAFT_OPS_MCP_STREAMABLE_HTTP_PATH", "/mcp"))
    parser.add_argument("--mcp-allowed-hosts", default=os.environ.get("MINECRAFT_OPS_MCP_ALLOWED_HOSTS", ""))
    parser.add_argument("--mcp-allowed-origins", default=os.environ.get("MINECRAFT_OPS_MCP_ALLOWED_ORIGINS", ""))
    parser.add_argument("--mcp-http-bearer-token", default=os.environ.get("MINECRAFT_OPS_MCP_BEARER_TOKEN", ""), help="Bearer token for HTTP transports. Prefer env input to avoid shell history.")
    parser.add_argument("--allow-unauthenticated-http", action="store_true", default=os.environ.get("MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP", "").lower() in {"1", "true", "yes", "on"})

    parser.add_argument("--mcsm-base-url", default=os.environ.get("MCSM_BASE_URL", ""), help="MCSManager base URL.")
    parser.add_argument("--mcsm-api-key", default=os.environ.get("MCSM_API_KEY", ""), help="MCSManager API key. Prefer passing through env to avoid shell history.")
    parser.add_argument("--default-daemon-id", default=os.environ.get("MCSM_DEFAULT_DAEMON_ID", ""), help="Default MCSManager daemon id.")
    parser.add_argument("--default-instance-uuid", default=os.environ.get("MCSM_DEFAULT_INSTANCE_UUID", ""), help="Default instance UUID.")
    parser.add_argument("--mcsm-timeout-seconds", default=os.environ.get("MCSM_TIMEOUT_SECONDS", "10"))

    parser.add_argument("--audit-log", default=os.environ.get("MINECRAFT_OPS_AUDIT_LOG", "/tmp/minecraft-ops-mcp-audit.jsonl"))
    parser.add_argument("--raw-command-allowlist", default=os.environ.get("MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST", "list,time,help"))
    parser.add_argument("--raw-command-denylist", default=os.environ.get("MINECRAFT_OPS_RAW_COMMAND_DENYLIST", "stop,op,deop,ban,ban-ip"))
    parser.add_argument("--max-bytes", default=os.environ.get("MINECRAFT_OPS_MAX_BYTES", "268435456"))
    parser.add_argument("--upload-allowed-dirs", default=os.environ.get("MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS", "/tmp/minecraft-ops-mcp-files"))
    parser.add_argument("--file-operation-whitelist", default=os.environ.get("MINECRAFT_OPS_FILE_OPERATION_WHITELIST", "server.properties,config,mods,logs,crash-reports,world"))
    parser.add_argument("--upload-url-allowed-domains", default=os.environ.get("MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS", ""))
    parser.add_argument("--modpack-workspace", default=os.environ.get("MINECRAFT_OPS_MODPACK_WORKSPACE", ""))
    parser.add_argument("--rcon-timeout-seconds", default=os.environ.get("MINECRAFT_OPS_RCON_TIMEOUT_SECONDS", "5"))
    parser.add_argument("--rcon-encoding", default=os.environ.get("MINECRAFT_OPS_RCON_ENCODING", "utf-8"))
    parser.add_argument("--msmp-timeout-seconds", default=os.environ.get("MINECRAFT_OPS_MSMP_TIMEOUT_SECONDS", "8"))
    parser.add_argument("--msmp-tls-verify", default=os.environ.get("MINECRAFT_OPS_MSMP_TLS_VERIFY", "true"))
    return parser.parse_args()


def build_env(args: argparse.Namespace, codex_home: Path) -> dict[str, str]:
    modpack_workspace = args.modpack_workspace or str(codex_home / "minecraft-ops-modpacks")
    return {
        "MCSM_BASE_URL": args.mcsm_base_url.rstrip("/"),
        "MCSM_API_KEY": args.mcsm_api_key,
        "MCSM_DEFAULT_DAEMON_ID": args.default_daemon_id,
        "MCSM_DEFAULT_INSTANCE_UUID": args.default_instance_uuid,
        "MCSM_TIMEOUT_SECONDS": str(args.mcsm_timeout_seconds),
        "MINECRAFT_OPS_AUDIT_LOG": args.audit_log,
        "MINECRAFT_OPS_RAW_COMMAND_ALLOWLIST": args.raw_command_allowlist,
        "MINECRAFT_OPS_RAW_COMMAND_DENYLIST": args.raw_command_denylist,
        "MINECRAFT_OPS_MAX_BYTES": str(args.max_bytes),
        "MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS": args.upload_allowed_dirs,
        "MINECRAFT_OPS_FILE_OPERATION_WHITELIST": args.file_operation_whitelist,
        "MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS": args.upload_url_allowed_domains,
        "MINECRAFT_OPS_MODPACK_WORKSPACE": modpack_workspace,
        "MINECRAFT_OPS_RCON_TIMEOUT_SECONDS": str(args.rcon_timeout_seconds),
        "MINECRAFT_OPS_RCON_ENCODING": args.rcon_encoding,
        "MINECRAFT_OPS_MSMP_TIMEOUT_SECONDS": str(args.msmp_timeout_seconds),
        "MINECRAFT_OPS_MSMP_TLS_VERIFY": args.msmp_tls_verify,
        "MINECRAFT_OPS_MCP_TRANSPORT": args.mcp_transport,
        "MINECRAFT_OPS_MCP_HOST": args.mcp_http_host,
        "MINECRAFT_OPS_MCP_PORT": str(args.mcp_http_port),
        "MINECRAFT_OPS_MCP_SSE_PATH": normalize_path(args.mcp_sse_path, trailing_slash=False),
        "MINECRAFT_OPS_MCP_MESSAGE_PATH": normalize_path(args.mcp_message_path, trailing_slash=True),
        "MINECRAFT_OPS_MCP_STREAMABLE_HTTP_PATH": normalize_path(args.mcp_streamable_http_path, trailing_slash=False),
        "MINECRAFT_OPS_MCP_ALLOWED_HOSTS": args.mcp_allowed_hosts,
        "MINECRAFT_OPS_MCP_ALLOWED_ORIGINS": args.mcp_allowed_origins,
        "MINECRAFT_OPS_MCP_BEARER_TOKEN": args.mcp_http_bearer_token,
        "MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP": "true" if args.allow_unauthenticated_http else "false",
    }


def validate_paths(mcp_repo: Path, skill_repo: Path) -> None:
    if not (mcp_repo / "pyproject.toml").is_file():
        raise SystemExit(f"MCP repo does not look valid: {mcp_repo}")
    if not (mcp_repo / "src" / "minecraft_ops_mcp" / "__main__.py").is_file():
        raise SystemExit(f"MCP package entrypoint not found under: {mcp_repo}")
    if not (skill_repo / "SKILL.md").is_file():
        raise SystemExit(f"Skill repo does not look valid: {skill_repo}")


def validate_required_env(env: dict[str, str]) -> None:
    missing = [key for key in ("MCSM_BASE_URL", "MCSM_API_KEY") if not env.get(key)]
    if missing:
        raise SystemExit(f"Missing required values for --write: {', '.join(missing)}")
    if env.get("MINECRAFT_OPS_MCP_TRANSPORT") in {"sse", "streamable-http"}:
        host = env.get("MINECRAFT_OPS_MCP_HOST", "127.0.0.1")
        unauth_allowed = env.get("MINECRAFT_OPS_MCP_ALLOW_UNAUTHENTICATED_HTTP", "").lower() in {"1", "true", "yes", "on"}
        if requires_http_auth(host) and not env.get("MINECRAFT_OPS_MCP_BEARER_TOKEN") and not unauth_allowed:
            raise SystemExit("HTTP MCP on a non-local host requires --mcp-http-bearer-token or --allow-unauthenticated-http.")


def build_mcp_json(server_name: str, mcp_repo: Path, env: dict[str, str], python: str, show_secrets: bool) -> dict[str, Any]:
    if env.get("MINECRAFT_OPS_MCP_TRANSPORT") == "streamable-http":
        config: dict[str, Any] = {"url": streamable_http_url(env)}
        if env.get("MINECRAFT_OPS_MCP_BEARER_TOKEN"):
            config["bearer_token_env_var"] = "MINECRAFT_OPS_MCP_BEARER_TOKEN"
        return {"mcpServers": {server_name: config}}
    if env.get("MINECRAFT_OPS_MCP_TRANSPORT") == "sse":
        return {"mcpServers": {server_name: {"url": sse_url(env), "transport": "sse"}}}
    json_env = {"PYTHONPATH": str(mcp_repo / "src"), **env}
    if not show_secrets and json_env.get("MCSM_API_KEY"):
        json_env["MCSM_API_KEY"] = "<redacted>"
    return {
        "mcpServers": {
            server_name: {
                "command": python,
                "args": ["-m", "minecraft_ops_mcp"],
                "env": json_env,
            }
        }
    }


def print_plan(args: argparse.Namespace, mcp_repo: Path, skill_repo: Path, codex_home: Path, env: dict[str, str], codex_found: bool) -> None:
    redacted = dict(env)
    if redacted.get("MCSM_API_KEY"):
        redacted["MCSM_API_KEY"] = "<redacted>"
    if redacted.get("MINECRAFT_OPS_MCP_BEARER_TOKEN"):
        redacted["MINECRAFT_OPS_MCP_BEARER_TOKEN"] = "<redacted>"
    print("minecraft-ops quick setup plan")
    print(f"- MCP repo: {mcp_repo}")
    print(f"- Skill repo: {skill_repo}")
    print(f"- Codex home: {codex_home}")
    print(f"- Codex CLI found: {codex_found}")
    print(f"- MCP server name: {args.server_name}")
    print(f"- MCP transport: {args.mcp_transport}")
    if args.mcp_transport == "sse":
        print(f"- SSE URL: {sse_url(env)}")
    if args.mcp_transport == "streamable-http":
        print(f"- Streamable HTTP URL: {streamable_http_url(env)}")
    print(f"- Install skill: {not args.skip_skill} ({args.skill_mode})")
    print(f"- Register MCP: {not args.skip_mcp}")
    print("- Environment:")
    for key in sorted(redacted):
        value = redacted[key]
        if value:
            print(f"  {key}={value}")


def install_skill(skill_repo: Path, dest: Path, mode: str, replace: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        if not replace:
            raise SystemExit(f"Skill destination exists: {dest}. Re-run with --replace to update it.")
        backup_path(dest)
    if mode == "symlink":
        os.symlink(skill_repo, dest, target_is_directory=True)
    else:
        ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")
        shutil.copytree(skill_repo, dest, ignore=ignore)
    print(f"Installed skill: {dest}")


def write_env_file(path: Path, env: dict[str, str], replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise SystemExit(f"Env file exists: {path}. Re-run with --replace to update it.")
    if path.exists():
        backup_path(path)
    lines = [
        "# Generated by minecraft-ops-mcp scripts/quick_setup.py",
        "# Contains secrets. Keep permissions at 0600.",
    ]
    for key in sorted(env):
        lines.append(f"{key}={shlex.quote(env[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    create_guardrail_dirs(env)
    print(f"Wrote env file: {path}")


def write_launcher(path: Path, env_file: Path, mcp_repo: Path, python: str, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise SystemExit(f"Launcher exists: {path}. Re-run with --replace to update it.")
    if path.exists():
        backup_path(path)
    content = f"""#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH={shlex.quote(str(mcp_repo / "src"))}${{PYTHONPATH:+:${{PYTHONPATH}}}}
if [ -f {shlex.quote(str(env_file))} ]; then
  set -a
  # shellcheck disable=SC1090
  source {shlex.quote(str(env_file))}
  set +a
fi
exec {shlex.quote(python)} -m minecraft_ops_mcp "$@"
"""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)
    print(f"Wrote launcher: {path}")


def create_guardrail_dirs(env: dict[str, str]) -> None:
    for key in ("MINECRAFT_OPS_MODPACK_WORKSPACE", "MINECRAFT_OPS_AUDIT_LOG"):
        value = env.get(key, "")
        if not value:
            continue
        path = Path(value).expanduser()
        target = path.parent if key.endswith("AUDIT_LOG") else path
        target.mkdir(parents=True, exist_ok=True)
    for raw_path in env.get("MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS", "").split(","):
        raw_path = raw_path.strip()
        if raw_path:
            Path(raw_path).expanduser().mkdir(parents=True, exist_ok=True)


def register_codex_mcp(codex_bin: str, server_name: str, launcher: Path, codex_home: Path, replace: bool, env: dict[str, str]) -> None:
    run_env = {**os.environ, "CODEX_HOME": str(codex_home)}
    if replace:
        subprocess.run([codex_bin, "mcp", "remove", server_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=run_env)
    transport = env.get("MINECRAFT_OPS_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        run([codex_bin, "mcp", "add", server_name, "--", str(launcher)], env=run_env)
    elif transport == "streamable-http":
        cmd = [codex_bin, "mcp", "add", server_name, "--url", streamable_http_url(env)]
        if env.get("MINECRAFT_OPS_MCP_BEARER_TOKEN"):
            cmd.extend(["--bearer-token-env-var", "MINECRAFT_OPS_MCP_BEARER_TOKEN"])
        run(cmd, env=run_env)
    else:
        raise SystemExit("Codex CLI does not register legacy SSE URLs. Use --mcp-transport streamable-http for --url, or --skip-mcp and configure your SSE-capable client manually.")
    print(f"Registered Codex MCP server: {server_name}")


def normalize_path(value: str, *, trailing_slash: bool) -> str:
    path = value.strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if trailing_slash and not path.endswith("/"):
        path += "/"
    if not trailing_slash and len(path) > 1:
        path = path.rstrip("/")
    return path


def http_base_url(env: dict[str, str]) -> str:
    host = env.get("MINECRAFT_OPS_MCP_HOST", "127.0.0.1")
    port = env.get("MINECRAFT_OPS_MCP_PORT", "8000")
    return f"http://{host}:{port}"


def sse_url(env: dict[str, str]) -> str:
    return http_base_url(env) + normalize_path(env.get("MINECRAFT_OPS_MCP_SSE_PATH", "/sse"), trailing_slash=False)


def streamable_http_url(env: dict[str, str]) -> str:
    return http_base_url(env) + normalize_path(env.get("MINECRAFT_OPS_MCP_STREAMABLE_HTTP_PATH", "/mcp"), trailing_slash=False)


def requires_http_auth(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    return normalized not in {"", "127.0.0.1", "localhost", "::1"}


def backup_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{timestamp}")
    if path.is_symlink() or path.is_file():
        path.rename(backup)
    elif path.is_dir():
        shutil.move(str(path), str(backup))
    print(f"Backed up {path} -> {backup}")
    return backup


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, check=True, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
