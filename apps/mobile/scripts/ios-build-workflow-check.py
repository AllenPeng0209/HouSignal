#!/usr/bin/env python3
"""Verify build receipt/error handling in isolated projects without real build tools."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

MOBILE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = MOBILE / "scripts/ios-build-local.sh"
SCENARIOS = [
    ("prebuild_failed", {"FAKE_PREBUILD_EXIT": "7"}, "Release", False, 7, "native_generation"),
    ("pods_failed", {"FAKE_POD_EXIT": "8"}, "Release", False, 8, "pods"),
    ("build_failed", {"FAKE_BUILD_EXIT": "65"}, "Release", False, 65, "build"),
    ("stale_bundle_rejected", {"FAKE_WRITE_BUNDLE": "0"}, "Release", False, 4, "artifact_validation"),
    ("signing_failed", {"FAKE_SIGN_EXIT": "11"}, "Release", False, 1, "simulator_signing"),
    ("release_passed", {"SKIP_BUNDLING": "1"}, "Release", False, 0, None),
    ("install_failed", {"FAKE_INSTALL_EXIT": "9"}, "Debug", True, 9, "install"),
    ("launch_failed", {"FAKE_LAUNCH_EXIT": "10"}, "Debug", True, 10, "launch"),
    ("installed_release_passed", {}, "Release", True, 0, None),
    ("installed_debug_passed", {}, "Debug", True, 0, None),
]
FAKE_TOOLS = {
    "node_modules/.bin/expo": """#!/bin/sh
mkdir -p ios/app.xcodeproj
echo prebuild >> fake-prebuild-count.txt
exit "${FAKE_PREBUILD_EXIT:-0}"
""",
    "bin/pod": "#!/bin/sh\nexit \"${FAKE_POD_EXIT:-0}\"\n",
    "bin/xcodebuild": """#!/bin/sh
if [ -n "${SKIP_BUNDLING:-}" ]; then exit 23; fi
if [ "${FAKE_BUILD_EXIT:-0}" != 0 ]; then exit "$FAKE_BUILD_EXIT"; fi
configuration=Debug
while [ "$#" -gt 0 ]; do
  if [ "$1" = -configuration ]; then shift; configuration="$1"; fi
  shift
done
app=".native-build/Build/Products/$configuration-iphonesimulator/app.app"
mkdir -p "$app"
printf '%s' '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.housignal.mobile</string><key>CFBundleExecutable</key><string>app</string><key>DTPlatformName</key><string>iphonesimulator</string></dict></plist>' > "$app/Info.plist"
printf 'fake-executable' > "$app/app"
if [ "${FAKE_WRITE_BUNDLE:-1}" = 1 ]; then printf 'fresh-js' > "$app/main.jsbundle"; fi
exit 0
""",
    "bin/otool": "#!/bin/sh\nprintf 'cmd LC_BUILD_VERSION\\ncmdsize 32\\nplatform 7\\nminos 16.4\\n'\n",
    "bin/codesign": """#!/usr/bin/env python3
import os, plistlib, sys
if os.environ.get('FAKE_SIGN_EXIT'):
    raise SystemExit(int(os.environ['FAKE_SIGN_EXIT']))
if '--force' in sys.argv:
    with open('fake-sign-targets.txt', 'a') as file: file.write(sys.argv[-1] + '\\n')
if '--display' in sys.argv:
    sys.stdout.buffer.write(plistlib.dumps({'com.apple.security.application-groups':['group.com.housignal.mobile.simulator']}))
elif '-dvvv' in sys.argv:
    print('Signature=adhoc\\nTeamIdentifier=not set', file=sys.stderr)
""",
    "bin/xcrun": """#!/bin/sh
case "$2" in
  list) echo '06F4FF50-10F0-4A96-82AA-B2879C8A94EE (Booted)' ;;
  install) exit "${FAKE_INSTALL_EXIT:-0}" ;;
  launch) printf '%s\\n' "$*" > fake-launch-args.txt; exit "${FAKE_LAUNCH_EXIT:-0}" ;;
