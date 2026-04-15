from __future__ import annotations

import json
import os
import posixpath
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from minecraft_ops_mcp.adapters.mcsm import McsmClient
from minecraft_ops_mcp.config import AppConfig, McsmConfig, MsmpConfig, RconConfig
from minecraft_ops_mcp.errors import OpsError
from minecraft_ops_mcp.modpack import ModpackManager


def app_config(**overrides) -> AppConfig:
    values = {
        "mcsm": McsmConfig(),
        "rcon": RconConfig(),
        "msmp": MsmpConfig(),
        "upload_allowed_dirs": (),
        "modpack_workspace": os.path.join(tempfile.gettempdir(), "minecraft-ops-mcp-test-workspace"),
    }
    values.update(overrides)
    return AppConfig(**values)


def write_fabric_jar(path: str, mod_id: str, version: str, minecraft: str = "~1.21.1") -> None:
    metadata = {
        "schemaVersion": 1,
        "id": mod_id,
        "version": version,
        "name": mod_id.title(),
        "environment": "*",
        "depends": {"minecraft": minecraft, "fabricloader": ">=0.16.0"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(metadata))


def fabric_jar_bytes(mod_id: str, version: str) -> bytes:
    with tempfile.NamedTemporaryFile() as handle:
        write_fabric_jar(handle.name, mod_id, version)
        handle.seek(0)
        return handle.read()


def write_forge_jar(path: str, mod_id: str, version: str) -> None:
    metadata = f'''
modLoader = "javafml"
loaderVersion = "[47,)"
license = "MIT"

[[mods]]
modId = "{mod_id}"
version = "{version}"
displayName = "{mod_id.title()}"

[[dependencies.{mod_id}]]
modId = "minecraft"
mandatory = true
versionRange = "[1.20.1,1.21)"
ordering = "NONE"
side = "BOTH"
'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/mods.toml", metadata)


class FakeMcsm:
    def __init__(self, remote_files: dict[str, bytes] | None = None) -> None:
        self.remote_files = {_normalize_remote(path): content for path, content in (remote_files or {}).items()}
        self.uploads: list[dict[str, Any]] = []
        self.deletes: list[list[str]] = []

    def list_files(self, target: str, *args, **kwargs) -> dict:
        directory = _normalize_remote(target)
        items = []
        for path in sorted(self.remote_files):
            if posixpath.dirname(path) == directory:
                items.append({"name": posixpath.basename(path), "path": path, "type": "file"})
        return {"status": 200, "data": {"items": items, "page": 0, "pageSize": 100, "total": len(items)}}

    def download_local_file(self, file_name: str, local_path: str, *args, **kwargs) -> dict:
        path = _normalize_remote(file_name)
        if path not in self.remote_files:
            raise OpsError(f"missing remote file: {path}")
        with open(local_path, "wb") as handle:
            handle.write(self.remote_files[path])
        return {"status": 200, "data": {"fileName": path, "localPath": local_path, "bytes": len(self.remote_files[path])}}

    def upload_local_file(self, upload_dir: str, local_path: str, remote_name: str, *args, **kwargs) -> dict:
        target = _normalize_remote(posixpath.join(upload_dir, remote_name))
        with open(local_path, "rb") as handle:
            content = handle.read()
        self.remote_files[target] = content
        self.uploads.append({"target": target, "validateLocalPath": kwargs.get("validate_local_path", True)})
        return {"status": 200, "data": {"uploadDir": upload_dir, "remoteName": remote_name, "bytes": len(content)}}

    def delete_files(self, targets: list[str], *args, **kwargs) -> dict:
        normalized = [_normalize_remote(target) for target in targets]
        for target in normalized:
            self.remote_files.pop(target, None)
        self.deletes.append(normalized)
        return {"status": 200, "data": {"targets": normalized}}

    def read_file(self, target: str, *args, **kwargs) -> dict:
        path = _normalize_remote(target)
        if path not in self.remote_files:
            raise OpsError(f"missing remote file: {path}")
        return {"status": 200, "data": {"content": self.remote_files[path].decode("utf-8", errors="replace")}}


def _normalize_remote(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/")).lstrip("/")


class ModpackTests(unittest.TestCase):
    def test_inspect_fabric_jar_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = os.path.join(temp_dir, "example-1.0.0.jar")
            write_fabric_jar(jar_path, "example", "1.0.0")
            config = app_config()
            manager = ModpackManager(config, McsmClient(config))

            result = manager.inspect_jar(local_path=jar_path)

        self.assertEqual(result["primaryMod"]["modId"], "example")
        self.assertEqual(result["primaryMod"]["loader"], "fabric")
        self.assertEqual(result["primaryMod"]["minecraftVersion"], "~1.21.1")
        self.assertEqual(len(result["sha256"]), 64)

    def test_inspect_forge_jar_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = os.path.join(temp_dir, "example-forge.jar")
            write_forge_jar(jar_path, "forgeexample", "2.0.0")
            config = app_config()
            manager = ModpackManager(config, McsmClient(config))

            result = manager.inspect_jar(local_path=jar_path)

        self.assertEqual(result["primaryMod"]["modId"], "forgeexample")
        self.assertEqual(result["primaryMod"]["loader"], "forge")
        self.assertEqual(result["primaryMod"]["minecraftVersion"], "[1.20.1,1.21)")

    def test_snapshot_local_modlist_saves_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as workspace:
            write_fabric_jar(os.path.join(temp_dir, "alpha.jar"), "alpha", "1.0.0")
            write_fabric_jar(os.path.join(temp_dir, "beta.jar"), "beta", "1.0.0")
            config = app_config(modpack_workspace=workspace)
            manager = ModpackManager(config, McsmClient(config))

            snapshot = manager.snapshot_modlist(
                local_dir=temp_dir,
                snapshot_name="baseline",
                minecraft_version="1.21.1",
                loader="fabric",
            )

            saved = Path(snapshot["snapshotPath"])
            self.assertTrue(saved.exists())
            self.assertTrue(str(saved).startswith(str(Path(workspace).resolve())))
            self.assertEqual(snapshot["summary"]["fileCount"], 2)
            self.assertEqual(snapshot["summary"]["parsedModCount"], 2)
            self.assertEqual(snapshot["minecraftVersion"], "1.21.1")
            for item in snapshot["modFiles"]:
                cache = item["cache"]
                self.assertTrue(Path(cache["path"]).exists())
                self.assertTrue(str(Path(cache["path"])).startswith(str(Path(workspace).resolve())))

    def test_diff_snapshots_detects_version_change(self) -> None:
        with tempfile.TemporaryDirectory() as before_dir, tempfile.TemporaryDirectory() as after_dir:
            write_fabric_jar(os.path.join(before_dir, "alpha.jar"), "alpha", "1.0.0")
            write_fabric_jar(os.path.join(after_dir, "alpha.jar"), "alpha", "1.1.0")
            config = app_config()
            manager = ModpackManager(config, McsmClient(config))
            before = manager.snapshot_modlist(local_dir=before_dir, save=False)
            after = manager.snapshot_modlist(local_dir=after_dir, save=False)

            diff = manager.diff_snapshots(before=before, after=after)

        self.assertEqual(diff["summary"]["changedFiles"], 1)
        self.assertEqual(diff["summary"]["changedMods"], 1)
        self.assertEqual(diff["mods"]["changed"][0]["modId"], "alpha")
        self.assertEqual(diff["mods"]["changed"][0]["before"]["version"], "1.0.0")
        self.assertEqual(diff["mods"]["changed"][0]["after"]["version"], "1.1.0")

    def test_snapshot_path_must_stay_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = app_config(modpack_workspace=workspace)
            manager = ModpackManager(config, McsmClient(config))
            with self.assertRaisesRegex(OpsError, "MODPACK_WORKSPACE"):
                manager.diff_snapshots(before_path="/tmp/outside.json", after={})

    def test_local_path_allowlist_rejects_outside_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as denied:
            jar_path = os.path.join(denied, "example.jar")
            write_fabric_jar(jar_path, "example", "1.0.0")
            config = app_config(upload_allowed_dirs=(allowed,))
            manager = ModpackManager(config, McsmClient(config))

            with self.assertRaisesRegex(OpsError, "UPLOAD_ALLOWED_DIRS"):
                manager.inspect_jar(local_path=jar_path)

    def test_remote_snapshot_rejects_traversal_path(self) -> None:
        config = app_config()
        manager = ModpackManager(config, McsmClient(config))
        with self.assertRaisesRegex(OpsError, "instance root"):
            manager.snapshot_modlist(mods_dir="../mods", save=False)

    def test_plan_apply_modlist_detects_upload_replace_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as desired_dir, tempfile.TemporaryDirectory() as workspace:
            write_fabric_jar(os.path.join(desired_dir, "alpha.jar"), "alpha", "2.0.0")
            write_fabric_jar(os.path.join(desired_dir, "beta.jar"), "beta", "1.0.0")
            config = app_config(modpack_workspace=workspace)
            fake = FakeMcsm(
                {
                    "mods/alpha.jar": fabric_jar_bytes("alpha", "1.0.0"),
                    "mods/old.jar": fabric_jar_bytes("old", "1.0.0"),
                }
            )
            manager = ModpackManager(config, fake)  # type: ignore[arg-type]
            desired = manager.snapshot_modlist(local_dir=desired_dir, save=False)

            plan = manager.plan_apply_modlist(manifest=desired, mods_dir="mods")

        self.assertEqual(plan["summary"]["upload"], 1)
        self.assertEqual(plan["summary"]["replace"], 1)
        self.assertEqual(plan["summary"]["delete"], 1)
        self.assertEqual(plan["summary"]["missingSources"], 0)

    def test_apply_modlist_uploads_desired_jars_and_deletes_extras(self) -> None:
        with tempfile.TemporaryDirectory() as desired_dir, tempfile.TemporaryDirectory() as workspace:
            write_fabric_jar(os.path.join(desired_dir, "beta.jar"), "beta", "1.0.0")
            config = app_config(modpack_workspace=workspace)
            fake = FakeMcsm({"mods/old.jar": fabric_jar_bytes("old", "1.0.0")})
            manager = ModpackManager(config, fake)  # type: ignore[arg-type]
            desired = manager.snapshot_modlist(local_dir=desired_dir, save=True)

            result = manager.apply_modlist(manifest=desired, mods_dir="mods")

        self.assertTrue(result["applied"])
        self.assertIn("mods/beta.jar", fake.remote_files)
        self.assertNotIn("mods/old.jar", fake.remote_files)
        self.assertEqual(fake.uploads[0]["target"], "mods/beta.jar")
        self.assertFalse(fake.uploads[0]["validateLocalPath"])
        self.assertEqual(fake.deletes, [["mods/old.jar"]])

    def test_rollback_snapshot_restores_cached_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as baseline_dir, tempfile.TemporaryDirectory() as workspace:
            write_fabric_jar(os.path.join(baseline_dir, "alpha.jar"), "alpha", "1.0.0")
            config = app_config(modpack_workspace=workspace)
            fake = FakeMcsm({"mods/beta.jar": fabric_jar_bytes("beta", "1.0.0")})
            manager = ModpackManager(config, fake)  # type: ignore[arg-type]
            baseline = manager.snapshot_modlist(local_dir=baseline_dir, save=True, snapshot_name="baseline")

            result = manager.rollback_snapshot(snapshot=baseline, mods_dir="mods")

        self.assertTrue(result["applied"])
        self.assertIn("mods/alpha.jar", fake.remote_files)
        self.assertNotIn("mods/beta.jar", fake.remote_files)
        self.assertEqual(result["rollbackSnapshot"]["snapshotId"], baseline["snapshotId"])

    def test_rollback_empty_snapshot_deletes_current_jars(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = app_config(modpack_workspace=workspace)
            fake = FakeMcsm({"mods/beta.jar": fabric_jar_bytes("beta", "1.0.0")})
            manager = ModpackManager(config, fake)  # type: ignore[arg-type]
            empty_snapshot = {
                "schemaVersion": 1,
                "kind": "modpackSnapshot",
                "snapshotId": "empty",
                "source": {"kind": "mcsm", "modsDir": "mods"},
                "modFiles": [],
                "summary": {"fileCount": 0},
            }

            result = manager.rollback_snapshot(snapshot=empty_snapshot, mods_dir="mods")

        self.assertTrue(result["applied"])
        self.assertEqual(fake.remote_files, {})
        self.assertEqual(fake.deletes, [["mods/beta.jar"]])

    def test_classify_startup_result_detects_mod_resolution_failure(self) -> None:
        config = app_config()
        manager = ModpackManager(config, McsmClient(config))

        result = manager.classify_startup_result(
            log_text="net.fabricmc.loader.impl.discovery.ModResolutionException: Mod sodium requires version >=1.21.1 of minecraft"
        )

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["category"], "mod_resolution")
        self.assertIn("mod_resolution", result["matchedCategories"])
        self.assertTrue(result["evidence"])

    def test_classify_startup_result_detects_success(self) -> None:
        config = app_config()
        manager = ModpackManager(config, McsmClient(config))

        result = manager.classify_startup_result(log_text='[Server thread/INFO]: Done (12.345s)! For help, type "help"')

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["category"], "startup_success")

    def test_classify_startup_result_reads_remote_log(self) -> None:
        config = app_config()
        fake = FakeMcsm({"logs/latest.log": b"java.lang.UnsupportedClassVersionError: compiled by a more recent version of the Java Runtime\n"})
        manager = ModpackManager(config, fake)  # type: ignore[arg-type]

        result = manager.classify_startup_result(log_path="logs/latest.log")

        self.assertEqual(result["category"], "java_version")
        self.assertEqual(result["sources"][0]["path"], "logs/latest.log")

    def test_record_list_and_get_test_run(self) -> None:
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as mods_dir:
            write_fabric_jar(os.path.join(mods_dir, "alpha.jar"), "alpha", "1.0.0")
            config = app_config(modpack_workspace=workspace)
            manager = ModpackManager(config, McsmClient(config))
            snapshot = manager.snapshot_modlist(local_dir=mods_dir, snapshot_name="baseline")
            classification = manager.classify_startup_result(log_text="NoSuchMethodError: example.ModApi.changed()V")

            recorded = manager.record_test_run(
                run_name="candidate-a",
                scenario="startup",
                target={"minecraftVersion": "1.21.1", "loader": "fabric"},
                candidate={"changedMods": ["alpha"]},
                before_snapshot=snapshot,
                after_snapshot=snapshot,
                classification=classification,
                log_excerpt="NoSuchMethodError: example.ModApi.changed()V",
                notes="candidate failed during startup",
                tags=["compat", "startup"],
            )
            listed = manager.list_test_runs(limit=5, outcome="failed")
            loaded = manager.get_test_run(run_id=recorded["runId"])
            run_path_exists = Path(recorded["runPath"]).exists()

        self.assertTrue(run_path_exists)
        self.assertEqual(recorded["summary"]["outcome"], "failed")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["runs"][0]["runId"], recorded["runId"])
        self.assertEqual(loaded["run"]["runId"], recorded["runId"])
        self.assertEqual(loaded["summary"]["classification"]["category"], "binary_incompatibility")

    def test_record_test_run_truncates_large_log_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = app_config(modpack_workspace=workspace)
            manager = ModpackManager(config, McsmClient(config))

            recorded = manager.record_test_run(run_name="large-log", log_excerpt="x" * 20000, outcome="failed")

        self.assertLess(len(recorded["record"]["logExcerpt"]), 17000)
        self.assertIn("truncated", recorded["record"]["logExcerpt"])

    def test_get_test_run_path_must_stay_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = app_config(modpack_workspace=workspace)
            manager = ModpackManager(config, McsmClient(config))

            with self.assertRaisesRegex(OpsError, "MODPACK_WORKSPACE"):
                manager.get_test_run(run_path="/tmp/outside-test-run.json")


if __name__ == "__main__":
    unittest.main()
