# Release Checklist

Use this checklist before publishing a release artifact.

## Local Quality Gates

```bash
python3 -m pip install -e .
PYTHONPATH=src python3 -B -m unittest discover -s tests
python3 -m compileall -q src scripts
```

Optional package build:

```bash
python3 -m build
```

If `build` is not installed, either install it in a virtual environment or use:

```bash
python3 -m pip install build
```

## Integration Gates

Against a disposable MCSManager/Minecraft environment:

```bash
python3 -B scripts/mcp_integration_probe.py > /tmp/minecraft-ops-mcp-probe-report.json
python3 -B scripts/msmp_temp_instance_probe.py > /tmp/minecraft-ops-mcp-msmp-probe-report.json
```

Expected result:

- base probe: all passed
- MSMP probe: all passed
- no `codex_probe_` or `codex-msmp-probe` temporary instances remain

## Secret Hygiene

Run a secret scan over repository and generated reports:

```bash
rg -n "api[_-]?key|password|secret|token" . /tmp/minecraft-ops-mcp-*.jsonl /tmp/minecraft-ops-mcp-*.json
```

Manually confirm all matches are placeholders, code, or redacted values.

## Versioning

- Update `src/minecraft_ops_mcp/__init__.py`.
- Update `pyproject.toml`.
- Update `CHANGELOG.md`.
- Update test report docs if the public tool surface changed.
