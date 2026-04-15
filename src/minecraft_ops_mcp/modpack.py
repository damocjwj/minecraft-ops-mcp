from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from .adapters.mcsm import McsmClient
from .config import AppConfig
from .errors import OpsError


SNAPSHOT_SCHEMA_VERSION = 1
TEST_RUN_SCHEMA_VERSION = 1
_JAR_READ_LIMIT = 4 * 1024 * 1024
_CLASSIFY_TEXT_LIMIT = 256 * 1024
_RUN_EXCERPT_LIMIT = 16 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ModpackManager:
    def __init__(self, config: AppConfig, mcsm: McsmClient) -> None:
        self.config = config
        self.mcsm = mcsm

    def inspect_jar(
        self,
        *,
        local_path: str | None = None,
        remote_path: str | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        if bool(local_path) == bool(remote_path):
            raise OpsError("Pass exactly one of local_path or remote_path.")
        if local_path:
            path = _ensure_local_path_allowed(local_path, self.config, "modpack.inspect_jar local_path")
            return inspect_jar_file(path, source={"kind": "local", "path": path})

        assert remote_path is not None
        suffix = f"-{os.path.basename(remote_path) or 'mod.jar'}"
        fd, tmp_path = tempfile.mkstemp(prefix="minecraft-ops-mcp-mod-", suffix=suffix)
        os.close(fd)
        try:
            self.mcsm.download_local_file(
                remote_path,
                tmp_path,
                daemon_public_base_url,
                overwrite=True,
                daemon_id=daemon_id,
                uuid=uuid,
                max_bytes=max_bytes,
                validate_local_path=False,
            )
            return inspect_jar_file(tmp_path, source={"kind": "mcsm", "path": remote_path})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def snapshot_modlist(
        self,
        *,
        mods_dir: str = "mods",
        local_dir: str | None = None,
        recursive: bool = False,
        save: bool = True,
        snapshot_name: str | None = None,
        minecraft_version: str | None = None,
        loader: str | None = None,
        notes: str | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
        cache_jars: bool = True,
        remote_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        if local_dir:
            source = {"kind": "local", "path": _ensure_local_path_allowed(local_dir, self.config, "modpack.snapshot_modlist local_dir")}
            files = self._inspect_local_mod_dir(source["path"], recursive=recursive, cache_jars=cache_jars)
        else:
            source = {
                "kind": "mcsm",
                "modsDir": mods_dir,
                "daemonIdSet": bool(daemon_id or self.config.mcsm.default_daemon_id),
                "uuidSet": bool(uuid or self.config.mcsm.default_instance_uuid),
                "remotePathsSet": bool(remote_paths),
            }
            if remote_paths is not None:
                files = [
                    self._inspect_remote_jar(_normalize_remote_dir(path), daemon_public_base_url, daemon_id, uuid, max_bytes, cache_jars)
                    for path in remote_paths
                ]
            else:
                files = self._inspect_remote_mod_dir(
                    mods_dir,
                    recursive=recursive,
                    daemon_public_base_url=daemon_public_base_url,
                    daemon_id=daemon_id,
                    uuid=uuid,
                    max_bytes=max_bytes,
                    cache_jars=cache_jars,
                )

        snapshot = _make_snapshot(
            source=source,
            mod_files=files,
            snapshot_name=snapshot_name,
            minecraft_version=minecraft_version,
            loader=loader,
            notes=notes,
        )
        if save:
            snapshot_path = self._write_snapshot(snapshot)
            snapshot["snapshotPath"] = snapshot_path
        return snapshot

    def diff_snapshots(
        self,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        before_path: str | None = None,
        after_path: str | None = None,
        before_snapshot_id: str | None = None,
        after_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        before_snapshot = self._load_snapshot_argument(before, before_path, before_snapshot_id, "before")
        after_snapshot = self._load_snapshot_argument(after, after_path, after_snapshot_id, "after")
        return diff_snapshot_objects(before_snapshot, after_snapshot)

    def plan_apply_modlist(
        self,
        *,
        manifest: dict[str, Any] | None = None,
        manifest_path: str | None = None,
        snapshot_id: str | None = None,
        mods_dir: str = "mods",
        clean_extra: bool = True,
        recursive: bool = False,
        current_paths: list[str] | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        desired = self._load_manifest_argument(manifest, manifest_path, snapshot_id, "manifest")
        current = self.snapshot_modlist(
            mods_dir=mods_dir,
            recursive=recursive,
            save=False,
            cache_jars=False,
            snapshot_name="current-before-apply",
            remote_paths=current_paths,
            daemon_public_base_url=daemon_public_base_url,
            daemon_id=daemon_id,
            uuid=uuid,
            max_bytes=max_bytes,
        )
        return self._build_apply_plan(desired, current, mods_dir=mods_dir, clean_extra=clean_extra)

    def apply_modlist(
        self,
        *,
        manifest: dict[str, Any] | None = None,
        manifest_path: str | None = None,
        snapshot_id: str | None = None,
        mods_dir: str = "mods",
        clean_extra: bool = True,
        recursive: bool = False,
        current_paths: list[str] | None = None,
        before_snapshot_name: str | None = None,
        after_snapshot_name: str | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        desired = self._load_manifest_argument(manifest, manifest_path, snapshot_id, "manifest")
        before = self.snapshot_modlist(
            mods_dir=mods_dir,
            recursive=recursive,
            save=True,
            cache_jars=True,
            snapshot_name=before_snapshot_name or "before-apply",
            remote_paths=current_paths,
            daemon_public_base_url=daemon_public_base_url,
            daemon_id=daemon_id,
            uuid=uuid,
            max_bytes=max_bytes,
        )
        plan = self._build_apply_plan(desired, before, mods_dir=mods_dir, clean_extra=clean_extra)
        if plan["summary"]["missingSources"]:
            raise OpsError("Cannot apply modlist because one or more desired jar sources are unavailable.")
        results = self._execute_apply_plan(plan, daemon_public_base_url, daemon_id, uuid, max_bytes)
        after_paths = _after_paths_from_plan(plan, current_paths)
        after = self.snapshot_modlist(
            mods_dir=mods_dir,
            recursive=recursive,
            save=True,
            cache_jars=True,
            snapshot_name=after_snapshot_name or "after-apply",
            remote_paths=after_paths,
            daemon_public_base_url=daemon_public_base_url,
            daemon_id=daemon_id,
            uuid=uuid,
            max_bytes=max_bytes,
        )
        return {
            "applied": True,
            "plan": plan,
            "beforeSnapshot": _snapshot_ref(before),
            "afterSnapshot": _snapshot_ref(after),
            "operationResults": results,
        }

    def plan_rollback_snapshot(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        snapshot_path: str | None = None,
        snapshot_id: str | None = None,
        mods_dir: str = "mods",
        clean_extra: bool = True,
        recursive: bool = False,
        current_paths: list[str] | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        desired = self._load_manifest_argument(snapshot, snapshot_path, snapshot_id, "snapshot")
        return self.plan_apply_modlist(
            manifest=desired,
            mods_dir=mods_dir,
            clean_extra=clean_extra,
            recursive=recursive,
            current_paths=current_paths,
            daemon_public_base_url=daemon_public_base_url,
            daemon_id=daemon_id,
            uuid=uuid,
            max_bytes=max_bytes,
        )

    def rollback_snapshot(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        snapshot_path: str | None = None,
        snapshot_id: str | None = None,
        mods_dir: str = "mods",
        clean_extra: bool = True,
        recursive: bool = False,
        current_paths: list[str] | None = None,
        before_snapshot_name: str | None = None,
        after_snapshot_name: str | None = None,
        daemon_public_base_url: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        desired = self._load_manifest_argument(snapshot, snapshot_path, snapshot_id, "snapshot")
        result = self.apply_modlist(
            manifest=desired,
            mods_dir=mods_dir,
            clean_extra=clean_extra,
            recursive=recursive,
            current_paths=current_paths,
            before_snapshot_name=before_snapshot_name or "before-rollback",
            after_snapshot_name=after_snapshot_name or "after-rollback",
            daemon_public_base_url=daemon_public_base_url,
            daemon_id=daemon_id,
            uuid=uuid,
            max_bytes=max_bytes,
        )
        result["rollbackSnapshot"] = _snapshot_ref(desired)
        return result

    def classify_startup_result(
        self,
        *,
        log_text: str | None = None,
        crash_text: str | None = None,
        log_path: str | None = None,
        crash_report_path: str | None = None,
        daemon_id: str | None = None,
        uuid: str | None = None,
        max_chars: int = _CLASSIFY_TEXT_LIMIT,
    ) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        chunks: list[str] = []
        if log_text:
            chunks.append(log_text)
            sources.append({"kind": "inline", "name": "log_text", "chars": len(log_text)})
        if crash_text:
            chunks.append(crash_text)
            sources.append({"kind": "inline", "name": "crash_text", "chars": len(crash_text)})
        if log_path:
            path = _normalize_remote_dir(log_path)
            text = _mcsm_response_text(self.mcsm.read_file(path, daemon_id, uuid))
            chunks.append(text)
            sources.append({"kind": "mcsm", "path": path, "chars": len(text)})
        if crash_report_path:
            path = _normalize_remote_dir(crash_report_path)
            text = _mcsm_response_text(self.mcsm.read_file(path, daemon_id, uuid))
            chunks.append(text)
            sources.append({"kind": "mcsm", "path": path, "chars": len(text)})
        if not chunks:
            raise OpsError("Pass at least one of log_text, crash_text, log_path, or crash_report_path.")
        return classify_startup_text("\n".join(chunks), sources=sources, max_chars=max_chars)

    def record_test_run(
        self,
        *,
        run_name: str | None = None,
        scenario: str | None = None,
        outcome: str | None = None,
        target: dict[str, Any] | None = None,
        candidate: dict[str, Any] | None = None,
        before_snapshot: dict[str, Any] | None = None,
        before_snapshot_path: str | None = None,
        before_snapshot_id: str | None = None,
        after_snapshot: dict[str, Any] | None = None,
        after_snapshot_path: str | None = None,
        after_snapshot_id: str | None = None,
        apply_result: dict[str, Any] | None = None,
        rollback_result: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
        log_excerpt: str | None = None,
        crash_excerpt: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        external_references: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        before_ref = self._optional_snapshot_ref(before_snapshot, before_snapshot_path, before_snapshot_id, "before_snapshot")
        after_ref = self._optional_snapshot_ref(after_snapshot, after_snapshot_path, after_snapshot_id, "after_snapshot")
        normalized_classification = classification if isinstance(classification, dict) else None
        normalized_outcome = str(outcome or _outcome_from_classification(normalized_classification) or "unknown")
        created_at = _utc_timestamp()
        base_record: dict[str, Any] = {
            "schemaVersion": TEST_RUN_SCHEMA_VERSION,
            "kind": "modpackTestRun",
            "runName": run_name or "",
            "scenario": scenario or "",
            "outcome": normalized_outcome,
            "createdAt": created_at,
            "target": target or {},
            "candidate": candidate or {},
            "beforeSnapshot": before_ref,
            "afterSnapshot": after_ref,
            "applyResult": _operation_result_summary(apply_result),
            "rollbackResult": _operation_result_summary(rollback_result),
            "classification": normalized_classification or {},
            "logExcerpt": _truncate_text(log_excerpt or "", _RUN_EXCERPT_LIMIT),
            "crashExcerpt": _truncate_text(crash_excerpt or "", _RUN_EXCERPT_LIMIT),
            "notes": notes or "",
            "tags": [str(item) for item in (tags or [])],
            "externalReferences": _external_references(external_references),
            "metadata": metadata or {},
        }
        run_id = _make_test_run_id(created_at, run_name, base_record)
        record = {"runId": run_id, **base_record}
        run_path = self._write_test_run(record)
        record["runPath"] = run_path
        return {
            "schemaVersion": TEST_RUN_SCHEMA_VERSION,
            "kind": "modpackTestRunRecordResult",
            "runId": run_id,
            "runPath": run_path,
            "record": record,
            "summary": _test_run_summary(record),
        }

    def list_test_runs(
        self,
        *,
        limit: int = 20,
        outcome: str | None = None,
        scenario: str | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise OpsError("limit must be between 1 and 200.")
        directory = Path(self.config.modpack_workspace).expanduser().resolve() / "runs"
        records: list[dict[str, Any]] = []
        if directory.exists():
            for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                if path.name == "latest.json":
                    continue
                try:
                    with path.open(encoding="utf-8") as handle:
                        record = json.load(handle)
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if outcome and record.get("outcome") != outcome:
                    continue
                if scenario and record.get("scenario") != scenario:
                    continue
                if tag and tag not in (record.get("tags") or []):
                    continue
                records.append(record)
                if len(records) >= limit:
                    break
        return {
            "schemaVersion": TEST_RUN_SCHEMA_VERSION,
            "kind": "modpackTestRunList",
            "runs": [_test_run_summary(record) for record in records],
            "count": len(records),
            "limit": limit,
            "workspace": str(directory),
        }

    def get_test_run(self, *, run_id: str | None = None, run_path: str | None = None) -> dict[str, Any]:
        path = self._test_run_path(run_id, run_path)
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except OSError as exc:
            raise OpsError(f"Unable to read test run: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpsError(f"Test run JSON is invalid: {exc}") from exc
        if not isinstance(record, dict):
            raise OpsError("Test run JSON must be an object.")
        record["runPath"] = path
        return {
            "schemaVersion": TEST_RUN_SCHEMA_VERSION,
            "kind": "modpackTestRun",
            "run": record,
            "summary": _test_run_summary(record),
        }

    def _inspect_local_mod_dir(self, local_dir: str, recursive: bool, cache_jars: bool) -> list[dict[str, Any]]:
        root = Path(local_dir)
        if not root.is_dir():
            raise OpsError(f"Local mod directory does not exist: {local_dir}")
        jars = root.rglob("*.jar") if recursive else root.glob("*.jar")
        inspected: list[dict[str, Any]] = []
        for path in sorted(jars, key=lambda item: str(item).lower()):
            item = inspect_jar_file(str(path), source={"kind": "local", "path": str(path), "relativePath": str(path.relative_to(root))})
            if cache_jars:
                self._attach_cached_blob(item, str(path))
            inspected.append(item)
        return inspected

    def _inspect_remote_mod_dir(
        self,
        mods_dir: str,
        *,
        recursive: bool,
        daemon_public_base_url: str | None,
        daemon_id: str | None,
        uuid: str | None,
        max_bytes: int | None,
        cache_jars: bool,
    ) -> list[dict[str, Any]]:
        remote_files = self._list_remote_mod_jars(mods_dir, recursive=recursive, daemon_id=daemon_id, uuid=uuid)
        inspected: list[dict[str, Any]] = []
        for remote_path in remote_files:
            inspected.append(self._inspect_remote_jar(remote_path, daemon_public_base_url, daemon_id, uuid, max_bytes, cache_jars))
        return inspected

    def _inspect_remote_jar(
        self,
        remote_path: str,
        daemon_public_base_url: str | None,
        daemon_id: str | None,
        uuid: str | None,
        max_bytes: int | None,
        cache_jars: bool,
    ) -> dict[str, Any]:
        suffix = f"-{os.path.basename(remote_path) or 'mod.jar'}"
        fd, tmp_path = tempfile.mkstemp(prefix="minecraft-ops-mcp-mod-", suffix=suffix)
        os.close(fd)
        try:
            self.mcsm.download_local_file(
                remote_path,
                tmp_path,
                daemon_public_base_url,
                overwrite=True,
                daemon_id=daemon_id,
                uuid=uuid,
                max_bytes=max_bytes,
                validate_local_path=False,
            )
            item = inspect_jar_file(tmp_path, source={"kind": "mcsm", "path": remote_path})
            if cache_jars:
                self._attach_cached_blob(item, tmp_path)
            return item
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _list_remote_mod_jars(self, mods_dir: str, *, recursive: bool, daemon_id: str | None, uuid: str | None) -> list[str]:
        pending = [_normalize_remote_dir(mods_dir)]
        seen_dirs: set[str] = set()
        jars: list[str] = []
        while pending:
            current = pending.pop(0)
            if current in seen_dirs:
                continue
            seen_dirs.add(current)
            page = 0
            while True:
                response = self.mcsm.list_files(current or "/", daemon_id, uuid, page=page, page_size=100)
                items, total, page_size = _extract_file_list(response)
                for item in items:
                    name = _file_item_name(item)
                    if not name:
                        continue
                    path = _file_item_path(item, current, name)
                    if _file_item_is_dir(item):
                        if recursive:
                            pending.append(path)
                        continue
                    if name.lower().endswith(".jar"):
                        jars.append(path)
                if total is None or page_size is None or (page + 1) * page_size >= total:
                    break
                page += 1
        return sorted(set(jars), key=str.lower)

    def _write_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = snapshot["snapshotId"]
        directory = Path(self.config.modpack_workspace).expanduser().resolve() / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{snapshot_id}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        latest = directory / "latest.json"
        try:
            latest.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            pass
        return str(path)

    def _build_apply_plan(self, desired: dict[str, Any], current: dict[str, Any], *, mods_dir: str, clean_extra: bool) -> dict[str, Any]:
        desired_entries = _desired_entries(desired, mods_dir)
        current_entries = _current_entries(current)
        operations: list[dict[str, Any]] = []
        missing_sources: list[dict[str, Any]] = []
        desired_paths = set(desired_entries)
        current_paths = set(current_entries)
        for target_path in sorted(desired_paths):
            desired_entry = desired_entries[target_path]
            current_entry = current_entries.get(target_path)
            source = _entry_upload_source(desired_entry)
            if source is None:
                missing_sources.append({"targetPath": target_path, "fileName": desired_entry.get("fileName")})
            if current_entry is None:
                action = "upload"
            elif desired_entry.get("sha256") and current_entry.get("sha256") == desired_entry.get("sha256"):
                action = "keep"
            else:
                action = "replace"
            operations.append(
                {
                    "action": action,
                    "targetPath": target_path,
                    "uploadDir": posixpath.dirname(target_path) or ".",
                    "remoteName": posixpath.basename(target_path),
                    "desired": _plan_entry_view(desired_entry),
                    "current": _plan_entry_view(current_entry) if current_entry else None,
                    "source": source,
                }
            )
        if clean_extra:
            for target_path in sorted(current_paths - desired_paths):
                current_entry = current_entries[target_path]
                operations.append(
                    {
                        "action": "delete",
                        "targetPath": target_path,
                        "current": _plan_entry_view(current_entry),
                    }
                )
        return {
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "kind": "modpackApplyPlan",
            "modsDir": _normalize_remote_dir(mods_dir),
            "cleanExtra": clean_extra,
            "desiredSnapshotId": desired.get("snapshotId") or desired.get("lockId") or "",
            "currentSnapshotId": current.get("snapshotId") or "",
            "operations": operations,
            "missingSources": missing_sources,
            "summary": {
                "upload": sum(1 for item in operations if item["action"] == "upload"),
                "replace": sum(1 for item in operations if item["action"] == "replace"),
                "keep": sum(1 for item in operations if item["action"] == "keep"),
                "delete": sum(1 for item in operations if item["action"] == "delete"),
                "missingSources": len(missing_sources),
            },
        }

    def _execute_apply_plan(
        self,
        plan: dict[str, Any],
        daemon_public_base_url: str | None,
        daemon_id: str | None,
        uuid: str | None,
        max_bytes: int | None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for operation in plan["operations"]:
            action = operation["action"]
            if action == "keep":
                results.append({"action": action, "targetPath": operation["targetPath"], "status": "skipped"})
                continue
            if action in {"upload", "replace"}:
                desired = operation["desired"]
                source = operation.get("source") or {}
                upload_path, validate_local_path = self._materialize_upload_source(source, desired.get("sha256") or "", max_bytes)
                result = self.mcsm.upload_local_file(
                    operation["uploadDir"],
                    upload_path,
                    operation["remoteName"],
                    daemon_public_base_url,
                    daemon_id,
                    uuid,
                    max_bytes=max_bytes,
                    validate_local_path=validate_local_path,
                )
                results.append({"action": action, "targetPath": operation["targetPath"], "status": "ok", "result": result})
        delete_targets = [item["targetPath"] for item in plan["operations"] if item["action"] == "delete"]
        if delete_targets:
            result = self.mcsm.delete_files(delete_targets, daemon_id, uuid)
            results.append({"action": "delete", "targets": delete_targets, "status": "ok", "result": result})
        return results

    def _materialize_upload_source(self, source: dict[str, Any], expected_sha256: str, max_bytes: int | None) -> tuple[str, bool]:
        source_kind = source.get("kind")
        if source_kind in {"cache", "local"}:
            path = str(source.get("path") or "")
            if not path:
                raise OpsError("Desired jar source is missing a local path.")
            actual = _sha256_file(path)
            if expected_sha256 and actual != expected_sha256:
                raise OpsError(f"Jar sha256 mismatch for {path}.")
            return path, source_kind != "cache"
        if source_kind == "url":
            url = str(source.get("url") or "")
            if not url:
                raise OpsError("Desired jar source is missing a URL.")
            tmp_path = self._workspace_tmp_path("download-url", ".jar")
            try:
                self.mcsm._stream_url_to_file(url, tmp_path, max_bytes or self.config.max_bytes, enforce_domain_allowlist=True)
                actual = _sha256_file(tmp_path)
                if expected_sha256 and actual != expected_sha256:
                    raise OpsError(f"Downloaded jar sha256 mismatch for {url}.")
                cache = self._cache_jar(tmp_path, actual)
                return cache["path"], False
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        raise OpsError(f"Unsupported desired jar source: {source}")

    def _attach_cached_blob(self, item: dict[str, Any], source_path: str) -> None:
        item["cache"] = self._cache_jar(source_path, str(item.get("sha256") or ""))

    def _cache_jar(self, source_path: str, sha256: str) -> dict[str, Any]:
        if not sha256:
            sha256 = _sha256_file(source_path)
        blob_path = self._blob_path(sha256)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            tmp_path = blob_path.with_suffix(".tmp")
            shutil.copyfile(source_path, tmp_path)
            os.replace(tmp_path, blob_path)
        return {"path": str(blob_path), "sha256": sha256, "available": blob_path.exists()}

    def _blob_path(self, sha256: str) -> Path:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise OpsError("Invalid sha256 for cached jar.")
        workspace = Path(self.config.modpack_workspace).expanduser().resolve()
        return workspace / "blobs" / sha256[:2].lower() / f"{sha256.lower()}.jar"

    def _workspace_tmp_path(self, prefix: str, suffix: str) -> str:
        directory = Path(self.config.modpack_workspace).expanduser().resolve() / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=f"{prefix}-", suffix=suffix, dir=str(directory))
        os.close(fd)
        return path

    def _load_snapshot_argument(
        self,
        snapshot: dict[str, Any] | None,
        snapshot_path: str | None,
        snapshot_id: str | None,
        label: str,
    ) -> dict[str, Any]:
        supplied = [value is not None for value in (snapshot, snapshot_path, snapshot_id)].count(True)
        if supplied != 1:
            raise OpsError(f"Pass exactly one of {label}, {label}_path, or {label}_snapshot_id.")
        if snapshot is not None:
            return snapshot
        path = self._snapshot_path(snapshot_path, snapshot_id)
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except OSError as exc:
            raise OpsError(f"Unable to read snapshot: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpsError(f"Snapshot JSON is invalid: {exc}") from exc
        if not isinstance(loaded, dict):
            raise OpsError("Snapshot JSON must be an object.")
        return loaded

    def _snapshot_path(self, snapshot_path: str | None, snapshot_id: str | None) -> str:
        workspace = Path(self.config.modpack_workspace).expanduser().resolve()
        if snapshot_id is not None:
            if not _SAFE_ID.match(snapshot_id):
                raise OpsError("snapshot_id contains unsupported characters.")
            return str(workspace / "snapshots" / f"{snapshot_id}.json")
        assert snapshot_path is not None
        resolved = Path(snapshot_path).expanduser().resolve()
        if resolved != workspace and workspace not in resolved.parents:
            raise OpsError("Snapshot path must be inside MINECRAFT_OPS_MODPACK_WORKSPACE.")
        return str(resolved)

    def _load_manifest_argument(
        self,
        manifest: dict[str, Any] | None,
        manifest_path: str | None,
        snapshot_id: str | None,
        label: str,
    ) -> dict[str, Any]:
        supplied = [value is not None for value in (manifest, manifest_path, snapshot_id)].count(True)
        if supplied != 1:
            raise OpsError(f"Pass exactly one of {label}, {label}_path, or snapshot_id.")
        if manifest is not None:
            return manifest
        return self._load_snapshot_argument(None, manifest_path, snapshot_id, label)

    def _optional_snapshot_ref(
        self,
        snapshot: dict[str, Any] | None,
        snapshot_path: str | None,
        snapshot_id: str | None,
        label: str,
    ) -> dict[str, Any]:
        supplied = [value is not None for value in (snapshot, snapshot_path, snapshot_id)].count(True)
        if supplied == 0:
            return {}
        if supplied != 1:
            raise OpsError(f"Pass at most one of {label}, {label}_path, or {label}_id.")
        loaded = self._load_snapshot_argument(snapshot, snapshot_path, snapshot_id, label)
        ref = _snapshot_ref(loaded)
        if not ref["snapshotPath"] and snapshot_path:
            ref["snapshotPath"] = self._snapshot_path(snapshot_path, None)
        return ref

    def _write_test_run(self, record: dict[str, Any]) -> str:
        run_id = str(record.get("runId") or "")
        if not _SAFE_ID.match(run_id):
            raise OpsError("runId contains unsupported characters.")
        directory = Path(self.config.modpack_workspace).expanduser().resolve() / "runs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        latest = directory / "latest.json"
        try:
            latest.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            pass
        return str(path)

    def _test_run_path(self, run_id: str | None, run_path: str | None) -> str:
        supplied = [value is not None for value in (run_id, run_path)].count(True)
        if supplied != 1:
            raise OpsError("Pass exactly one of run_id or run_path.")
        workspace = Path(self.config.modpack_workspace).expanduser().resolve()
        if run_id is not None:
            if not _SAFE_ID.match(run_id):
                raise OpsError("run_id contains unsupported characters.")
            return str(workspace / "runs" / f"{run_id}.json")
        assert run_path is not None
        resolved = Path(run_path).expanduser().resolve()
        if resolved != workspace and workspace not in resolved.parents:
            raise OpsError("Test run path must be inside MINECRAFT_OPS_MODPACK_WORKSPACE.")
        return str(resolved)


def inspect_jar_file(path: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    real_path = os.path.realpath(os.path.abspath(path))
    errors: list[str] = []
    metadata_files: list[str] = []
    mods: list[dict[str, Any]] = []
    try:
        size = os.path.getsize(real_path)
    except OSError as exc:
        raise OpsError(f"Unable to stat jar file: {exc}") from exc
    try:
        digest = _sha256_file(real_path)
        with zipfile.ZipFile(real_path) as archive:
            mods.extend(_inspect_fabric(archive, "fabric.mod.json", "fabric", metadata_files, errors))
            mods.extend(_inspect_fabric(archive, "quilt.mod.json", "quilt", metadata_files, errors))
            mods.extend(_inspect_forge(archive, "META-INF/mods.toml", "forge", metadata_files, errors))
            mods.extend(_inspect_forge(archive, "META-INF/neoforge.mods.toml", "neoforge", metadata_files, errors))
            mods.extend(_inspect_mcmod_info(archive, metadata_files, errors))
    except zipfile.BadZipFile as exc:
        errors.append(f"Invalid jar/zip file: {exc}")
        digest = _sha256_file(real_path)
    primary = mods[0] if mods else None
    return {
        "fileName": os.path.basename(real_path),
        "path": (source or {}).get("path", real_path),
        "source": source or {"kind": "local", "path": real_path},
        "sizeBytes": size,
        "sha256": digest,
        "metadataFiles": metadata_files,
        "detectedLoaders": sorted({item["loader"] for item in mods if item.get("loader")}),
        "primaryMod": primary,
        "mods": mods,
        "errors": errors,
    }


def diff_snapshot_objects(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_files = _index_files(before)
    after_files = _index_files(after)
    before_mods = _index_mods(before)
    after_mods = _index_mods(after)
    added_files = sorted(set(after_files) - set(before_files))
    removed_files = sorted(set(before_files) - set(after_files))
    changed_files = [
        {
            "fileName": name,
            "before": _file_diff_view(before_files[name]),
            "after": _file_diff_view(after_files[name]),
            "sameVersionDifferentHash": _same_version(before_files[name], after_files[name])
            and before_files[name].get("sha256") != after_files[name].get("sha256"),
        }
        for name in sorted(set(before_files) & set(after_files))
        if before_files[name].get("sha256") != after_files[name].get("sha256")
        or _primary_version(before_files[name]) != _primary_version(after_files[name])
    ]
    added_mods = sorted(set(after_mods) - set(before_mods))
    removed_mods = sorted(set(before_mods) - set(after_mods))
    changed_mods = [
        {
            "modId": mod_id,
            "before": before_mods[mod_id],
            "after": after_mods[mod_id],
            "sameVersionDifferentHash": before_mods[mod_id].get("version") == after_mods[mod_id].get("version")
            and before_mods[mod_id].get("sha256") != after_mods[mod_id].get("sha256"),
        }
        for mod_id in sorted(set(before_mods) & set(after_mods))
        if before_mods[mod_id] != after_mods[mod_id]
    ]
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "beforeSnapshotId": before.get("snapshotId"),
        "afterSnapshotId": after.get("snapshotId"),
        "files": {
            "added": [_file_diff_view(after_files[name]) for name in added_files],
            "removed": [_file_diff_view(before_files[name]) for name in removed_files],
            "changed": changed_files,
        },
        "mods": {
            "added": [after_mods[name] for name in added_mods],
            "removed": [before_mods[name] for name in removed_mods],
            "changed": changed_mods,
        },
        "warnings": _diff_warnings(before, after, changed_files, changed_mods),
        "summary": {
            "addedFiles": len(added_files),
            "removedFiles": len(removed_files),
            "changedFiles": len(changed_files),
            "addedMods": len(added_mods),
            "removedMods": len(removed_mods),
            "changedMods": len(changed_mods),
        },
    }


def classify_startup_text(text: str, *, sources: list[dict[str, Any]] | None = None, max_chars: int = _CLASSIFY_TEXT_LIMIT) -> dict[str, Any]:
    if max_chars < 1024:
        raise OpsError("max_chars must be at least 1024.")
    original_chars = len(text)
    truncated = original_chars > max_chars
    sample = text[-max_chars:] if truncated else text
    failure_matches = _match_startup_rules(sample)
    success_matches = _match_success_signals(sample)
    if failure_matches:
        primary = failure_matches[0]
        status = "failure"
        category = primary["category"]
        confidence = primary["confidence"]
        recommended_next = primary["recommendedNext"]
    elif success_matches:
        status = "success"
        category = "startup_success"
        confidence = "high"
        recommended_next = ["Record the candidate as started successfully, then continue smoke tests with player join and representative commands."]
    else:
        status = "unknown"
        category = "unknown"
        confidence = "low"
        recommended_next = ["Read latest.log and crash-reports again, then capture the first causal exception or final startup status line."]
    evidence = _unique_evidence([match["line"] for match in failure_matches + success_matches])
    return {
        "schemaVersion": TEST_RUN_SCHEMA_VERSION,
        "kind": "modpackStartupClassification",
        "status": status,
        "category": category,
        "confidence": confidence,
        "matchedCategories": _matched_categories(failure_matches),
        "signatures": [_signature_view(match) for match in failure_matches + success_matches],
        "evidence": evidence,
        "recommendedNext": recommended_next,
        "sources": sources or [],
        "summary": {
            "inputChars": original_chars,
            "analyzedChars": len(sample),
            "truncatedFromStart": truncated,
            "failureSignalCount": len(failure_matches),
            "successSignalCount": len(success_matches),
        },
    }


def _match_startup_rules(text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for rule in _STARTUP_FAILURE_RULES:
        for pattern in rule["patterns"]:
            line = _first_matching_line(text, pattern)
            if line is None:
                continue
            matches.append(
                {
                    "category": rule["category"],
                    "signature": pattern,
                    "confidence": rule["confidence"],
                    "line": line,
                    "recommendedNext": rule["recommendedNext"],
                }
            )
            break
    return matches


def _match_success_signals(text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for pattern in _STARTUP_SUCCESS_PATTERNS:
        line = _first_matching_line(text, pattern)
        if line is not None:
            matches.append({"category": "startup_success", "signature": pattern, "confidence": "high", "line": line})
    return matches


def _first_matching_line(text: str, pattern: str) -> str | None:
    compiled = re.compile(pattern, re.IGNORECASE)
    for line in text.splitlines():
        if compiled.search(line):
            return _line_excerpt(line)
    return None


def _unique_evidence(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    evidence: list[str] = []
    for line in lines:
        if not line or line in seen:
            continue
        seen.add(line)
        evidence.append(line)
        if len(evidence) >= 8:
            break
    return evidence


def _matched_categories(matches: list[dict[str, Any]]) -> list[str]:
    categories: list[str] = []
    for match in matches:
        category = str(match.get("category") or "")
        if category and category not in categories:
            categories.append(category)
    return categories


def _signature_view(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": match.get("category") or "",
        "signature": match.get("signature") or "",
        "confidence": match.get("confidence") or "",
        "evidence": match.get("line") or "",
    }


_STARTUP_SUCCESS_PATTERNS = [
    r"\bDone \([0-9.]+s\)! For help, type \"help\"",
    r"\bRCON running\b",
    r"\bThreadedAnvilChunkStorage: All dimensions are saved\b",
]


_STARTUP_FAILURE_RULES: list[dict[str, Any]] = [
    {
        "category": "java_version",
        "confidence": "high",
        "patterns": [r"UnsupportedClassVersionError", r"compiled by a more recent version of the Java Runtime"],
        "recommendedNext": [
            "Align the instance Java runtime with the Minecraft and loader requirements.",
            "Record the Java version change in the test run before retesting the same modlist.",
        ],
    },
    {
        "category": "mod_resolution",
        "confidence": "high",
        "patterns": [r"ModResolutionException", r"Could not resolve", r"requires .+ version", r"depends on .+ but"],
        "recommendedNext": [
            "Inspect the named mod dependencies and compare them with the current snapshot metadata.",
            "Change only the missing or incompatible dependency first, then create a new candidate snapshot.",
        ],
    },
    {
        "category": "mixin_failure",
        "confidence": "high",
        "patterns": [r"MixinApplyError", r"MixinTransformerError", r"InvalidMixinException", r"Mixin transformation of .+ failed"],
        "recommendedNext": [
            "Identify the owning mod in the first mixin failure and test that mod against the Minecraft/loader version.",
            "Check for same-version-different-hash warnings before replacing unrelated mods.",
        ],
    },
    {
        "category": "binary_incompatibility",
        "confidence": "high",
        "patterns": [r"NoSuchMethodError", r"NoSuchFieldError", r"AbstractMethodError", r"IncompatibleClassChangeError"],
        "recommendedNext": [
            "Treat this as an incompatible version pair and inspect the stack owner plus the changed mod versions.",
            "Try one version step for the implicated mod or dependency, then snapshot and retest.",
        ],
    },
    {
        "category": "missing_dependency_or_wrong_side",
        "confidence": "medium",
        "patterns": [r"NoClassDefFoundError", r"ClassNotFoundException", r"Attempted to load class .+ for invalid dist", r"wrong environment"],
        "recommendedNext": [
            "Check whether the class belongs to a missing dependency or a client-only mod loaded on the server.",
            "Remove wrong-side mods or add the missing dependency before the next run.",
        ],
    },
    {
        "category": "duplicate_mod",
        "confidence": "medium",
        "patterns": [r"DuplicateModsFoundException", r"duplicate mod"],
        "recommendedNext": [
            "Use the snapshot duplicateModIds summary and file hashes to remove duplicate jars.",
            "Retest after the duplicate set is reduced to one jar per mod id.",
        ],
    },
    {
        "category": "config_error",
        "confidence": "medium",
        "patterns": [r"Failed to load config", r"Could not read config", r"TOML.*error", r"ParsingException"],
        "recommendedNext": [
            "Back up the config file, then regenerate or minimally edit the failing config.",
            "Keep the modlist unchanged while validating the config-only change.",
        ],
    },
    {
        "category": "port_conflict",
        "confidence": "high",
        "patterns": [r"Address already in use", r"Failed to bind to port"],
        "recommendedNext": [
            "Check for another running instance or conflicting server-port/query-port/rcon-port.",
            "Do not change mod versions until the port conflict is resolved.",
        ],
    },
    {
        "category": "startup_failure",
        "confidence": "low",
        "patterns": [r"Crash report saved to", r"Exception in server tick loop", r"Failed to start the minecraft server"],
        "recommendedNext": [
            "Open the referenced crash report and classify again with the crash text.",
            "Use the before snapshot as rollback anchor if the candidate cannot be diagnosed quickly.",
        ],
    },
]


def _inspect_fabric(
    archive: zipfile.ZipFile,
    member: str,
    loader: str,
    metadata_files: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    raw = _read_zip_text(archive, member)
    if raw is None:
        return []
    metadata_files.append(member)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{member}: invalid JSON: {exc}")
        return []
    if loader == "quilt":
        quilt_loader = data.get("quilt_loader") if isinstance(data.get("quilt_loader"), dict) else {}
        mod_id = quilt_loader.get("id") or data.get("id")
        version = quilt_loader.get("version") or data.get("version")
        name = (data.get("metadata") or {}).get("name") if isinstance(data.get("metadata"), dict) else data.get("name")
        depends = quilt_loader.get("depends") or data.get("depends") or []
        dependencies = _quilt_dependencies(depends)
    else:
        mod_id = data.get("id")
        version = data.get("version")
        name = data.get("name")
        dependencies = _fabric_dependencies(data)
    return [
        {
            "modId": mod_id or "",
            "name": name or mod_id or "",
            "version": str(version or ""),
            "loader": loader,
            "environment": data.get("environment") or "*",
            "minecraftVersion": _dependency_version(dependencies, "minecraft"),
            "loaderVersion": _dependency_version(dependencies, "fabricloader" if loader == "fabric" else "quilt_loader"),
            "dependencies": dependencies,
            "metadataFile": member,
        }
    ]


def _inspect_forge(
    archive: zipfile.ZipFile,
    member: str,
    loader: str,
    metadata_files: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    raw = _read_zip_text(archive, member)
    if raw is None:
        return []
    metadata_files.append(member)
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"{member}: invalid TOML: {exc}")
        return []
    dependencies = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
    mods: list[dict[str, Any]] = []
    for entry in data.get("mods", []) if isinstance(data.get("mods"), list) else []:
        if not isinstance(entry, dict):
            continue
        mod_id = str(entry.get("modId") or "")
        deps = _forge_dependencies(dependencies.get(mod_id, []))
        loader_dep = "neoforge" if loader == "neoforge" else "forge"
        mods.append(
            {
                "modId": mod_id,
                "name": entry.get("displayName") or mod_id,
                "version": str(entry.get("version") or ""),
                "loader": loader,
                "environment": "*",
                "minecraftVersion": _dependency_version(deps, "minecraft"),
                "loaderVersion": _dependency_version(deps, loader_dep) or str(data.get("loaderVersion") or ""),
                "dependencies": deps,
                "metadataFile": member,
            }
        )
    return mods


def _inspect_mcmod_info(archive: zipfile.ZipFile, metadata_files: list[str], errors: list[str]) -> list[dict[str, Any]]:
    raw = _read_zip_text(archive, "mcmod.info")
    if raw is None:
        return []
    metadata_files.append("mcmod.info")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"mcmod.info: invalid JSON: {exc}")
        return []
    items = data if isinstance(data, list) else data.get("modList", []) if isinstance(data, dict) else []
    mods: list[dict[str, Any]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        mod_id = str(entry.get("modid") or entry.get("modId") or "")
        mods.append(
            {
                "modId": mod_id,
                "name": entry.get("name") or mod_id,
                "version": str(entry.get("version") or ""),
                "loader": "legacy-forge",
                "environment": "*",
                "minecraftVersion": entry.get("mcversion") or "",
                "loaderVersion": "",
                "dependencies": [],
                "metadataFile": "mcmod.info",
            }
        )
    return mods


def _fabric_dependencies(data: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for relation in ("depends", "recommends", "suggests", "conflicts", "breaks"):
        value = data.get(relation)
        if not isinstance(value, dict):
            continue
        for mod_id, requirement in sorted(value.items()):
            dependencies.append({"modId": mod_id, "relation": relation, "version": _stringify_requirement(requirement)})
    return dependencies


def _quilt_dependencies(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [
            {"modId": mod_id, "relation": "depends", "version": _stringify_requirement(requirement)}
            for mod_id, requirement in sorted(value.items())
        ]
    if isinstance(value, list):
        dependencies: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                dependencies.append({"modId": item, "relation": "depends", "version": ""})
            elif isinstance(item, dict):
                mod_id = item.get("id") or item.get("modId")
                if mod_id:
                    dependencies.append({"modId": str(mod_id), "relation": "depends", "version": _stringify_requirement(item.get("versions", ""))})
        return dependencies
    return []


def _forge_dependencies(value: Any) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        dependencies.append(
            {
                "modId": str(item.get("modId") or ""),
                "relation": "depends" if item.get("mandatory", True) else "suggests",
                "version": str(item.get("versionRange") or ""),
                "ordering": str(item.get("ordering") or ""),
                "side": str(item.get("side") or ""),
            }
        )
    return dependencies


def _make_snapshot(
    *,
    source: dict[str, Any],
    mod_files: list[dict[str, Any]],
    snapshot_name: str | None,
    minecraft_version: str | None,
    loader: str | None,
    notes: str | None,
) -> dict[str, Any]:
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    duplicate_mod_ids = _duplicate_mod_ids(mod_files)
    content_digest = hashlib.sha256(
        json.dumps(
            [{"fileName": item.get("fileName"), "sha256": item.get("sha256"), "mods": item.get("mods", [])} for item in mod_files],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    prefix = _safe_snapshot_name(snapshot_name) if snapshot_name else "snapshot"
    snapshot_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{prefix}-{content_digest}"
    mod_count = sum(len(item.get("mods") or []) for item in mod_files)
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "kind": "modpackSnapshot",
        "snapshotId": snapshot_id,
        "snapshotName": snapshot_name or "",
        "createdAt": created_at,
        "source": source,
        "minecraftVersion": minecraft_version or "",
        "loader": loader or "",
        "notes": notes or "",
        "modFiles": mod_files,
        "summary": {
            "fileCount": len(mod_files),
            "parsedModCount": mod_count,
            "unknownFileCount": sum(1 for item in mod_files if not item.get("mods")),
            "filesWithErrors": sum(1 for item in mod_files if item.get("errors")),
            "duplicateModIds": duplicate_mod_ids,
            "detectedLoaders": sorted({loader for item in mod_files for loader in item.get("detectedLoaders", [])}),
        },
    }


def _read_zip_text(archive: zipfile.ZipFile, member: str) -> str | None:
    try:
        info = archive.getinfo(member)
    except KeyError:
        return None
    if info.file_size > _JAR_READ_LIMIT:
        raise OpsError(f"Metadata file is too large: {member}")
    with archive.open(info) as handle:
        return handle.read().decode("utf-8", errors="replace")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OpsError(f"Unable to hash jar file: {exc}") from exc
    return digest.hexdigest()


def _ensure_local_path_allowed(path: str, config: AppConfig, operation: str) -> str:
    resolved = os.path.realpath(os.path.abspath(path))
    allowed_dirs = tuple(os.path.realpath(os.path.abspath(item)) for item in config.upload_allowed_dirs)
    if not allowed_dirs:
        return resolved
    if any(resolved == allowed or resolved.startswith(f"{allowed}{os.sep}") for allowed in allowed_dirs):
        return resolved
    raise OpsError(f"{operation} is outside MINECRAFT_OPS_UPLOAD_ALLOWED_DIRS.")


def _normalize_remote_dir(path: str) -> str:
    raw = path.replace("\\", "/")
    if any(part == ".." for part in raw.split("/")):
        raise OpsError("Remote mod directory must not escape its instance root.")
    normalized = posixpath.normpath(raw).lstrip("/")
    if normalized in {"", "."}:
        return ""
    return normalized


def _extract_file_list(response: Any) -> tuple[list[dict[str, Any]], int | None, int | None]:
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], None, None
    if not isinstance(data, dict):
        raise OpsError(f"MCSManager file list response was not understood: {response}")
    items = data.get("items") or data.get("data") or data.get("files") or []
    if not isinstance(items, list):
        raise OpsError(f"MCSManager file list items were not a list: {response}")
    total = data.get("total")
    page_size = data.get("pageSize") or data.get("page_size")
    return [item for item in items if isinstance(item, dict)], total if isinstance(total, int) else None, page_size if isinstance(page_size, int) else None


def _file_item_name(item: dict[str, Any]) -> str:
    value = item.get("name") or item.get("fileName") or item.get("filename")
    return str(value or "")


def _file_item_path(item: dict[str, Any], current_dir: str, name: str) -> str:
    path = item.get("path") or item.get("target")
    if path:
        return _normalize_remote_dir(str(path))
    return _normalize_remote_dir(posixpath.join(current_dir, name))


def _file_item_is_dir(item: dict[str, Any]) -> bool:
    if item.get("isDirectory") is True or item.get("directory") is True:
        return True
    item_type = str(item.get("type") or item.get("fileType") or "").lower()
    return item_type in {"dir", "directory", "folder"}


def _dependency_version(dependencies: list[dict[str, Any]], mod_id: str) -> str:
    for dependency in dependencies:
        if dependency.get("modId") == mod_id:
            return str(dependency.get("version") or "")
    return ""


def _stringify_requirement(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _duplicate_mod_ids(mod_files: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for item in mod_files:
        for mod in item.get("mods") or []:
            mod_id = mod.get("modId")
            if mod_id:
                counts[mod_id] = counts.get(mod_id, 0) + 1
    return sorted(mod_id for mod_id, count in counts.items() if count > 1)


def _safe_snapshot_name(value: str | None) -> str:
    if not value:
        return "snapshot"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:48] or "snapshot"


def _snapshot_files(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    files = snapshot.get("modFiles") or snapshot.get("mods") or []
    return [item for item in files if isinstance(item, dict)]


def _desired_entries(manifest: dict[str, Any], mods_dir: str) -> dict[str, dict[str, Any]]:
    entries = _snapshot_files(manifest)
    normalized: dict[str, dict[str, Any]] = {}
    for item in entries:
        entry = dict(item)
        relative_path = _entry_relative_path(entry, manifest)
        target_path = _join_remote_path(mods_dir, relative_path)
        entry["targetPath"] = target_path
        entry["relativePath"] = relative_path
        normalized[target_path] = entry
    return normalized


def _current_entries(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for item in _snapshot_files(snapshot):
        path = str(item.get("path") or (item.get("source") or {}).get("path") or item.get("fileName") or "")
        if not path:
            continue
        normalized[_normalize_remote_dir(path)] = item
    return normalized


def _entry_relative_path(entry: dict[str, Any], manifest: dict[str, Any]) -> str:
    explicit = entry.get("targetPath") or entry.get("relativePath")
    if explicit:
        return _normalize_relative_path(str(explicit))
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    source_relative = source.get("relativePath")
    if source_relative:
        return _normalize_relative_path(str(source_relative))
    source_path = source.get("path") or entry.get("path")
    mods_dir = ((manifest.get("source") or {}).get("modsDir") or "") if isinstance(manifest.get("source"), dict) else ""
    if source_path and mods_dir:
        source_path_norm = _normalize_remote_dir(str(source_path))
        mods_dir_norm = _normalize_remote_dir(str(mods_dir))
        if source_path_norm == mods_dir_norm:
            return _normalize_relative_path(posixpath.basename(source_path_norm))
        prefix = f"{mods_dir_norm.rstrip('/')}/" if mods_dir_norm else ""
        if prefix and source_path_norm.startswith(prefix):
            return _normalize_relative_path(source_path_norm.removeprefix(prefix))
    return _normalize_relative_path(str(entry.get("fileName") or posixpath.basename(str(source_path or ""))))


def _normalize_relative_path(path: str) -> str:
    raw = path.replace("\\", "/").lstrip("/")
    if any(part == ".." for part in raw.split("/")):
        raise OpsError("Mod jar relative path must not escape the mods directory.")
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."} or normalized.endswith("/"):
        raise OpsError("Mod jar relative path must name a jar file.")
    if not normalized.lower().endswith(".jar"):
        raise OpsError("Mod jar relative path must end with .jar.")
    return normalized


def _join_remote_path(mods_dir: str, relative_path: str) -> str:
    base = _normalize_remote_dir(mods_dir)
    return _normalize_remote_dir(posixpath.join(base, relative_path))


def _entry_upload_source(entry: dict[str, Any]) -> dict[str, Any] | None:
    cache = entry.get("cache") if isinstance(entry.get("cache"), dict) else {}
    cache_path = cache.get("path")
    if cache_path and os.path.exists(str(cache_path)):
        return {"kind": "cache", "path": str(cache_path), "sha256": cache.get("sha256") or entry.get("sha256") or ""}
    for key in ("cachePath", "localPath", "local_path"):
        value = entry.get(key)
        if value and os.path.exists(str(value)):
            return {"kind": "local", "path": str(value), "sha256": entry.get("sha256") or ""}
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    if source.get("kind") == "local" and source.get("path") and os.path.exists(str(source["path"])):
        return {"kind": "local", "path": str(source["path"]), "sha256": entry.get("sha256") or ""}
    source_url = entry.get("url") or entry.get("downloadUrl") or source.get("url") or source.get("downloadUrl")
    if source_url:
        return {"kind": "url", "url": str(source_url), "sha256": entry.get("sha256") or ""}
    return None


def _plan_entry_view(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {}
    primary = entry.get("primaryMod") if isinstance(entry.get("primaryMod"), dict) else {}
    return {
        "fileName": entry.get("fileName") or posixpath.basename(str(entry.get("targetPath") or entry.get("path") or "")),
        "targetPath": entry.get("targetPath") or entry.get("path") or (entry.get("source") or {}).get("path"),
        "sha256": entry.get("sha256") or "",
        "modId": primary.get("modId") if isinstance(primary, dict) else "",
        "version": primary.get("version") if isinstance(primary, dict) else "",
        "loader": primary.get("loader") if isinstance(primary, dict) else "",
    }


def _after_paths_from_plan(plan: dict[str, Any], current_paths: list[str] | None) -> list[str] | None:
    paths = {
        item["targetPath"]
        for item in plan.get("operations", [])
        if isinstance(item, dict) and item.get("action") in {"upload", "replace", "keep"} and item.get("targetPath")
    }
    if current_paths and not plan.get("cleanExtra", True):
        paths.update(_normalize_remote_dir(path) for path in current_paths)
    if not paths:
        return []
    return sorted(paths)


def _snapshot_ref(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshotId": snapshot.get("snapshotId") or snapshot.get("lockId") or "",
        "snapshotPath": snapshot.get("snapshotPath") or "",
        "summary": snapshot.get("summary") or {},
    }


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _compact_timestamp(timestamp: str) -> str:
    return timestamp.replace("-", "").replace(":", "").replace("+00:00", "Z")


def _make_test_run_id(created_at: str, run_name: str | None, record: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    prefix = _safe_snapshot_name(run_name) if run_name else "run"
    return f"{_compact_timestamp(created_at)}-{prefix}-{digest}"


def _outcome_from_classification(classification: dict[str, Any] | None) -> str | None:
    if not classification:
        return None
    status = classification.get("status")
    if status == "success":
        return "passed"
    if status == "failure":
        return "failed"
    return "unknown"


def _operation_result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    operation_results = result.get("operationResults") if isinstance(result.get("operationResults"), list) else []
    return {
        "applied": bool(result.get("applied")),
        "planSummary": plan.get("summary") if isinstance(plan.get("summary"), dict) else {},
        "beforeSnapshot": result.get("beforeSnapshot") if isinstance(result.get("beforeSnapshot"), dict) else {},
        "afterSnapshot": result.get("afterSnapshot") if isinstance(result.get("afterSnapshot"), dict) else {},
        "rollbackSnapshot": result.get("rollbackSnapshot") if isinstance(result.get("rollbackSnapshot"), dict) else {},
        "operationCount": len(operation_results),
        "operationStatuses": [
            {
                "action": item.get("action"),
                "targetPath": item.get("targetPath"),
                "targets": item.get("targets"),
                "status": item.get("status"),
            }
            for item in operation_results
            if isinstance(item, dict)
        ][:50],
    }


def _external_references(references: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in references or []:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": str(item.get("title") or item.get("name") or item.get("url") or ""),
                "url": str(item.get("url") or ""),
                "note": str(item.get("note") or ""),
            }
        )
    return normalized


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80] + f"\n...[truncated {len(text) - limit + 80} chars]..."


def _line_excerpt(line: str, limit: int = 300) -> str:
    cleaned = re.sub(r"\s+", " ", line).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _test_run_summary(record: dict[str, Any]) -> dict[str, Any]:
    classification = record.get("classification") if isinstance(record.get("classification"), dict) else {}
    before = record.get("beforeSnapshot") if isinstance(record.get("beforeSnapshot"), dict) else {}
    after = record.get("afterSnapshot") if isinstance(record.get("afterSnapshot"), dict) else {}
    return {
        "runId": record.get("runId") or "",
        "runName": record.get("runName") or "",
        "createdAt": record.get("createdAt") or "",
        "scenario": record.get("scenario") or "",
        "outcome": record.get("outcome") or "",
        "tags": record.get("tags") or [],
        "classification": {
            "status": classification.get("status") or "",
            "category": classification.get("category") or "",
            "confidence": classification.get("confidence") or "",
        },
        "beforeSnapshotId": before.get("snapshotId") or "",
        "afterSnapshotId": after.get("snapshotId") or "",
        "notes": _line_excerpt(str(record.get("notes") or ""), 240),
    }


def _mcsm_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("content", "text", "value", "file", "data"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def _index_files(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("fileName") or item.get("path") or item.get("sha256")): item for item in _snapshot_files(snapshot)}


def _index_mods(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for file_item in _snapshot_files(snapshot):
        for mod in file_item.get("mods") or []:
            mod_id = mod.get("modId")
            if not mod_id:
                continue
            indexed[str(mod_id)] = {
                "modId": str(mod_id),
                "name": mod.get("name") or "",
                "version": mod.get("version") or "",
                "loader": mod.get("loader") or "",
                "minecraftVersion": mod.get("minecraftVersion") or "",
                "loaderVersion": mod.get("loaderVersion") or "",
                "fileName": file_item.get("fileName") or "",
                "sha256": file_item.get("sha256") or "",
            }
    return indexed


def _primary_version(file_item: dict[str, Any]) -> str:
    primary = file_item.get("primaryMod")
    if isinstance(primary, dict):
        return str(primary.get("version") or "")
    mods = file_item.get("mods") or []
    if mods and isinstance(mods[0], dict):
        return str(mods[0].get("version") or "")
    return ""


def _same_version(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_version = _primary_version(before)
    after_version = _primary_version(after)
    return bool(before_version or after_version) and before_version == after_version


def _file_diff_view(file_item: dict[str, Any]) -> dict[str, Any]:
    primary = file_item.get("primaryMod") if isinstance(file_item.get("primaryMod"), dict) else {}
    return {
        "fileName": file_item.get("fileName"),
        "sha256": file_item.get("sha256"),
        "modId": primary.get("modId") if isinstance(primary, dict) else "",
        "version": primary.get("version") if isinstance(primary, dict) else "",
        "loader": primary.get("loader") if isinstance(primary, dict) else "",
    }


def _diff_warnings(
    before: dict[str, Any],
    after: dict[str, Any],
    changed_files: list[dict[str, Any]],
    changed_mods: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for snapshot in (before, after):
        duplicate_mod_ids = ((snapshot.get("summary") or {}).get("duplicateModIds") or []) if isinstance(snapshot.get("summary"), dict) else []
        for mod_id in duplicate_mod_ids:
            warnings.append({"type": "duplicate_mod_id", "snapshotId": snapshot.get("snapshotId"), "modId": mod_id})
    for item in changed_files:
        if item.get("sameVersionDifferentHash"):
            warnings.append({"type": "same_file_version_different_hash", "fileName": item.get("fileName")})
    for item in changed_mods:
        if item.get("sameVersionDifferentHash"):
            warnings.append({"type": "same_mod_version_different_hash", "modId": item.get("modId")})
    return warnings
