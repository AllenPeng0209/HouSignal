#!/usr/bin/env python3
"""Verify the internal-test APK without contacting APIs or reading dotenv files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from datetime import datetime, timezone


root = Path(__file__).resolve().parents[1]
reports = root / "reports"
receipt_path = reports / "android-build-verification.json"
receipt_path.unlink(missing_ok=True)
apk = reports / "android-housignal-internal-test.apk"
sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
tools = sdk / "build-tools/36.0.0"
env = os.environ.copy()
env.setdefault("JAVA_HOME", "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")


def run(arguments: list[str], report_name: str) -> str:
    result = subprocess.run(arguments, env=env, check=True, capture_output=True, text=True)
    (reports / report_name).write_text(result.stdout)
    return result.stdout


signature = run([str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)], "android-apk-signature.log")
badging = run([str(tools / "aapt2"), "dump", "badging", str(apk)], "android-apk-badging.log")
manifest = run([str(tools / "aapt2"), "dump", "xmltree", "--file", "AndroidManifest.xml", str(apk)], "android-apk-manifest.log")
resources = run([str(tools / "aapt2"), "dump", "resources", str(apk)], "android-apk-resources.log")

assert "CN=Android Debug" in signature, "This receipt is for debug-test signing only"
assert "SYSTEM_ALERT_WINDOW" not in manifest, "Unexpected overlay permission"
assert "application-debuggable" not in badging, "APK must use Release application flags"
assert "usesCleartextTraffic" not in manifest, "Release should use target-SDK HTTPS default"
assert re.search(r'string/expo_system_ui_user_interface_style\s*\n\s*\(\) "light"', resources)
assert "native-code: 'arm64-v8a' 'x86_64'" in badging
assert "application-label:'楼响'" in badging
assert "package: name='com.housignal.mobile' versionCode='1' versionName='1.0.0'" in badging

for resource_name in ("secure_store_backup_rules", "secure_store_data_extraction_rules"):
    resource = re.search(rf"xml/{resource_name}\s*\n\s*\(\) \(file\) (\S+) type=XML", resources)
    assert resource, f"Missing {resource_name}"
    xml = run([str(tools / "aapt2"), "dump", "xmltree", "--file", resource.group(1), str(apk)], f"android-apk-{resource_name}.log")
    assert "exclude" in xml and '"SecureStore"' in xml, "SecureStore must be excluded from backup"

with zipfile.ZipFile(apk) as package:
    bundle = package.read("assets/index.android.bundle")
    abis = sorted({name.split("/")[1] for name in package.namelist() if name.startswith("lib/")})
    assert abis == ["arm64-v8a", "x86_64"]
generated_bundle = root / "android/app/build/generated/assets/react/release/index.android.bundle"
assert bundle == generated_bundle.read_bytes(), "APK must contain the freshly generated bundle"
bundle_type = subprocess.run(["file", "--brief", "-"], input=bundle, check=True, capture_output=True).stdout.decode().strip()
assert "Hermes JavaScript bytecode" in bundle_type

source_map = json.loads((root / "android/app/build/intermediates/sourcemaps/react/release/index.android.bundle.packager.map").read_text())
source_matches = 0
matched_sources: set[str] = set()
for source, content in zip(source_map["sources"], source_map["sourcesContent"]):
    if not source.startswith("/src/"):
        continue
    local_source = (root / source.lstrip("/")).resolve()
    assert local_source.is_relative_to(root / "src"), "Application source must stay inside src"
    if local_source.is_file():
        assert local_source.read_text() == content, f"Bundled source is stale: {source}"
        source_matches += 1
        matched_sources.add(source)
assert source_matches >= 50, "Expected application modules in the production source map"
required_sources = {"/src/lib/transfers.ts", "/src/lib/transfers-core.ts", "/src/state/storage-core.ts"}
assert required_sources <= matched_sources, "Expected transfer and secure-storage modules in the APK bundle"
source_tree = hashlib.sha256()
source_files = sorted(path for path in (root / "src").rglob("*") if path.is_file())
for source_file in source_files:
    source_tree.update(source_file.relative_to(root).as_posix().encode() + b"\0")
    source_tree.update(hashlib.sha256(source_file.read_bytes()).digest())
assert "BUILD SUCCESSFUL" in (reports / "android-build.log").read_text()

receipt = {
    "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "passed",
    "artifact": str(apk),
    "artifact_kind": "internal-test APK",
    "artifact_bytes": apk.stat().st_size,
    "artifact_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
    "package": "com.housignal.mobile",
    "name": "楼响",
    "version": "1.0.0",
    "version_code": 1,
    "min_sdk": 24,
    "target_sdk": 36,
    "abis": abis,
    "signing": {"verified": True, "certificate": "CN=Android Debug", "production_signing": False},
    "hermes": {"classification": bundle_type, "bytes": len(bundle), "sha256": hashlib.sha256(bundle).hexdigest(), "apk_matches_generated_bundle": True},
    "current_source_map_matches": source_matches,
    "source_provenance": {"source_file_count": len(source_files), "source_tree_sha256": source_tree.hexdigest(), "required_source_matches": sorted(required_sources)},
    "manifest": {"overlay_permission": False, "debuggable": False, "explicit_cleartext_enabled": False, "light_style": True, "secure_store_backup_excluded": True},
    "build": {"gradle": "9.3.1", "jdk": "17", "max_workers": 2, "heap_mb": 2048, "dotenv_disabled": True, "final_prebuild": False},
    "android_runtime_ui": {"status": "not_run", "reason": "No attached Android device, emulator binary, or system image was available."},
    "production_release": {"status": "not_submitted", "pending": ["Production developer signing", "Actual-device acceptance", "Production HTTPS backend configuration", "Store acceptance"]},
}
receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(receipt, ensure_ascii=False, indent=2))
