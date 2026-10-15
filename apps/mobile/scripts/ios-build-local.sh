#!/usr/bin/env bash
# Reproducible simulator build with ad-hoc signing for the exact owned simulator group.
set -euo pipefail

mobile_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$mobile_dir"
configuration="${1:-Debug}"
case "$configuration" in
  Debug|Release) ;;
  *) echo "Usage: $0 [Debug|Release] [--install]" >&2; exit 2 ;;
esac
install="${2:-}"
case "$install" in
  ''|--install) ;;
  *) echo "Usage: $0 [Debug|Release] [--install]" >&2; exit 2 ;;
esac

simulator_udid="${SIMULATOR_UDID:-06F4FF50-10F0-4A96-82AA-B2879C8A94EE}"
metro_port="${RCT_METRO_PORT:-8081}"
build_jobs="${IOS_BUILD_JOBS:-2}"
build_arch="${IOS_BUILD_ARCH:-$(uname -m)}"
case "$build_arch" in
  arm64|x86_64) ;;
  *) echo "Unsupported simulator architecture: $build_arch" >&2; exit 2 ;;
esac
configuration_slug="$(printf '%s' "$configuration" | tr '[:upper:]' '[:lower:]')"
mkdir -p reports
lock_dir="$mobile_dir/reports/.ios-build-lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "An iOS build owns $lock_dir. Do not prebuild or modify Pods while it runs." >&2
  exit 3
fi

log_path="$mobile_dir/reports/ios-build-$configuration_slug.log"
receipt_path="$mobile_dir/reports/ios-build-$configuration_slug-verification.json"
app_path="$mobile_dir/.native-build/Build/Products/$configuration-iphonesimulator/app.app"
archive_path="$mobile_dir/.native-build/housignal-ios-simulator-release.zip"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stage="preparing"
build_status=""
install_status="not_requested"
launch_status="not_requested"
native_fingerprint=""
native_regenerated="false"
if [[ "$install" == --install ]]; then
  install_status="pending"
  launch_status="pending"
fi

write_receipt() {
  python3 - "$receipt_path" "$configuration" "$1" "$2" "$stage" "$build_status" \
    "$started_at" "$simulator_udid" "$app_path" "$log_path" "$install_status" \
    "$launch_status" "$native_fingerprint" "$native_regenerated" "$archive_path" "$build_arch" <<'PY'
import datetime, hashlib, json, pathlib, plistlib, sys
(receipt_path, configuration, outcome, exit_code, stage, build_status, started_at,
 simulator, app_path, log_path, install_status, launch_status, fingerprint,
 native_regenerated, archive_path, build_arch) = sys.argv[1:]
app = pathlib.Path(app_path)
bundle = app / 'main.jsbundle'
source_map = pathlib.Path('.native-build') / f'ios-{configuration.lower()}-main.jsbundle.map'
metadata = {}
if (app / 'Info.plist').is_file():
    try:
        with (app / 'Info.plist').open('rb') as f:
            info = plistlib.load(f)
        metadata = {key: info.get(key) for key in (
            'CFBundleIdentifier', 'CFBundleDisplayName', 'CFBundleShortVersionString',
            'CFBundleVersion', 'MinimumOSVersion', 'DTSDKName', 'UIUserInterfaceStyle',
            'UIApplicationSceneManifest')}
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
receipt = {
    'configuration': configuration,
    'architecture': build_arch,
    'outcome': outcome,
    'script_exit_code': int(exit_code) if exit_code else None,
    'build_exit_code': int(build_status) if build_status else None,
    'stage': stage,
    'failed_stage': stage if outcome == 'failed' else None,
    'started_at': started_at,
    'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat() if outcome != 'running' else None,
    'simulator_udid': simulator,
    'app_path': app_path,
    'app_exists': app.is_dir(),
    'artifact_validated': outcome == 'passed',
    'embedded_js_exists': bundle.is_file(),
    'embedded_js_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest() if bundle.is_file() and outcome == 'passed' else None,
    'source_map_path': str(source_map.resolve()) if source_map.is_file() and outcome == 'passed' else None,
    'source_map_sha256': hashlib.sha256(source_map.read_bytes()).hexdigest() if source_map.is_file() and outcome == 'passed' else None,
    'log_path': log_path,
    'install_status': install_status,
    'launch_status': launch_status,
    'runtime_ui_acceptance': 'not_run_by_build_script',
    'native_input_fingerprint': fingerprint or None,
    'native_regenerated': native_regenerated == 'true',
    'native_metadata': metadata,
    'archive_path': archive_path if configuration == 'Release' and outcome == 'passed' else None,
    'scope': 'Local ad-hoc simulator build; not Apple device/profile or App Store signing',
}
signing_path = pathlib.Path('reports') / f'ios-simulator-signing-{configuration.lower()}.json'
if signing_path.is_file() and outcome == 'passed':
    receipt['simulator_signing'] = json.loads(signing_path.read_text())
pathlib.Path(receipt_path).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
if outcome != 'running':
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
PY
}