esac
exit 0
""",
}


def make_project(root, name):
    project = root / name
    for directory in ("scripts", "node_modules/.bin", "bin", "reports", "assets"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    (project / "app.json").write_text("{}")
    (project / "package.json").write_text("{}")
    (project / "assets/brand-icon.png").write_bytes(b"initial-icon")
    shutil.copy(SOURCE, project / "scripts/ios-build-local.sh")
    shutil.copy(MOBILE / "scripts/ios-build-sign-simulator.py", project / "scripts/ios-build-sign-simulator.py")
    for relative, contents in FAKE_TOOLS.items():
        file = project / relative
        file.write_text(contents)
        file.chmod(0o755)
    old_app = project / ".native-build/Build/Products/Release-iphonesimulator/app.app"
    old_app.mkdir(parents=True)
    (old_app / "main.jsbundle").write_text("stale-js")
    return project


def run(project, configuration, install=False, overrides=None):
    env = dict(os.environ, **(overrides or {}))
    env["PATH"] = str(project / "bin") + os.pathsep + env["PATH"]
    env["RCT_METRO_PORT"] = "8087"
    command = ["bash", "scripts/ios-build-local.sh", configuration]
    if install:
        command.append("--install")
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=15)
    receipt = json.loads((project / f"reports/ios-build-{configuration.lower()}-verification.json").read_text())
    return result, receipt


def run_signer(project, overrides=None):
    env = dict(os.environ, **(overrides or {}))
    env["PATH"] = str(project / "bin") + os.pathsep + env["PATH"]
    app = project / ".native-build/Build/Products/Release-iphonesimulator/app.app"
    result = subprocess.run(["python3", "scripts/ios-build-sign-simulator.py", str(app)], cwd=project, env=env, capture_output=True, text=True, timeout=15)
    receipt = json.loads((project / "reports/ios-simulator-signing-release.json").read_text())
    return result, receipt


def main():
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="housignal-ios-workflow-check-") as temp:
        root = pathlib.Path(temp)
        for name, overrides, configuration, install, expected, stage in SCENARIOS:
            project = make_project(root, name)
            result, receipt = run(project, configuration, install, overrides)
            assert result.returncode == expected, (name, result.returncode, result.stderr)
            assert receipt["script_exit_code"] == expected, (name, receipt)
            assert receipt["failed_stage"] == stage, (name, receipt)
            assert receipt["outcome"] == ("passed" if expected == 0 else "failed"), (name, receipt)
            assert not (project / "reports/.ios-build-lock").exists(), name
            if expected == 0 and configuration == "Release":
                assert pathlib.Path(receipt["archive_path"]).is_file()
                assert receipt["embedded_js_sha256"]
            if name == "launch_failed":
                assert receipt["install_status"] == "passed" and receipt["launch_status"] == "failed"
            if expected == 0 and install:
                args = (project / "fake-launch-args.txt").read_text()
                assert "--terminate-running-process" in args
                assert ("-RCT_jsLocation 127.0.0.1:8087" in args) == (configuration == "Debug")
            outcomes.append({"check": name, "passed": True})
            print(name + ": passed")
        project = make_project(root, "native_asset_change")
        first_result, first = run(project, "Release")
        assert first_result.returncode == 0
        (project / "assets/brand-icon.png").write_bytes(b"updated-icon")
        second_result, second = run(project, "Release")
        assert second_result.returncode == 0
        assert first["native_input_fingerprint"] != second["native_input_fingerprint"]
        assert second["native_regenerated"] is True
        assert len((project / "fake-prebuild-count.txt").read_text().splitlines()) == 2
        outcomes.append({"check": "native_asset_change", "passed": True})
        print("native_asset_change: passed")
        for name in ("signing_failure_invalidates_receipt", "nested_signing_path_escape", "executable_path_escape"):
            project = make_project(root, name)
            setup, _ = run(project, "Release")
            assert setup.returncode == 0
            app = project / ".native-build/Build/Products/Release-iphonesimulator/app.app"
            before = (project / "fake-sign-targets.txt").read_text()
            overrides = {}
            if name == "signing_failure_invalidates_receipt":
                overrides["FAKE_SIGN_EXIT"] = "11"
            elif name == "nested_signing_path_escape":
                outside = project / "outside.framework"
                outside.mkdir()
                (app / "Frameworks").mkdir()
                (app / "Frameworks/Escaped.framework").symlink_to(outside, target_is_directory=True)
            else:
                import plistlib
                info_path = app / "Info.plist"
                info = plistlib.loads(info_path.read_bytes())
                info["CFBundleExecutable"] = "../outside-executable"
                info_path.write_bytes(plistlib.dumps(info))
            result, receipt = run_signer(project, overrides)
            assert result.returncode == 1, (name, result.returncode, result.stderr)
            assert receipt["outcome"] == "failed" and receipt["signature_verified"] is False
            assert (project / "fake-sign-targets.txt").read_text() == before, name
            outcomes.append({"check": name, "passed": True})
            print(name + ": passed")
    report = {
        "script": "scripts/ios-build-local.sh",
        "command": "python3 scripts/ios-build-workflow-check.py",
        "isolated_checks_passed": len(outcomes),
        "checks": outcomes,
        "scope": "Temporary projects with fake build commands verify workflow control only. Native compilation, simulator launch and UI acceptance are separate evidence.",
    }
    (MOBILE / "reports/ios-build-script-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(outcomes)} isolated workflow checks passed; no live native/build files changed.")


if __name__ == "__main__":
    main()
