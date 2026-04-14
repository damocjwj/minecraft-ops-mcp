from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import uuid as uuidlib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlencode
from urllib.request import Request, urlopen

from ..config import AppConfig
from ..errors import ConfigError, OpsError


class McsmClient:
    def __init__(self, config: AppConfig) -> None:
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
    ) -> Any:
        config = self.prepare_upload(upload_dir, daemon_id, uuid)
        data = config.get("data", {}) if isinstance(config, dict) else {}
        password = data.get("password")
        addr = data.get("addr")
        if not password or not addr:
            raise OpsError(f"MCSManager upload config was incomplete: {config}")
        upload_url = _daemon_url(self.config.base_url, addr, daemon_public_base_url, f"/upload/{password}")
        file_name = remote_name or os.path.basename(local_path)
        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        boundary = f"----minecraft-ops-mcp-{uuidlib.uuid4().hex}"
        try:
            with open(local_path, "rb") as handle:
                file_bytes = handle.read()
        except OSError as exc:
            raise OpsError(f"Unable to read local file for upload: {exc}") from exc
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        request = Request(
            upload_url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpsError(f"MCSManager daemon upload HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise OpsError(f"MCSManager daemon upload failed: {exc.reason}") from exc
        text = raw.decode("utf-8", errors="replace")
        return {
            "status": 200,
            "data": {
                "uploadDir": upload_dir,
                "remoteName": file_name,
                "bytes": len(file_bytes),
                "daemonUploadUrl": upload_url,
                "response": text,
            },
        }

    def upload_url_file(
        self,
        url: str,
        upload_dir: str,
        remote_name: str | None = None,
        daemon_public_base_url: str | None = None,
        max_bytes: int = 256 * 1024 * 1024,
        daemon_id: str | None = None,
        uuid: str | None = None,
    ) -> Any:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise OpsError("Only http:// and https:// URLs are supported for file.upload_url.")
        guessed_name = remote_name or os.path.basename(parsed.path) or "download.bin"
        safe_suffix = os.path.basename(guessed_name) or "download.bin"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="minecraft-ops-mcp-upload-", suffix=f"-{safe_suffix}", delete=False) as tmp:
                tmp_path = tmp.name
                total = 0
                try:
                    with urlopen(url, timeout=self.config.timeout_seconds) as response:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes:
                                raise OpsError(f"Remote file exceeds max_bytes={max_bytes}.")
                            tmp.write(chunk)
                except HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise OpsError(f"Remote URL HTTP {exc.code}: {detail}") from exc
                except URLError as exc:
                    raise OpsError(f"Remote URL download failed: {exc.reason}") from exc
                except OSError as exc:
                    raise OpsError(f"Unable to stage remote URL download: {exc}") from exc
            result = self.upload_local_file(
                upload_dir,
                tmp_path,
                guessed_name,
                daemon_public_base_url,
                daemon_id,
                uuid,
            )
            if isinstance(result, dict):
                result.setdefault("data", {})
                if isinstance(result["data"], dict):
                    result["data"]["sourceUrl"] = url
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
    ) -> Any:
        config = self.prepare_download(file_name, daemon_id, uuid)
        data = config.get("data", {}) if isinstance(config, dict) else {}
        password = data.get("password")
        addr = data.get("addr")
        if not password or not addr:
            raise OpsError(f"MCSManager download config was incomplete: {config}")
        download_name = os.path.basename(file_name.rstrip("/")) or "download.bin"
        download_url = ""
        content: bytes | None = None
        last_error = ""
        for path in (f"/download/{password}/{quote(download_name)}", f"/download/{password}"):
            download_url = _daemon_url(self.config.base_url, addr, daemon_public_base_url, path)
            try:
                with urlopen(download_url, timeout=self.config.timeout_seconds) as response:
                    content = response.read()
                break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"MCSManager daemon download HTTP {exc.code}: {detail}"
                if exc.code not in {400, 404}:
                    raise OpsError(last_error) from exc
            except URLError as exc:
                last_error = f"MCSManager daemon download failed: {exc.reason}"
                raise OpsError(last_error) from exc
        if content is None:
            raise OpsError(last_error or "MCSManager daemon download failed.")
        target_path = local_path or os.path.join("/tmp", "minecraft-ops-mcp-downloads", download_name)
        if os.path.exists(target_path) and not overwrite:
            raise OpsError(f"Local file already exists: {target_path}. Pass overwrite=true to replace it.")
        parent = os.path.dirname(target_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            with open(target_path, "wb") as handle:
                handle.write(content)
        except OSError as exc:
            raise OpsError(f"Unable to write local download file: {exc}") from exc
        return {
            "status": 200,
            "data": {
                "fileName": file_name,
                "localPath": target_path,
                "bytes": len(content),
                "daemonDownloadUrl": download_url,
            },
        }

    def write_file(self, target: str, text: str, daemon_id: str | None = None, uuid: str | None = None) -> Any:
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
