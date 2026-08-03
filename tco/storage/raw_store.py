"""Raw storage: сохранение исходных ответов и HTML-снимков.

Тело всегда сжимается gzip, для каждого объекта считается SHA-256, а имя
содержит источник, сценарий, момент и request id (DELTA §3.2). Поддерживаются
файловое хранилище и S3/MinIO; выбор — по наличию ``S3_ENDPOINT_URL``.

Недоступность raw storage не должна обрушивать расчет: писатель возвращает
ошибку, вызывающий код фиксирует ее в метриках и продолжает.
"""

from __future__ import annotations

import gzip
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from tco.core.config import Settings, get_settings
from tco.core.errors import StorageError
from tco.core.logging import get_logger, redact
from tco.core.utils import sha256_bytes, utcnow

logger = get_logger(__name__)


@dataclass(slots=True)
class StoredObject:
    """Результат сохранения объекта в raw storage."""

    ref: str
    size_bytes: int
    checksum_sha256: str
    content_type: str
    content_encoding: str = "gzip"


class RawStorageBackend(Protocol):
    def write(self, key: str, data: bytes, content_type: str) -> str: ...

    def read(self, ref: str) -> bytes: ...

    def delete(self, ref: str) -> bool: ...

    def exists(self, ref: str) -> bool: ...


class FileSystemBackend:
    """Файловое хранилище (значение по умолчанию для одной VM)."""

    scheme = "file"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        relative = ref.split("://", 1)[1] if "://" in ref else ref
        # Защита от выхода за пределы каталога хранилища.
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        if not str(candidate).startswith(str(root)):
            raise StorageError(f"Недопустимая ссылка на объект хранилища: {ref}")
        return candidate

    def write(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.scheme}://{key}"

    def read(self, ref: str) -> bytes:
        path = self._path(ref)
        if not path.exists():
            raise StorageError(f"Объект не найден в raw storage: {ref}")
        return path.read_bytes()

    def delete(self, ref: str) -> bool:
        path = self._path(ref)
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, ref: str) -> bool:
        return self._path(ref).exists()


class S3Backend:
    """S3/MinIO-совместимое хранилище."""

    scheme = "s3"

    def __init__(self, settings: Settings) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - опциональная зависимость
            raise StorageError(
                "Для S3-хранилища требуется пакет boto3 (extras: s3)"
            ) from exc

        self.bucket = settings.s3_bucket or "tco-raw"
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def _key(self, ref: str) -> str:
        if ref.startswith(f"{self.scheme}://"):
            without_scheme = ref[len(self.scheme) + 3 :]
            return without_scheme.split("/", 1)[1] if "/" in without_scheme else without_scheme
        return ref

    def write(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentEncoding="gzip",
        )
        return f"{self.scheme}://{self.bucket}/{key}"

    def read(self, ref: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(ref))
        return response["Body"].read()

    def delete(self, ref: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(ref))
        return True

    def exists(self, ref: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(ref))
            return True
        except Exception:  # noqa: BLE001 - любой сбой трактуем как отсутствие
            return False


