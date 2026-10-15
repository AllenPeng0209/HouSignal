#!/usr/bin/env bash
set -euo pipefail

# Produces an internal-test APK using Expo's generated debug signing key.
# This does not provision EAS, create production credentials, or submit a release.
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"

export CI=1
export EXPO_NO_DOTENV=1
export NODE_ENV=production
if [[ -z "${JAVA_HOME:-}" ]]; then
  if [[ -d /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ]]; then
    export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
  else
    export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
  fi
fi
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
task_architectures="${HOUSIGNAL_ANDROID_ARCHITECTURES:-arm64-v8a,x86_64}"
mkdir -p reports

if [[ "${HOUSIGNAL_ANDROID_PREBUILD:-1}" != "0" ]]; then
  npx expo prebuild --platform android --no-install > reports/android-prebuild.log 2>&1
fi
test -f android/gradlew
(
  cd android
  ./gradlew assembleRelease --max-workers=2 --no-daemon \
    '-Dorg.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=768m -Dfile.encoding=UTF-8' \
    "-PreactNativeArchitectures=$task_architectures"
) > reports/android-build.log 2>&1

task_apk="android/app/build/outputs/apk/release/app-release.apk"
test -s "$task_apk"
task_delivery="reports/android-housignal-internal-test.apk"
cp "$task_apk" "$task_delivery"
printf 'Internal-test APK (debug test signing): %s/%s\n' "$task_root" "$task_delivery"
printf 'Architectures: %s\n' "$task_architectures"
shasum -a 256 "$task_delivery"
