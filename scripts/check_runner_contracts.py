"""Audit raw discovery against a pinned runner standard. Never changes a host."""

import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def audit(registry_root, records):
    root = Path(registry_root).resolve()
    index = json.loads((root / "registry/index.json").read_text())["entries"]
    validator = Draft202012Validator(
        json.loads((root / "schemas/envelope.v1.schema.json").read_text())
    )
    profiles = {}
    for profile, entry in index.items():
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Registry path escapes root: {profile}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Registry hash mismatch: {profile}")
        document = json.loads(data)
        profiles[profile] = {
            operation["operationId"]
            for path_item in document.get("paths", {}).values()
            for method, operation in path_item.items()
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}
            and isinstance(operation, dict)
            and "operationId" in operation
        }
    records = [runner for item in records for runner in item.get("runners", [item])]
    results = []
    for record in records:
        raw = record.get("Metadata", record.get("metadata"))
        errors = []
        if not isinstance(raw, str):
            errors.append("Raw metadata string is required to verify registered byte size")
            envelope = raw if isinstance(raw, dict) else {}
            size = None
        else:
            size = len(raw.encode("utf-8"))
            if size > 1024:
                errors.append(f"Metadata is {size} bytes; maximum is 1024")
            try:
                envelope = json.loads(raw)
            except ValueError:
                envelope = {}
                errors.append("Metadata is not valid JSON")
        errors.extend(error.message for error in validator.iter_errors(envelope))
        if isinstance(envelope, dict):
            for operation, endpoint in envelope.get("endpoints", {}).items():
                if not isinstance(endpoint, dict):
                    continue
                profile = endpoint.get("profile")
                if profile not in profiles:
                    errors.append(f"Missing registry profile: {profile}")
                elif operation not in profiles[profile]:
                    errors.append(f"Unknown operationId {operation} in {profile}")
        results.append(
            {
                "app": record.get("App", record.get("app")),
                "runner_id": record.get("RunnerID", record.get("runner_id")),
                "metadata_bytes": size,
                "passed": not errors,
                "errors": errors,
            }
        )
    return {
        "checks": ["raw UTF-8 size", "envelope schema", "registry hashes", "profile operationIds"],
        "execution_conformance_checked": False,
        "registrations": results,
        "passed": all(item["passed"] for item in results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.discovery.read_text())
    if not isinstance(records, list) or not records:
        parser.error("Discovery must be a nonempty registration array")
    report = audit(args.registry, records)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{sum(r['passed'] for r in report['registrations'])}/{len(report['registrations'])} registrations pass preflight"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
