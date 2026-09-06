"""Check release archives without extracting or importing their contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {".local", ".venv", "venv", "spec", "planning-private", "workspaces", "__pycache__"}
PRIVATE_SUFFIXES = {".wav", ".ogg", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".mp4",
                    ".bin", ".pyc", ".pyo", ".pem", ".key", ".safetensors", ".ckpt", ".pt", ".pth"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_names(names: list[str]) -> None:
    require(len(names) == len(set(names)), "Duplicate archive paths")
    for name in names:
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in name,
                f"Unsafe archive path: {name}")
        require(not (set(path.parts) & PRIVATE_PARTS), f"Private directory in archive: {name}")
        require(not any(part.startswith(".") for part in path.parts), f"Hidden file in archive: {name}")
        require(path.suffix.lower() not in PRIVATE_SUFFIXES and not name.endswith(".local.json"),
                f"Local artifact in archive: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    wheels, sources = list(args.dist.glob("*.whl")), list(args.dist.glob("*.tar.gz"))
    require(len(wheels) == len(sources) == 1, "Expected exactly one wheel and one sdist")
    modules = {p.relative_to(ROOT / "src").as_posix(): p.read_bytes()
               for p in (ROOT / "src/matter_audio_core").rglob("*.py")}
    license_bytes = (ROOT / "LICENSE").read_bytes()
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = [entry.filename for entry in wheel.infolist() if not entry.is_dir()]
        check_names(names)
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_paths) == 1, "Expected one wheel metadata file")
        info = metadata_paths[0].rsplit("/", 1)[0]
        allowed = set(modules) | {f"{info}/{name}" for name in (
            "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "licenses/LICENSE")}
        require(set(names) == allowed, "Unexpected or missing wheel contents")
        metadata = BytesParser().parsebytes(wheel.read(metadata_paths[0]))
        require(metadata["Name"] == "matter-audio-core", "Wrong project metadata")
        require(metadata["License-Expression"] == "MIT", "Missing SPDX license metadata")
        require(metadata.get_all("License-File") == ["LICENSE"], "Missing license-file metadata")
        require(wheel.read(f"{info}/licenses/LICENSE") == license_bytes, "Wheel license mismatch")
        for name, data in modules.items():
            require(wheel.read(name) == data, f"Wheel module differs from source: {name}")
    with tarfile.open(sources[0], "r:gz") as source:
        entries = source.getmembers()
        require(all(item.isdir() or item.isfile() for item in entries), "Links or special files in sdist")
        names = [entry.name for entry in entries if entry.isfile()]
        check_names(names)
        prefixes = {PurePosixPath(name).parts[0] for name in names}
        require(len(prefixes) == 1, "Expected one sdist root directory")
        prefix = prefixes.pop()
        relative = {name.removeprefix(prefix + "/") for name in names}
        required = {"pyproject.toml", "LICENSE", "README.md", "README.zh-CN.md", "CONTRIBUTING.md",
                    "SECURITY.md", "CHANGELOG.md", "docs/architecture.md", "docs/pcm16-profile.md",
                    "docs/validation.md", "docs/codex.md", "examples/quickstart.py", "tests/test_core.py",
                    "integrations/codex/matter-audio/SKILL.md",
                    "integrations/codex/matter-audio/config.example.json",
                    "integrations/codex/matter-audio/scripts/run_audio.py", "tools/check_distribution.py"}
        require(required <= relative, f"Missing sdist files: {sorted(required - relative)}")
        for name, data in {"LICENSE": license_bytes, **{"src/" + k: v for k, v in modules.items()}}.items():
            member = source.extractfile(f"{prefix}/{name}")
            require(member is not None and member.read() == data, f"Sdist content differs: {name}")
    print(json.dumps({"status": "passed", "core_modules": len(modules), "archives": [
        {"name": path.name, "bytes": path.stat().st_size,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in [*wheels, *sources]
    ]}, indent=2))


if __name__ == "__main__":
    main()
