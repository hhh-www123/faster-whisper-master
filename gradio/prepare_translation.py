"""Download only translation packages. Never changes Whisper weights.

Official package index: https://github.com/argosopentech/argospm-index
Packages carry their own README/license. Downloads may take several minutes.
"""
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests

from core import ROOT, ChineseTranslator


def main():
    base = ROOT / "model" / "translation"
    base.mkdir(parents=True, exist_ok=True)
    for name in ChineseTranslator._packages.values():
        if (base / name / "model" / "model.bin").is_file():
            print(f"Already installed: {name}")
            continue
        with tempfile.TemporaryDirectory(dir=base) as staging:
            staging = Path(staging)
            package = staging / "package.zip"
            print(f"Downloading {name}…", flush=True)
            with requests.get(f"https://argos-net.com/v1/{name}.argosmodel", stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with package.open("wb") as handle:
                    for chunk in response.iter_content(1024*1024):
                        handle.write(chunk)
            with zipfile.ZipFile(package) as archive:
                for member in archive.infolist():
                    target = (staging / member.filename).resolve()
                    if not target.is_relative_to(staging.resolve()):
                        raise ValueError("Unsafe package member")
                archive.extractall(staging)
            candidates = list(staging.glob("*/metadata.json"))
            if len(candidates) != 1:
                raise ValueError("Unexpected translation package structure")
            extracted = candidates[0].parent
            metadata = json.loads((extracted / "metadata.json").read_text(encoding="utf-8"))
            if not (extracted / "model" / "model.bin").is_file() or not metadata.get("from_code"):
                raise ValueError("Incomplete translation package")
            shutil.move(str(extracted), str(base / name))
            print(f"Installed: {name}", flush=True)


if __name__ == "__main__":
    main()
