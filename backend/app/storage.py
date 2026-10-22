from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


class LocalObjectStore:
    """Filesystem-backed object store for the P0 single-node deployment."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def upload_path(self, upload_id: str, filename: str) -> Path:
        folder = self.root / "uploads" / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        return folder / f"source{suffix}"

    def document_path(self, document_id: str, filename: str) -> Path:
        folder = self.root / "documents" / document_id / "source"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / Path(filename).name

    def run_dir(self, document_id: str, run_id: str) -> Path:
        path = self.root / "documents" / document_id / "runs" / run_id
        path.mkdir(parents=True, exist_ok=False)
        return path

    def write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(data)

    def copy(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

