"""Verify the independently distributed MIT HTTP client archives."""
from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from check_distribution import check_names, require


ROOT = Path(__file__).resolve().parents[1] / "integrations/voicestudio"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    wheels, sources = list(args.dist.glob("*.whl")), list(args.dist.glob("*.tar.gz"))
    require(len(wheels) == len(sources) == 1, "Expected one adapter wheel and sdist")
    modules = {p.relative_to(ROOT / "src").as_posix(): p.read_bytes()
               for p in (ROOT / "src/matter_audio_voicestudio").glob("*.py")}
    license_bytes = (ROOT / "LICENSE").read_bytes()
    with zipfile.ZipFile(wheels[0]) as archive:
        names = [p.filename for p in archive.infolist() if not p.is_dir()]
        check_names(names)
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_paths) == 1, "Expected one adapter metadata file")
        info = metadata_paths[0].rsplit("/", 1)[0]
        allowed = set(modules) | {f"{info}/{name}" for name in (
            "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "licenses/LICENSE")}
        require(set(names) == allowed, "Unexpected or missing adapter wheel contents")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        require(metadata["Name"] == "matter-audio-voicestudio", "Wrong adapter name")
        require(metadata["License-Expression"] == "MIT", "Adapter must remain MIT")
        require(metadata.get_all("License-File") == ["LICENSE"], "Missing adapter license metadata")
        dependencies = metadata.get_all("Requires-Dist") or []
        require(len(dependencies) == 1 and dependencies[0].startswith("matter-audio-core"),
                "Adapter must depend only on Core")
        require(archive.read(f"{info}/licenses/LICENSE") == license_bytes, "Adapter license mismatch")
        for name, expected in modules.items():
            require(archive.read(name) == expected, f"Adapter wheel differs: {name}")
    with tarfile.open(sources[0], "r:gz") as archive:
        entries = archive.getmembers()
        require(all(p.isfile() or p.isdir() for p in entries), "Special file in adapter sdist")
        names = [p.name for p in entries if p.isfile()]
        check_names(names)
        prefixes = {name.split("/", 1)[0] for name in names}
        require(len(prefixes) == 1, "Expected one adapter sdist root")
        prefix = prefixes.pop()
        required = {name: (ROOT / name).read_bytes() for name in (
            "LICENSE", "README.md", "pyproject.toml", "MANIFEST.in", "config.example.json",
            "tests/test_transcription.py", "tests/test_editing.py", "tools/evaluate_editing.py")}
        required.update({p.relative_to(ROOT).as_posix(): p.read_bytes()
                         for p in (ROOT / "tests").glob("test_*.py")})
        required.update({"src/" + name: data for name, data in modules.items()})
        for name, expected in required.items():
            member = archive.extractfile(f"{prefix}/{name}")
            require(member is not None and member.read() == expected, f"Adapter sdist differs: {name}")
    print(json.dumps({"status": "passed", "adapter_modules": len(modules),
                      "license": "MIT", "archives": [p.name for p in (*wheels, *sources)]}))


if __name__ == "__main__":
    main()
