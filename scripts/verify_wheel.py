"""Prove the release wheel contains every current runtime source and static asset."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(wheel, root):
    sources = {path.relative_to(root).as_posix(): path
               for path in (root / "ard").rglob("*")
               if path.is_file() and "__pycache__" not in path.parts
               and (path.suffix == ".py" or "static" in path.relative_to(root / "ard").parts)}
    with zipfile.ZipFile(wheel) as archive:
        packaged = {name for name in archive.namelist() if name.startswith("ard/") and not name.endswith("/")}
        if packaged != set(sources):
            raise ValueError(f"Package membership mismatch: missing={set(sources)-packaged}, extra={packaged-set(sources)}")
        mismatches = [name for name, path in sources.items() if archive.read(name) != path.read_bytes()]
        if mismatches:
            raise ValueError("Package differs from current source: " + ", ".join(mismatches))
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        if "\nVersion: " + version + "\n" not in archive.read(metadata).decode():
            raise ValueError("Package version mismatch")
    build_inputs = ["pyproject.toml", "requirements.lock", "README.md"]
    checked = dict(sources)
    for directory in ("tests", "scripts"):
        checked.update({path.relative_to(root).as_posix(): path for path in (root / directory).rglob("*")
                        if path.is_file() and "__pycache__" not in path.parts})
    checked.update({name: root / name for name in
                    build_inputs + ["requirements-dev.txt", "package.json", "package-lock.json", ".github/workflows/ci.yml"]})
    return {
        "status": "PASS", "version": version,
        "wheel": wheel.name, "wheel_sha256": digest(wheel.read_bytes()),
        "runtime_files_compared_byte_for_byte": len(sources),
        "files_sha256": {name: digest(path.read_bytes()) for name, path in sorted(checked.items())},
        "scope": "Current runtime sources equal wheel bytes; test/build inputs recorded separately; no browser/hardware claim"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.wheel, Path(__file__).resolve().parents[1])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "files_sha256"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
