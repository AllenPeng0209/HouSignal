#!/usr/bin/env python3
"""Build an installable wheel, zipapp, and plugin bundle without build dependencies."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import zipapp
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for source in sorted((ROOT / "src/louxiang_agent").glob("*.py")):
        files["louxiang_agent/" + source.name] = source.read_bytes()
    info = "louxiang_agent-" + VERSION + ".dist-info"
    files[info + "/METADATA"] = ("Metadata-Version: 2.1\nName: louxiang-agent\nVersion: " + VERSION + "\nSummary: Louxiang remote agent CLI with browser OAuth PKCE\nRequires-Python: >=3.10\n\n").encode()
    files[info + "/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: louxiang-stdlib-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[info + "/entry_points.txt"] = b"[console_scripts]\nlouxiang = louxiang_agent.cli:main\n"
    rows = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append([name, "sha256=" + digest, str(len(data))])
    rows.append([info + "/RECORD", "", ""])
    record = io.StringIO(newline="")
    csv.writer(record).writerows(rows)
    files[info + "/RECORD"] = record.getvalue().encode()
    wheel = output / ("louxiang_agent-" + VERSION + "-py3-none-any.whl")
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with tempfile.TemporaryDirectory(prefix="louxiang-zipapp-") as temporary:
        app = Path(temporary)
        package = app / "louxiang_agent"
        package.mkdir()
        for source in (ROOT / "src/louxiang_agent").glob("*.py"):
            (package / source.name).write_bytes(source.read_bytes())
        (app / "__main__.py").write_text("from louxiang_agent.cli import main\nraise SystemExit(main())\n")
        zipapp.create_archive(app, output / "louxiang.pyz", interpreter="/usr/bin/env python3", compressed=True)
    # A standalone marketplace root: unzip and pass this folder to codex plugin marketplace add.
    with zipfile.ZipFile(output / "louxiang-plugin.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "plugin").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, "louxiang-tools/plugin/" + path.relative_to(ROOT / "plugin").as_posix())
        marketplace = {"name": "louxiang-tools", "interface": {"displayName": "楼响"}, "plugins": [{
            "name": "louxiang", "source": {"source": "local", "path": "./plugin"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}
        archive.writestr("louxiang-tools/.agents/plugins/marketplace.json", json.dumps(marketplace, ensure_ascii=False, indent=2))
        archive.writestr("louxiang-tools/README.md", (ROOT / "README.md").read_bytes())
    manifest = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size} for path in [wheel, output / "louxiang.pyz", output / "louxiang-plugin.zip"]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
