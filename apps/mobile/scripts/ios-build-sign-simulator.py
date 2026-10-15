#!/usr/bin/env python3
"""Ad-hoc sign only this app's simulator outputs; never use Apple account keys."""
import datetime
import hashlib
import json
import pathlib
import plistlib
import re
import subprocess
import sys

MOBILE = pathlib.Path(__file__).resolve().parent.parent
BUNDLE_ID = "com.housignal.mobile"
SIMULATOR_GROUP = "group.com.housignal.mobile.simulator"
ENTITLEMENTS = {"com.apple.security.application-groups": [SIMULATOR_GROUP]}


def write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def sign(app):
    app = app.resolve()
    products = (MOBILE / ".native-build/Build/Products").resolve()
    if app.parent.parent != products or app.parent.name not in ("Debug-iphonesimulator", "Release-iphonesimulator"):
        raise ValueError("Only this project's generated simulator application may be signed")
    configuration = app.parent.name.removesuffix("-iphonesimulator")
    receipt_path = MOBILE / f"reports/ios-simulator-signing-{configuration.lower()}.json"
    receipt = {
        "configuration": configuration,
        "app_path": str(app),
        "outcome": "running",
        "signature_verified": False,
        "signing_style": "adhoc_simulator_own_app_group",
        "entitlements": ENTITLEMENTS,
        "scope": "Simulator only. Exact owned group; no production entitlements or Apple provisioning identity.",
    }
    write_receipt(receipt_path, receipt)
    try:
        return sign_checked(app, configuration, receipt_path, receipt)
    except Exception as error:
        receipt.update({"outcome": "failed", "signature_verified": False, "error_type": type(error).__name__, "error": str(error)})
        write_receipt(receipt_path, receipt)
        raise


def sign_checked(app, configuration, receipt_path, receipt):
    with (app / "Info.plist").open("rb") as file:
        info = plistlib.load(file)
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("DTPlatformName") != "iphonesimulator":
        raise ValueError("Expected the owned HouSignal iOS simulator application")
    executable = app / info["CFBundleExecutable"]
    if not executable.resolve().is_relative_to(app):
        raise ValueError("The application executable escapes the owned bundle")
    load_commands = subprocess.run(["otool", "-l", str(executable)], check=True, capture_output=True, text=True).stdout
    if not re.search(r"cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform 7\s", load_commands):
        raise ValueError("The executable is not an iOS Simulator Mach-O")
    entitlements_path = MOBILE / ".native-build/ios-simulator-entitlements.plist"
    entitlements_path.write_bytes(plistlib.dumps(ENTITLEMENTS))
    frameworks = sorted(app.glob("Frameworks/**/*.framework"), key=lambda path: len(path.parts), reverse=True)
    libraries = sorted(app.glob("Frameworks/**/*.dylib"), key=lambda path: len(path.parts), reverse=True)
    if any(not target.resolve().is_relative_to(app) for target in libraries + frameworks):
        raise ValueError("A nested signing target escapes the owned bundle")
    # Never apply app grants to libraries; sign nested code before sealing the bundle.
    for nested in libraries + frameworks:
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(nested)], check=True)
    subprocess.run([
        "codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID,
        "--timestamp=none", "--entitlements", str(entitlements_path),
        "--generate-entitlement-der", str(app),
    ], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    result = subprocess.run(["codesign", "--display", "--entitlements", "-", "--xml", str(app)], check=True, capture_output=True)
    actual = plistlib.loads(result.stdout)
    if actual != ENTITLEMENTS:
        raise ValueError(f"Unexpected simulator entitlements: {actual!r}")
    description = subprocess.run(["codesign", "-dvvv", str(app)], check=True, capture_output=True, text=True).stderr
    if "Signature=adhoc" not in description or "TeamIdentifier=not set" not in description:
        raise ValueError("Expected ad-hoc signing without an Apple team/certificate identity")
    bundle = app / "main.jsbundle"
    receipt.update({
        "configuration": configuration,
        "app_path": str(app),
        "signing_style": "adhoc_simulator_own_app_group",
        "entitlements": actual,
        "signature_verified": True,
        "outcome": "passed",
        "team_identifier": None,
        "apple_certificate_used": False,
        "signed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "embedded_js_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest() if bundle.is_file() else None,
        "runtime_secure_store": "requires_separate_read_write_verification",
        "scope": "Simulator only. Exact owned group; no production entitlements or Apple provisioning identity.",
    })
    write_receipt(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ios-build-sign-simulator.py <owned simulator app path>")
    sign(pathlib.Path(sys.argv[1]))
