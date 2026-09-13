import os
import subprocess

for key, value in {
    "SECRET_KEY": "test-secret",
    "JWT_SECRET_KEY": "test-jwt-secret",
    "POSTGRES_DB": "test",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
    "TELEGRAM_BOT_TOKEN": "123456789:test-token",
}.items():
    os.environ.setdefault(key, value)

import pytest

from app.schemas.download import DownloadCreate
from app.services.media_formats import (
    AUDIO_OUTPUT_FORMATS,
    VIDEO_OUTPUT_FORMATS,
    normalize_output_format,
    output_format_kind,
)
from app.services.download import DownloadFormatError, DownloadService
from app.workers.tasks import download as download_worker


def test_download_schema_normalizes_and_rejects_output_formats():
    data = DownloadCreate(
        telegram_id=123,
        source_url="upload://sample.mp4",
        media_type="convert",
        output_format=".MP4",
    )
    assert data.output_format == "mp4"
    assert normalize_output_format(" FLAC ") == "flac"
    assert output_format_kind(".opus") == "audio"
    assert output_format_kind("webm") == "video"
    with pytest.raises(ValueError):
        DownloadCreate(
            telegram_id=123,
            source_url="upload://sample.mp4",
            media_type="convert",
            output_format="gif",
        )


@pytest.mark.asyncio
async def test_download_service_rejects_incompatible_media_targets_before_db_access():
    service = DownloadService(None)
    with pytest.raises(DownloadFormatError):
        await service.create_job(
            source_url="upload://sample.mp4",
            telegram_id=123,
            media_type="convert",
            output_format=None,
        )
    with pytest.raises(DownloadFormatError):
        await service.create_job(
            source_url="https://example.test/audio",
            telegram_id=123,
            media_type="audio",
            output_format="mp4",
        )


def test_ffmpeg_conversion_supports_all_exposed_targets(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=8000",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(source),
        ],
        check=True,
    )
    monkeypatch.setattr(download_worker, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(download_worker, "_check_job_control", lambda _job_id: None)
    monkeypatch.setattr(download_worker, "_update_download_stats", lambda **_kwargs: None)

    source_bytes = source.read_bytes()
    for index, output_format in enumerate(AUDIO_OUTPUT_FORMATS + VIDEO_OUTPUT_FORMATS, start=1):
        input_path = tmp_path / f"source-{index}.mp4"
        input_path.write_bytes(source_bytes)
        output = download_worker._run_ffmpeg_conversion(
            job_id=9000 + index,
            input_path=input_path,
            output_format=output_format,
            max_download_bytes=50 * 1024 * 1024,
        )
        assert output.suffix == f".{output_format}"
        assert output.is_file() and output.stat().st_size > 0
        output.unlink()


def test_uploaded_source_resolution_rejects_path_traversal(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = incoming / "upload-safe.mp4"
    source.write_bytes(b"sample")
    monkeypatch.setattr(download_worker, "DOWNLOAD_DIR", tmp_path)

    assert download_worker._resolve_uploaded_source("upload://upload-safe.mp4") == source
    with pytest.raises(RuntimeError):
        download_worker._resolve_uploaded_source("upload://../secret.mp4")