finish() {
  original_status="$1"
  trap - EXIT
  set +e
  outcome="failed"
  if [[ "$original_status" -eq 0 ]]; then outcome="passed"; fi
  if [[ "$original_status" -ne 0 && "$stage" == install ]]; then install_status="failed"; fi
  if [[ "$original_status" -ne 0 && "$stage" == launch ]]; then launch_status="failed"; fi
  write_receipt "$outcome" "$original_status"
  receipt_status=$?
  rmdir "$lock_dir"
  if [[ "$original_status" -eq 0 && "$receipt_status" -ne 0 ]]; then
    echo "Build finished but verification receipt failed" >&2
    exit "$receipt_status"
  fi
  exit "$original_status"
}
trap 'finish "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
write_receipt running ""

# SDK 57 supports official source compilation flags. Avoid editing generated Pods.
export RCT_USE_RN_DEP=0 RCT_USE_PREBUILT_RNCORE=0 EXPO_USE_PRECOMPILED_MODULES=0
export EXPO_NO_DOTENV=1
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
if [[ "$configuration" == Release ]]; then
  export EXPO_PUBLIC_API_URL=""
  export SOURCEMAP_FILE="$mobile_dir/.native-build/ios-release-main.jsbundle.map"
  unset SKIP_BUNDLING
  export FORCE_BUNDLING=1
fi

# Clean only the managed iOS tree when native inputs change. Reuse build intermediates.
stage="native_generation"
native_fingerprint="$(python3 - <<'PY'
import hashlib, pathlib
paths = ['app.json', 'app.config.js', 'app.config.ts', 'package.json', 'package-lock.json']
paths += [f'node_modules/{name}/package.json' for name in (
    'expo', 'react-native', 'expo-modules-core', 'expo-build-properties')]
for directory in ('plugins', 'config-plugins', 'assets'):
    if pathlib.Path(directory).is_dir():
        paths.extend(str(p) for p in sorted(pathlib.Path(directory).rglob('*')) if p.is_file())
digest = hashlib.sha256(b'RN-source=1;Expo-precompiled=0;dotenv=0')
for name in paths:
    path = pathlib.Path(name)
    if path.is_file():
        digest.update(name.encode() + b'\0' + path.read_bytes() + b'\0')
print(digest.hexdigest())
PY
)"
previous_fingerprint=""
if [[ -f reports/ios-native-inputs.sha256 ]]; then
  previous_fingerprint="$(cat reports/ios-native-inputs.sha256)"
fi
if [[ ! -d ios/app.xcodeproj || "$native_fingerprint" != "$previous_fingerprint" ]]; then
  ./node_modules/.bin/expo prebuild --platform ios --clean --no-install > reports/ios-prebuild.log 2>&1
  native_regenerated="true"
fi
stage="pods"
(cd ios && pod install) > reports/ios-pods.log 2>&1
printf '%s\n' "$native_fingerprint" > reports/ios-native-inputs.sha256

if [[ "$configuration" == Release ]]; then
  if [[ -f "$app_path/main.jsbundle" ]]; then
    mv "$app_path/main.jsbundle" "$mobile_dir/.native-build/previous-release-main.jsbundle"
  fi
  if [[ -f "$archive_path" ]]; then
    mv "$archive_path" "$mobile_dir/.native-build/previous-ios-simulator-release.zip"
  fi
  if [[ -f "$SOURCEMAP_FILE" ]]; then
    mv "$SOURCEMAP_FILE" "$mobile_dir/.native-build/previous-ios-release-main.jsbundle.map"
  fi
fi
stage="build"
set +e
xcodebuild -workspace ios/app.xcworkspace -scheme app \
  -configuration "$configuration" -sdk iphonesimulator \
  -destination "id=$simulator_udid" -derivedDataPath .native-build \
  -jobs "$build_jobs" "ARCHS=$build_arch" ONLY_ACTIVE_ARCH=YES \
  CODE_SIGNING_ALLOWED=NO RCT_METRO_PORT="$metro_port" \
  build > "$log_path" 2>&1
build_status=$?
set -e
if [[ "$build_status" -ne 0 ]]; then
  tail -30 "$log_path" >&2
  exit "$build_status"
fi
stage="artifact_validation"
if [[ ! -d "$app_path" || ! -f "$app_path/Info.plist" ]]; then
  echo "Build did not produce a simulator application" >&2
  exit 4
fi
if [[ "$configuration" == Release ]]; then
  if [[ ! -s "$app_path/main.jsbundle" ]]; then
    echo "Release build did not embed a fresh main.jsbundle" >&2
    exit 4
  fi
fi
stage="simulator_signing"
python3 scripts/ios-build-sign-simulator.py "$app_path"
if [[ "$configuration" == Release ]]; then
  stage="archive"
  ditto -c -k --sequesterRsrc --keepParent "$app_path" "$archive_path"
fi
if [[ "$install" == --install ]]; then
  stage="simulator_boot"
  if ! xcrun simctl list devices booted | grep -Fq "$simulator_udid"; then
    xcrun simctl boot "$simulator_udid"
  fi
  xcrun simctl bootstatus "$simulator_udid" -b
  stage="install"
  xcrun simctl install "$simulator_udid" "$app_path"
  install_status="passed"
  stage="launch"
  if [[ "$configuration" == Debug ]]; then
    xcrun simctl launch --terminate-running-process "$simulator_udid" com.housignal.mobile \
      -RCT_jsLocation "127.0.0.1:$metro_port"
  else
    xcrun simctl launch --terminate-running-process "$simulator_udid" com.housignal.mobile
  fi
  launch_status="passed"
fi
stage="complete"
