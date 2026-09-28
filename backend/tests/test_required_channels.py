"""Language-scoped required channels, persistence and upgrade compatibility."""
import importlib.util
from pathlib import Path
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import text

from app.db.session import AsyncSessionLocal, engine
from app.models.user import User, UserStatus
from app.schemas.experience import RequiredChannelCreate, RequiredChannelUpdate, RequiredChannelResponse
from app.services.bot_experience import BotExperienceError, BotExperienceService


def data(language="all", number=1, **changes):
    return {"chat_id": f"-100{900000 + number}", "title": f"Channel {number}",
            "invite_url": f"https://t.me/example{number}", "language": language, **changes}


@pytest.mark.parametrize("language", ["all", "fa", "en"])
def test_channel_language_round_trips_in_validation(language):
    request = RequiredChannelCreate(actor_telegram_id=42, **data(language))
    assert BotExperienceService._validate_channel(request.model_dump())["language"] == language
    assert RequiredChannelUpdate(actor_telegram_id=42, language=language).language == language


@pytest.mark.parametrize("language", ["de", "", None])
def test_invalid_channel_language_cannot_broaden_or_remove_membership(language):
    with pytest.raises(ValidationError):
        RequiredChannelCreate(actor_telegram_id=42, **data(language))
    with pytest.raises(ValidationError):
        RequiredChannelUpdate(actor_telegram_id=42, language=language)
    with pytest.raises(BotExperienceError):
        BotExperienceService._validate_channel(data(language))


def test_legacy_creation_applies_to_both_languages():
    payload = data()
    del payload["language"]
    assert RequiredChannelCreate(actor_telegram_id=42, **payload).language == "all"
    assert BotExperienceService._validate_channel(payload)["language"] == "all"


@pytest.mark.asyncio
async def test_postgres_keeps_multiple_channels_and_filters_configuration_by_language():
    try:
        async with AsyncSessionLocal() as db:
            actor = User(telegram_id=9_500_000_000_000 + uuid.uuid4().int % 100_000_000,
                         first_name="Channel test", status=UserStatus.ACTIVE)
            db.add(actor)
            await db.flush()
            service = BotExperienceService(db)
            ids = []
            prefix = uuid.uuid4().int % 1_000_000_000
            for i, language in enumerate(("fa", "fa", "en", "all", "fa")):
                row = await service.create_channel(actor_user_id=actor.id, actor_telegram_id=actor.telegram_id,
                    data=data(language, prefix + i, sort_order=i, is_active=i != 4))
                ids.append(row.id)
                response = RequiredChannelResponse(**service.serialize_channel(row))
                assert response.language == language
            # A second insert must not replace or hide the first record.
            assert set(ids) <= {row.id for row in await service.list_channels()}

            async def visible(language):
                config = await service.configuration(language)
                return [c["id"] for c in config["required_channels"] if c["id"] in ids]

            assert await visible("fa") == [ids[0], ids[1], ids[3]]
            assert await visible("en") == [ids[2], ids[3]]
            await service.update_channel(channel_id=ids[0], actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id, changes={"language": "en"})
            assert await visible("fa") == [ids[1], ids[3]]
            assert await visible("en") == [ids[0], ids[2], ids[3]]
            # Editing another field preserves the channel's chosen language.
            changed = await service.update_channel(channel_id=ids[0], actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id, changes={"title": "Updated"})
            assert changed.language == "en"
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_migration_preserves_existing_channels_and_default():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/fc2d3e4f5a6b_add_required_channel_language.py"
    spec = importlib.util.spec_from_file_location("channel_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(conn):
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

    try:
        async with engine.begin() as conn:
            # The temporary table shadows only this connection; production-like
            # tables in the shared CI database are untouched.
            await conn.execute(text("CREATE TEMP TABLE required_channels (id integer PRIMARY KEY, title text) ON COMMIT DROP"))
            await conn.execute(text("INSERT INTO required_channels VALUES (1, 'First'), (2, 'Second')"))
            await conn.run_sync(migrate)
            assert (await conn.execute(text("SELECT id, title, language FROM required_channels ORDER BY id"))).all() == [
                (1, "First", "all"), (2, "Second", "all"),
            ]
            await conn.execute(text("INSERT INTO required_channels(id, title) VALUES (3, 'Third')"))
            assert await conn.scalar(text("SELECT language FROM required_channels WHERE id=3")) == "all"
    finally:
        await engine.dispose()
