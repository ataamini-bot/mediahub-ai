"""Temporary bytes are removed; links, ownership, quotas and admin logs survive."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.api.customers import router as customer_router
from app.api.downloads import router as download_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.download_job import DownloadJob, DownloadJobStatus as Status
from app.models.user import User, UserStatus
from app.services.customers import CustomerService
from app.services.download_access import DownloadAccessService, DownloadEntitlement, DailyDownloadLimitReached
from app.services.media_files import release_files, remove_orphans, sweep, unlink_inside


@pytest_asyncio.fixture
async def records(tmp_path, monkeypatch):
    assert settings.app_env == 'test', 'Disposable test database required'
    monkeypatch.setenv('DOWNLOAD_DIR', str(tmp_path))
    async with AsyncSessionLocal() as db:
        users = [User(telegram_id=8_750_000_000_000+uuid.uuid4().int%100_000_000, first_name='File cleanup test', status=UserStatus.ACTIVE) for _ in range(3)]
        db.add_all(users); await db.flush()
        db.add(AdminAccount(user_id=users[0].id, is_superadmin=True, is_active=True, created_by_user_id=users[0].id))
        await db.commit()
    state = SimpleNamespace(admin=users[0], user=users[1], outsider=users[2], root=tmp_path, now=datetime.now(timezone.utc))
    try:
        yield state
    finally:
        async with AsyncSessionLocal() as db:
            ids = [u.id for u in users]
            await db.execute(delete(DownloadJob).where(DownloadJob.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.commit()
        await engine.dispose()


async def add_job(db, records, **changes):
    job = DownloadJob(**{**dict(user_id=records.user.id, source_url='https://youtube.com/watch?v=retained-link',
        status=Status.COMPLETED, progress=100, media_type='video', quality='720p', format_id='abc',
        file_size=12, created_at=records.now, completed_at=records.now), **changes})
    db.add(job); await db.flush()
    return job


@pytest.mark.asyncio
async def test_delivery_cleanup_preserves_source_details_quota_and_admin_activity(records):
    async with AsyncSessionLocal() as db:
        job = await add_job(db, records)
        path = records.root / f'{job.id}.mp4'; path.write_bytes(b'private-media')
        part = records.root / f'{job.id}.video.part'; part.write_bytes(b'partial')
        job.file_path = str(path)
        await db.commit()
        entitlement = DownloadEntitlement(user_id=records.user.id, plan_id=None, plan_name='test', plan_slug='paid',
            daily_download_limit=1, max_file_size_mb=500, max_quality=1080, max_concurrent_downloads=1,
            priority_processing=False, forced_join_required=False)
        service = DownloadAccessService(db)
        with pytest.raises(DailyDownloadLimitReached):
            await service._validate_download_limit(entitlement)
        await service.mark_delivered(job.id)
        delivered = job.delivered_at
        await db.run_sync(lambda session: release_files(session, job))
        await db.commit()
        assert not path.exists() and not part.exists()
        assert job.source_url == 'https://youtube.com/watch?v=retained-link'
        assert job.user_id == records.user.id and job.file_size == 12 and job.quality == '720p'
        assert job.delivered_at == delivered and job.files_removed_at and job.status == Status.COMPLETED
        assert not await db.run_sync(lambda session: release_files(session, job))
        with pytest.raises(DailyDownloadLimitReached):
            await service._validate_download_limit(entitlement)
        activity = await CustomerService(db).download_activity(records.user.telegram_id)
        assert activity['items'][0]['source_url'] == job.source_url
        assert activity['items'][0]['files_removed_at']
        await db.rollback()


@pytest.mark.asyncio
async def test_cleanup_covers_failed_upload_and_conversion_inputs_without_deleting_logs(records):
    async with AsyncSessionLocal() as db:
        job = await add_job(db, records, status=Status.FAILED, media_type='convert', source_url='upload://upload-private.mp4')
        incoming = records.root / 'incoming'; incoming.mkdir()
        source = incoming / 'upload-private.mp4'; source.write_bytes(b'input')
        partial = records.root / f'{job.id}.converted.wav.part'; partial.write_bytes(b'partial')
        await db.commit()
        await db.run_sync(lambda s: sweep(s, now=records.now))
        await db.commit()
        assert not source.exists() and not partial.exists()
        assert job.source_url == 'upload://upload-private.mp4' and job.user_id == records.user.id
        assert job.status == Status.FAILED and job.files_removed_at


@pytest.mark.asyncio
async def test_janitor_preserves_inflight_and_recent_undelivered_files(records):
    async with AsyncSessionLocal() as db:
        active = await add_job(db, records, status=Status.PROCESSING)
        paused = await add_job(db, records, status=Status.PAUSED)
        fresh = await add_job(db, records)
        old = await add_job(db, records, completed_at=records.now-timedelta(hours=25))
        for job in (active, paused, fresh, old):
            path = records.root / f'{job.id}.mp4'; path.write_bytes(b'file'); job.file_path=str(path)
        await db.commit()
        await db.run_sync(lambda s: sweep(s, now=records.now))
        await db.commit()
        assert old.files_removed_at and not (records.root / f'{old.id}.mp4').exists()
        for job in (active, paused, fresh):
            assert job.files_removed_at is None and (records.root / f'{job.id}.mp4').exists()
            if job.status != Status.COMPLETED:
                assert not await db.run_sync(lambda s: release_files(s, job))
        assert old.source_url and old.user_id == records.user.id


@pytest.mark.asyncio
async def test_activity_endpoint_requires_internal_key_and_admin_permission(records):
    async with AsyncSessionLocal() as db:
        for i in range(7):
            await add_job(db, records, source_url=f'https://example.com/source-{i}')
        await add_job(db, records, user_id=records.outsider.id, source_url='https://example.com/other-user')
        await db.commit()
    app = FastAPI(); app.include_router(customer_router); app.include_router(download_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        path = f'/admin/customers/{records.user.telegram_id}/downloads'
        assert (await client.get(path, params={'actor_telegram_id':records.admin.telegram_id})).status_code == 401
        headers = {'X-Internal-Api-Key':settings.bot_backend_api_key}
        for actor_id in (records.user.telegram_id, records.outsider.telegram_id):
            response = await client.get(path, headers=headers, params={'actor_telegram_id':actor_id})
            assert response.status_code == 403
        response = await client.get(path, headers=headers, params={'actor_telegram_id':records.admin.telegram_id})
        assert response.status_code == 200
        first = response.json()
        assert first['total']==7 and len(first['items'])==5
        assert all('other-user' not in row['source_url'] for row in first['items'])
        second = (await client.get(path, headers=headers, params={'actor_telegram_id':records.admin.telegram_id,'page':2})).json()
        assert len(second['items'])==2
        assert {j['id'] for j in first['items']}.isdisjoint(j['id'] for j in second['items'])


def test_file_removal_never_follows_symlinks_outside_media_volume(tmp_path):
    root = tmp_path / 'downloads'; root.mkdir()
    private = tmp_path / 'financial.env'; private.write_text('keep')
    link = root / '1.mp4'; link.symlink_to(private)
    directory = root / 'incoming'; directory.symlink_to(tmp_path, target_is_directory=True)
    for path in (link, directory/'financial.env', private):
        unlink_inside(path, root)
    assert private.read_text()=='keep' and not link.exists()


@pytest.mark.asyncio
async def test_orphan_cleanup_keeps_live_files_and_does_not_delete_unknown_files(records):
    async with AsyncSessionLocal() as db:
        job = await add_job(db, records, status=Status.PROCESSING)
        await db.commit()
        paths = [records.root/f'{job.id}.mp4', records.root/'999999999.mp4', records.root/'cover-test.jpg', records.root/'config.env']
        for path in paths:
            path.write_bytes(b'data'); os.utime(path,(0,0))
        await db.run_sync(lambda s: remove_orphans(s, now=records.now))
        assert [p.exists() for p in paths] == [True,False,False,True]
