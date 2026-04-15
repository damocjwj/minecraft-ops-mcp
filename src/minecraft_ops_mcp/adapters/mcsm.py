from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlencode
from urllib.request import Request, urlopen

import httpx

from ..config import AppConfig
from ..errors import ConfigError, OpsError


CHUNK_SIZE = 1024 * 1024


class McsmClient:
    def __init__(self, config: AppConfig) -> None:
        self.app_config = config
        self.config = config.mcsm

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise ConfigError("MCSManager is not configured. Set MCSM_BASE_URL and MCSM_API_KEY.")

    def _ids(self, daemon_id: str | None, uuid: str | None) -> tuple[str, str]:
        daemon = daemon_id or self.config.default_daemon_id
        instance = uuid or self.config.default_instance_uuid
        if not daemon:
            raise ConfigError("Missing daemonId. Pass daemonId or set MCSM_DEFAULT_DAEMON_ID.")
        if not instance:
            raise ConfigError("Missing uuid. Pass uuid or set MCSM_DEFAULT_INSTANCE_UUID.")
        return daemon, instance

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        self._require_enabled()
        query_params = {key: value for key, value in (query or {}).items() if value is not None}
        query_params["apikey"] = self.config.api_key
        url = f"{self.config.base_url}{path}?{urlencode(query_params, doseq=True)}"
        data = None
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=utf-8",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpsError(f"MCSManager HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise OpsError(f"MCSManager request failed: {exc.reason}") from exc
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        if isinstance(payload, dict) and payload.get("status") not in (None, 200):
            raise OpsError(f"MCSManager API error: {payload}")
        return payload

    def list_daemons(self) -> Any:
        return self._request("GET", "/api/service/remote_services_list")

    def get_daemon_system(self) -> Any:
        return self._request("GET", "/api/service/remote_services_system")

    def list_instances(
        self,
        daemon_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        instance_name: str | None = None,
        status: str = "",
    ) -> Any:
        daemon = daemon_id or self.config.default_daemon_id
        if not daemon:
            raise ConfigError("Missing daemonId. Pass daemonId or set MCSM_DEFAULT_DAEMON_ID.")
        return self._request(
            "GET",
            "/api/service/remote_service_instances",
            {
                "daemonId": daemon,
                "page": page,
                "page_size": page_size,
                "instance_name": instance_name,
                "status": status,
            },
        )

    def get_instance(self, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request("GET", "/api/instance", {"daemonId": daemon, "uuid": instance})

    def create_instance(self, daemon_id: str | None, config: dict) -> Any:
        daemon = daemon_id or self.config.default_daemon_id
        if not daemon:
            raise ConfigError("Missing daemonId. Pass daemonId or set MCSM_DEFAULT_DAEMON_ID.")
        return self._request("POST", "/api/instance", {"daemonId": daemon}, config)

    def update_instance_config(self, config: dict, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request("PUT", "/api/instance", {"daemonId": daemon, "uuid": instance}, config)

    def delete_instances(self, uuids: list[str], delete_file: bool, daemon_id: str | None = None) -> Any:
        daemon = daemon_id or self.config.default_daemon_id
        if not daemon:
            raise ConfigError("Missing daemonId. Pass daemonId or set MCSM_DEFAULT_DAEMON_ID.")
        return self._request("DELETE", "/api/instance", {"daemonId": daemon}, {"uuids": uuids, "deleteFile": delete_file})

    def reinstall_instance(
        self,
        target_url: str,
        title: str,
        description: str,
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/protected_instance/install_instance",
            {"daemonId": daemon, "uuid": instance},
            {"targetUrl": target_url, "title": title, "description": description},
        )

    def run_update_task(self, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/protected_instance/asynchronous",
            {"daemonId": daemon, "uuid": instance, "task_name": "update"},
        )

    def instance_action(self, action: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        if action not in {"open", "stop", "restart", "kill"}:
            raise OpsError(f"Unsupported instance action: {action}")
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "GET",
            f"/api/protected_instance/{action}",
            {"daemonId": daemon, "uuid": instance},
        )

    def send_command(self, command: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "GET",
            "/api/protected_instance/command",
            {"daemonId": daemon, "uuid": instance, "command": command},
        )

    def get_logs(self, daemon_id: str | None = None, uuid: str | None = None, size: int | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "GET",
            "/api/protected_instance/outputlog",
            {"daemonId": daemon, "uuid": instance, "size": size},
        )

    def list_files(
        self,
        target: str,
        daemon_id: str | None = None,
        uuid: str | None = None,
        page: int = 0,
        page_size: int = 100,
    ) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "GET",
            "/api/files/list",
            {
                "daemonId": daemon,
                "uuid": instance,
                "target": target,
                "page": page,
                "page_size": page_size,
            },
        )

    def read_file(self, target: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "PUT",
            "/api/files/",
            {"daemonId": daemon, "uuid": instance},
            {"target": target},
        )

    def prepare_download(self, file_name: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/download",
            {"daemonId": daemon, "uuid": instance, "file_name": file_name},
        )

    def prepare_upload(self, upload_dir: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/upload",
            {"daemonId": daemon, "uuid": instance, "upload_dir": upload_dir},
        )

    def upload_local_file(
        self,
        upload_dir: str,
        local_path: str,
        remote_name: str | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
        validate_local_path: bool = True,
    ) -> Any:
        self._ensure_remote_path_allowed(upload_dir, "file.upload_local upload_dir")
        if validate_local_path:
            resolved_path = self._ensure_local_path_allowed(local_path, "file.upload_local local_path")
        else:
            resolved_path = os.path.realpath(os.path.abspath(local_path))
        transfer_limit = _effective_max_bytes(max_bytes, self.app_config.max_bytes)
        file_size = _file_size(resolved_path)
        if file_size > transfer_limit:
            raise OpsError(f"Local upload exceeds max_bytes={transfer_limit}.")
        config = self.prepare_upload(upload_dir, daemon_id, uuid)
        data = config.get("data", {}) if isinstance(config, dict) else {}
        password = data.get("password")
        addr = data.get("addr")
        if not password or not addr:
            raise OpsError(f"MCSManager upload config was incomplete: {config}")
        upload_url = _daemon_url(self.config.base_url, addr, daemon_public_base_url, f"/upload/{password}")
        file_name = remote_name or os.path.basename(resolved_path)
        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with open(resolved_path, "rb") as handle:
                    files = {"file": (file_name, handle, content_type)}
                    response = client.post(upload_url, files=files)
                    response.raise_for_status()
                    text = response.text
        except httpx.HTTPStatusError as exc:
            raise OpsError(f"MCSManager daemon upload HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise OpsError(f"MCSManager daemon upload failed: {exc}") from exc
        except OSError as exc:
            raise OpsError(f"Unable to read local file for upload: {exc}") from exc
        return {
            "status": 200,
            "data": {
                "uploadDir": upload_dir,
                "remoteName": file_name,
                "bytes": file_size,
                "daemonUploadUrlSet": bool(upload_url),
                "response": text,
            },
        }

    def upload_url_file(
        self,
        url: str,
        upload_dir: str,
        remote_name: str | None = None,
        daemon_public_base_url: str | None = None,
        max_bytes: int | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        self._ensure_remote_path_allowed(upload_dir, "file.upload_url upload_dir")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise OpsError("Only http:// and https:// URLs are supported for file.upload_url.")
        self._ensure_upload_url_allowed(url)
        transfer_limit = _effective_max_bytes(max_bytes, self.app_config.max_bytes)
        guessed_name = remote_name or os.path.basename(parsed.path) or "download.bin"
        safe_suffix = os.path.basename(guessed_name) or "download.bin"
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="minecraft-ops-mcp-upload-", suffix=f"-{safe_suffix}")
            os.close(fd)
            total = self._stream_url_to_file(url, tmp_path, transfer_limit, enforce_domain_allowlist=True)
            result = self.upload_local_file(
                upload_dir,
                tmp_path,
                guessed_name,
                daemon_public_base_url,
                daemon_id,
                uuid,
                transfer_limit,
                validate_local_path=False,
            )
            if isinstance(result, dict):
                result.setdefault("data", {})
                if isinstance(result["data"], dict):
                    result["data"]["sourceUrlHost"] = parsed.hostname or ""
                    result["data"]["stagedBytes"] = total
            return result
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def download_local_file(
        self,
        file_name: str,
        local_path: str | None = None,
        daemon_public_base_url: str | None = None,
        overwrite: bool = False,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
        validate_local_path: bool = True,
    ) -> Any:
        self._ensure_remote_path_allowed(file_name, "file.download_local file_name")
        target_path = self._download_target_path(file_name, local_path, validate_local_path=validate_local_path)
        config = self.prepare_download(file_name, daemon_id, uuid)
        data = config.get("data", {}) if isinstance(config, dict) else {}
        password = data.get("password")
        addr = data.get("addr")
        if not password or not addr:
            raise OpsError(f"MCSManager download config was incomplete: {config}")
        download_name = os.path.basename(file_name.rstrip("/")) or "download.bin"
        download_url = ""
        transfer_limit = _effective_max_bytes(max_bytes, self.app_config.max_bytes)
        total: int | None = None
        last_error = ""
        for path in (f"/download/{password}/{quote(download_name)}", f"/download/{password}"):
            download_url = _daemon_url(self.config.base_url, addr, daemon_public_base_url, path)
            try:
                total = self._stream_url_to_file(
                    download_url,
                    target_path,
                    transfer_limit,
                    overwrite=overwrite,
                    enforce_domain_allowlist=False,
                )
                break
            except OpsError as exc:
                last_error = str(exc)
                if "HTTP 400" not in last_error and "HTTP 404" not in last_error:
                    raise
        if total is None:
            raise OpsError(last_error or "MCSManager daemon download failed.")
        return {
            "status": 200,
            "data": {
                "fileName": file_name,
                "localPath": target_path,
                "bytes": total,
                "daemonDownloadUrlSet": bool(download_url),
            },
        }

    def write_file(self, target: str, text: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        self._ensure_remote_path_allowed(target, "file.write target")
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "PUT",
            "/api/files/",
            {"daemonId": daemon, "uuid": instance},
            {"target": target, "text": text},
        )

    def write_new_file(
        self,
        target: str,
        text: str,
        overwrite: bool = False,
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        self._ensure_remote_path_allowed(target, "file.write_new target")
        if not overwrite:
            try:
                self.read_file(target, daemon_id, uuid)
            except OpsError:
                pass
            else:
                raise OpsError(f"Remote file already exists: {target}. Pass overwrite=true to replace it.")
        touch_result = self.touch(target, daemon_id, uuid)
        write_result = self.write_file(target, text, daemon_id, uuid)
        return {"status": 200, "data": {"target": target, "touch": touch_result, "write": write_result}}

    def _download_target_path(self, file_name: str, local_path: str | None, validate_local_path: bool = True) -> str:
        download_name = os.path.basename(file_name.rstrip("/")) or "download.bin"
        target_path = local_path or os.path.join("/tmp", "minecraft-ops-mcp-downloads", download_name)
        if not validate_local_path:
            return os.path.realpath(os.path.abspath(target_path))
        return self._ensure_local_path_allowed(target_path, "file.download_local local_path")

    def _stream_url_to_file(
        self,
        url: str,
        target_path: str,
        max_bytes: int,
        overwrite: bool = True,
        enforce_domain_allowlist: bool = False,
    ) -> int:
        if os.path.exists(target_path) and not overwrite:
            raise OpsError(f"Local file already exists: {target_path}. Pass overwrite=true to replace it.")
        parent = os.path.dirname(target_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{target_path}.part"
        total = 0
        try:
            with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    if enforce_domain_allowlist:
                        self._ensure_upload_url_allowed(str(response.url))
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise OpsError(f"Remote file exceeds max_bytes={max_bytes}.")
                        except ValueError:
                            pass
                    with open(tmp_path, "wb") as handle:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                raise OpsError(f"Remote file exceeds max_bytes={max_bytes}.")
                            handle.write(chunk)
            os.replace(tmp_path, target_path)
            return total
        except httpx.HTTPStatusError as exc:
            raise OpsError(f"HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise OpsError(f"HTTP request failed: {exc}") from exc
        except OSError as exc:
            raise OpsError(f"Unable to write local file: {exc}") from exc
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    def _ensure_local_path_allowed(self, path: str, operation: str) -> str:
        resolved = os.path.realpath(os.path.abspath(path))
        allowed_dirs = tuple(os.path.realpath(os.path.abspath(item)) for item in self.app_config.upload_allowed_dirs)
        if not allowed_dirs:
            return resolved
        if any(resolved == allowed or resolved.startswith(f"{allowed}{os.sep}") for allowed in allowed_dirs):
            return resolved
        raise OpsError(f"{operation} is outside MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS.")

    def _ensure_remote_path_allowed(self, path: str, operation: str) -> str:
        whitelist = self.app_config.file_operation_whitelist
        if not whitelist:
            return _normalize_remote_path(path)
        normalized = _normalize_remote_path(path)
        allowed_paths = tuple(_normalize_remote_path(item) for item in whitelist)
        if any(_remote_path_matches(normalized, allowed) for allowed in allowed_paths):
            return normalized
        raise OpsError(f"{operation} is outside MINECRAFT_OPS_FILE_OPERATION_WHITELIST.")

    def _ensure_upload_url_allowed(self, url: str) -> None:
        allowed_domains = self.app_config.upload_url_allowed_domains
        if not allowed_domains:
            return
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if any(_domain_matches(hostname, allowed.lower()) for allowed in allowed_domains):
            return
        raise OpsError("URL host is not allowed by MINECRAFT_OPS_UPLOAD_URL_ALLOWED_DOMAINS.")

    def delete_files(self, targets: list[str], daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "DELETE",
            "/api/files",
            {"daemonId": daemon, "uuid": instance},
            {"targets": targets},
        )

    def move_files(self, targets: list[list[str]], daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "PUT",
            "/api/files/move",
            {"daemonId": daemon, "uuid": instance},
            {"targets": targets},
        )

    def copy_files(self, targets: list[list[str]], daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/copy",
            {"daemonId": daemon, "uuid": instance},
            {"targets": targets},
        )

    def compress(
        self,
        source: str,
        targets: list[str],
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/compress",
            {"daemonId": daemon, "uuid": instance},
            {"type": 1, "code": "utf-8", "source": source, "targets": targets},
        )

    def uncompress(
        self,
        source: str,
        target: str,
        code: str = "utf-8",
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/compress",
            {"daemonId": daemon, "uuid": instance},
            {"type": 2, "code": code, "source": source, "targets": target},
        )

    def touch(self, target: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/touch",
            {"daemonId": daemon, "uuid": instance},
            {"target": target},
        )

    def mkdir(self, target: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
        daemon, instance = self._ids(daemon_id, uuid)
        return self._request(
            "POST",
            "/api/files/mkdir",
            {"daemonId": daemon, "uuid": instance},
            {"target": target},
        )


def _daemon_url(panel_base_url: str, addr: str, override: str | None, path: str) -> str:
    if override:
        return override.rstrip("/") + path
    if addr.startswith("http://") or addr.startswith("https://"):
        return addr.rstrip("/") + path
    parsed = urlparse(panel_base_url)
    scheme = parsed.scheme or "http"
    host_port = addr
    host, sep, port = addr.partition(":")
    if host in {"localhost", "127.0.0.1", "::1"} and parsed.hostname:
        host_port = f"{parsed.hostname}{sep}{port}" if sep else parsed.hostname
    return f"{scheme}://{host_port}{path}"


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError as exc:
        raise OpsError(f"Unable to stat local file: {exc}") from exc


def _effective_max_bytes(override: int | None, configured: int) -> int:
    value = configured if override is None else override
    if value <= 0:
        raise OpsError("max_bytes must be a positive integer.")
    return value


def _normalize_remote_path(path: str) -> str:
    raw = path.replace("\\", "/")
    if any(part == ".." for part in raw.split("/")):
        raise OpsError("Remote file path must not escape its instance root.")
    normalized = posixpath.normpath(raw).lstrip("/")
    if normalized == ".":
        return ""
    return normalized


def _remote_path_matches(path: str, allowed: str) -> bool:
    if allowed in {"", "."}:
        return True
    return path == allowed or path.startswith(f"{allowed.rstrip('/')}/")


def _domain_matches(hostname: str, allowed: str) -> bool:
    allowed = allowed.lstrip(".")
    return hostname == allowed or hostname.endswith(f".{allowed}")
