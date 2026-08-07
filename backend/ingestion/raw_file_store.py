"""Raw Files store — secure upload, access-controlled."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Iterable, List

from backend.ingestion.models import new_id, utc_now_iso

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".tiff"}
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB


class UploadRejected(Exception):
    pass


class RawFileStore:
    """
    Access-controlled storage for original uploaded documents.

    Every write is namespaced by document_id so a directory-traversal
    attempt or filename collision can't clobber another tenant's file.
    RBAC tags are required at upload time and are what later reads are
    gated on.
    """

    def __init__(self, root: str = "./raw_files"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _validate(self, filename: str, size_bytes: int, rbac_tags: Iterable[str]) -> None:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise UploadRejected(f"extension '{ext}' not allowed")
        if size_bytes <= 0:
            raise UploadRejected("empty file")
        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise UploadRejected(f"file exceeds {MAX_FILE_SIZE_BYTES} byte limit")
        if not list(rbac_tags):
            raise UploadRejected("at least one RBAC tag is required for access control")
        if any(part in ("..", "") for part in Path(filename).parts):
            raise UploadRejected("invalid filename")

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def save(
        self,
        *,
        file_bytes: bytes,
        original_filename: str,
        uploader_id: str,
        rbac_tags: List[str],
    ) -> dict:
        """Persist a raw upload. Returns what the caller needs to write a
        Metadata DB row — this function does NOT touch the Metadata DB."""
        self._validate(original_filename, len(file_bytes), rbac_tags)

        document_id = new_id()
        ext = Path(original_filename).suffix.lower()
        doc_dir = self.root / document_id
        doc_dir.mkdir(parents=True, exist_ok=False)

        dest_path = doc_dir / f"original{ext}"
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        os.chmod(dest_path, 0o600)  # owner read/write only

        return {
            "document_id": document_id,
            "storage_path": str(dest_path),
            "checksum_sha256": self._sha256(file_bytes),
            "uploader_id": uploader_id,
            "rbac_tags": list(rbac_tags),
            "original_filename": original_filename,
            "upload_timestamp": utc_now_iso(),
        }

    def read(self, document_id: str, requester_rbac_tags: Iterable[str], required_tags: Iterable[str]) -> bytes:
        """Access-controlled read: requester must hold at least one of the
        document's required RBAC tags."""
        if not set(requester_rbac_tags) & set(required_tags):
            raise PermissionError(f"requester lacks RBAC access to document {document_id}")

        doc_dir = self.root / document_id
        if not doc_dir.exists():
            raise FileNotFoundError(document_id)
        files = list(doc_dir.glob("original.*"))
        if not files:
            raise FileNotFoundError(document_id)
        return files[0].read_bytes()

    def delete(self, document_id: str) -> None:
        doc_dir = self.root / document_id
        if doc_dir.exists():
            shutil.rmtree(doc_dir)