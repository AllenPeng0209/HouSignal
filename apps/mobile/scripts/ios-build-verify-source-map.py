#!/usr/bin/env python3
"""Compare bundled app source-map contents with the current repository source."""
import datetime
import hashlib
import json
import pathlib
import re

MOBILE = pathlib.Path(__file__).resolve().parent.parent


def verify():
    path = MOBILE / ".native-build/ios-release-main.jsbundle.map"
    mapping = json.loads(path.read_text())
    assert len(mapping["sources"]) == len(mapping["sourcesContent"])
    physical = []
    generated = []
    mismatches = []
    for source, content in zip(mapping["sources"], mapping["sourcesContent"]):
        name = source.lstrip("/")
        if not name.startswith("src/"):
            continue
        if name.startswith("src/app?ctx="):
            routes = set(re.findall(r'"(\./[^"\n]+)"\s*:\s*\{\s*enumerable:', content or ""))
            expected = {
                "./" + str(file.relative_to(MOBILE / "src/app"))
                for file in (MOBILE / "src/app").rglob("*")
                if file.is_file() and file.suffix in (".ts", ".tsx", ".js", ".jsx")
                and not file.name.endswith(".d.ts") and ".web." not in file.name
            }
            if routes != expected:
                mismatches.append({"source": name, "reason": "generated route inventory differs", "missing": sorted(expected - routes), "extra": sorted(routes - expected)})
            else:
                generated.append({"source": name, "kind": "Expo Router context", "routes_verified": len(routes), "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
            continue
        file = (MOBILE / name).resolve()
        if not file.is_relative_to(MOBILE / "src") or not file.is_file() or content is None:
            mismatches.append({"source": name, "reason": "missing or out-of-scope source/map content"})
            continue
        actual = file.read_bytes()
        if actual != content.encode("utf-8"):
            mismatches.append({"source": name, "reason": "full text differs"})
            continue
        physical.append({"source": name, "bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest()})
    critical = {"src/lib/transfers.ts", "src/lib/transfers-core.ts", "src/state/storage-core.ts"}
    found = {module["source"] for module in physical}
    if not critical <= found:
        mismatches.append({"reason": "critical runtime modules are absent", "missing": sorted(critical - found)})
    report = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_map": str(path),
        "source_map_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_map_entries": len(mapping["sources"]),
        "app_modules_checked": len(physical),
        "full_text_matches": len(physical),
        "generated_contexts": generated,
        "mismatches": mismatches,
        "modules": physical,
        "scope": "Every physical runtime app module is compared byte-for-byte with current src. Generated Expo Router contexts verify the current route inventory separately; erased types/unused source files are not runtime modules.",
    }
    (MOBILE / "reports/ios-source-map-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("source_map_entries", "app_modules_checked", "full_text_matches", "generated_contexts", "mismatches", "source_map_sha256")}, ensure_ascii=False, indent=2))
    if mismatches:
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    verify()