class RawStore:
    """Фасад raw storage с gzip, checksum и структурированными ключами."""

    def __init__(self, backend: RawStorageBackend | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.backend = backend or self._default_backend()

    def _default_backend(self) -> RawStorageBackend:
        if self.settings.s3_endpoint_url and self.settings.s3_bucket:
            return S3Backend(self.settings)
        return FileSystemBackend(self.settings.raw_storage_dir)

    @staticmethod
    def build_key(
        *,
        kind: str,
        source_code: str,
        scenario_code: str | None,
        request_id: str,
        collected_at: datetime | None = None,
        extension: str = "json.gz",
    ) -> str:
        """Ключ объекта: ``kind/YYYY/MM/DD/source/scenario/timestamp-request.ext``."""
        moment = collected_at or utcnow()
        safe_scenario = _slug(scenario_code or "adhoc")
        safe_source = _slug(source_code)
        return (
            f"{kind}/{moment:%Y/%m/%d}/{safe_source}/{safe_scenario}/"
            f"{moment:%Y%m%dT%H%M%S%f}-{_slug(request_id) or 'noreq'}.{extension}"
        )

    # ------------------------------------------------------------------ #
    # Запись
    # ------------------------------------------------------------------ #

    def put_json(
        self,
        payload: Any,
        *,
        source_code: str,
        scenario_code: str | None,
        request_id: str,
        collected_at: datetime | None = None,
        kind: str = "raw",
    ) -> StoredObject:
        """Сохраняет JSON-ответ. Секреты вычищаются перед записью."""
        body = json.dumps(redact(payload), ensure_ascii=False, default=str).encode("utf-8")
        return self._put(
            body,
            kind=kind,
            source_code=source_code,
            scenario_code=scenario_code,
            request_id=request_id,
            collected_at=collected_at,
            content_type="application/json",
            extension="json.gz",
        )

    def put_html(
        self,
        html: str,
        *,
        source_code: str,
        scenario_code: str | None,
        request_id: str,
        collected_at: datetime | None = None,
    ) -> StoredObject:
        return self._put(
            html.encode("utf-8", errors="replace"),
            kind="html",
            source_code=source_code,
            scenario_code=scenario_code,
            request_id=request_id,
            collected_at=collected_at,
            content_type="text/html",
            extension="html.gz",
        )

    def put_bytes(
        self,
        data: bytes,
        *,
        source_code: str,
        scenario_code: str | None,
        request_id: str,
        content_type: str,
        extension: str,
        kind: str = "artifact",
        collected_at: datetime | None = None,
    ) -> StoredObject:
        return self._put(
            data,
            kind=kind,
            source_code=source_code,
            scenario_code=scenario_code,
            request_id=request_id,
            collected_at=collected_at,
            content_type=content_type,
            extension=extension,
        )

    def _put(
        self,
        body: bytes,
        *,
        kind: str,
        source_code: str,
        scenario_code: str | None,
        request_id: str,
        collected_at: datetime | None,
        content_type: str,
        extension: str,
    ) -> StoredObject:
        compressed = _gzip(body)
        key = self.build_key(
            kind=kind,
            source_code=source_code,
            scenario_code=scenario_code,
            request_id=request_id,
            collected_at=collected_at,
            extension=extension,
        )
        try:
            ref = self.backend.write(key, compressed, content_type)
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Не удалось сохранить объект в raw storage: {exc}") from exc
        return StoredObject(
            ref=ref,
            size_bytes=len(compressed),
            checksum_sha256=sha256_bytes(compressed),
            content_type=content_type,
        )

    # ------------------------------------------------------------------ #
    # Чтение и очистка
    # ------------------------------------------------------------------ #

    def get_bytes(self, ref: str) -> bytes:
        return _gunzip(self.backend.read(ref))

    def get_json(self, ref: str) -> Any:
        return json.loads(self.get_bytes(ref).decode("utf-8"))

    def get_text(self, ref: str) -> str:
        return self.get_bytes(ref).decode("utf-8", errors="replace")

    def delete(self, ref: str) -> bool:
        try:
            return self.backend.delete(ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось удалить объект raw storage", ref=ref, error=str(exc))
            return False

    def health(self) -> dict[str, Any]:
        """Проверка доступности хранилища на запись и чтение."""
        probe = b"tco-healthcheck"
        try:
            stored = self.put_bytes(
                probe,
                source_code="healthcheck",
                scenario_code="healthcheck",
                request_id="probe",
                content_type="application/octet-stream",
                extension="bin.gz",
                kind="health",
            )
            data = self.get_bytes(stored.ref)
            self.delete(stored.ref)
            return {"available": data == probe, "backend": type(self.backend).__name__}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "backend": type(self.backend).__name__, "error": str(exc)}


def _gzip(data: bytes) -> bytes:
    buffer = io.BytesIO()
    # mtime=0 делает содержимое побайтово воспроизводимым.
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=0) as handle:
        handle.write(data)
    return buffer.getvalue()


def _gunzip(data: bytes) -> bytes:
    return gzip.decompress(data)


def _slug(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in str(value))[:64]


_store: RawStore | None = None


def get_raw_store() -> RawStore:
    global _store
    if _store is None:
        _store = RawStore()
    return _store


def reset_raw_store() -> None:
    global _store
    _store = None
