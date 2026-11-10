import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.bot_experience import RequiredChannel
from app.services.installation import configure
from app.services.operations import notification_routes


@pytest.mark.asyncio
async def test_console_bootstrap_topics_and_language_channels_are_atomic_and_reusable(monkeypatch):
    assert settings.app_env == "test"
    telegram_id = 8_950_000_000_000 + uuid.uuid4().int % 100_000_000
    monkeypatch.setattr(settings, "telegram_superadmin_id", telegram_id)
    try:
        async with AsyncSessionLocal() as db:
            try:
                assert await configure(db, {"action": "bootstrap"}) == {"bootstrap": True}
                topics = dict(zip(("monitoring", "payments", "backups", "system", "support"), (2, 4, 6, 8, 10)))
                await configure(db, {"action": "forum", "routes": {"chat_id": -100999888, "topics": topics}})
                routes = await notification_routes(db)
                assert routes == {"enabled": True, "chat_id": -100999888, "topics": topics}
                data = {"chat_id": "-100" + str(telegram_id), "title": "Test channel", "language": "fa",
                        "invite_url": "https://t.me/test_mediahub", "sort_order": 1, "is_active": True}
                await configure(db, {"action": "channel", "channel": data})
                await configure(db, {"action": "channel", "channel": {**data, "language": "en"}})
                rows = (await db.scalars(select(RequiredChannel).where(RequiredChannel.chat_id == data["chat_id"]))).all()
                assert len(rows) == 1 and rows[0].language == "en"
                # An invalid incomplete forum must not partially change the destinations.
                with pytest.raises(ValueError):
                    await configure(db, {"action": "forum", "routes": {"chat_id": -100123, "topics": {"system": 4}}})
                assert (await notification_routes(db))["chat_id"] == -100999888
            finally:
                await db.rollback()
    finally:
        await engine.dispose()
