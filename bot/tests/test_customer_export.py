import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")
os.environ.setdefault("BOT_BACKEND_API_KEY", "a" * 64)

import pytest
from app.handlers import customers
from app.middleware.interface import ui_language


def message(chat_id=42, kind="private", text="۷۸۹", **kw):
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=kind), from_user=SimpleNamespace(id=42),
                           answer=AsyncMock(), text=text, answer_document=kw.get("document", AsyncMock()))


@pytest.mark.asyncio
async def test_export_prompt_requires_access_and_numeric_input(monkeypatch):
    state = AsyncMock()
    msg = message()
    cb = SimpleNamespace(message=msg, from_user=msg.from_user, answer=AsyncMock())
    api = AsyncMock(side_effect=customers.BackendAPIError(status_code=403, detail="Denied"))
    monkeypatch.setattr(customers, "api", api)
    await customers.ask_export_id(cb, state)
    state.set_state.assert_not_awaited()
    msg.answer.assert_not_awaited()
    api.side_effect = None
    await customers.ask_export_id(cb, state)
    state.set_state.assert_awaited_once_with(customers.CustomerStates.export_id)
    export = AsyncMock(return_value=True)
    monkeypatch.setattr(customers, "send_download_export", export)
    for text in ("0", "9223372036854775808", "123;456", "@someone"):
        await customers.receive_export_id(message(text=text), state)
    export.assert_not_awaited()
    for text in ("۷۸۹", "٧٨٩", "789"):
        msg = message(text=text)
        await customers.receive_export_id(msg, state)
        export.assert_awaited_with(msg, 42, 789)


@pytest.mark.asyncio
@pytest.mark.parametrize(("chat_id", "kind"), [(-100222, "supergroup"), (789, "private")])
async def test_export_never_replies_to_group_or_target_chat(monkeypatch, chat_id, kind):
    session = AsyncMock()
    monkeypatch.setattr(customers.aiohttp, "ClientSession", session)
    msg = message(chat_id, kind)
    cb = SimpleNamespace(message=msg, from_user=msg.from_user, data="customer:xlsx:789", answer=AsyncMock())
    await customers.export_from_profile(cb, AsyncMock())
    assert not await customers.send_download_export(msg, 42, 789)
    session.assert_not_called()
    msg.answer_document.assert_not_awaited()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 204, 403, 404, 500])
async def test_file_is_streamed_only_after_the_backend_authorizes_actor(monkeypatch, status):
    class Context:
        def __init__(self, obj): self.obj = obj
        async def __aenter__(self): return self.obj
        async def __aexit__(self, *args): pass

    async def chunks(size):
        assert size > 0
        for part in (b"PK", b"excel"):
            yield part

    response = SimpleNamespace(status=status, headers={"X-Report-Rows": "17"},
                               content=SimpleNamespace(iter_chunked=chunks))
    def get(url, *, params, headers, allow_redirects):
        assert url.endswith("/admin/customers/789/downloads.xlsx")
        assert params == {"actor_telegram_id": "42", "language": "en"}
        assert headers == {"X-Internal-API-Key": "test-internal-key"} and not allow_redirects
        return Context(response)
    monkeypatch.setattr(customers, "_internal_headers", lambda: {"X-Internal-API-Key": "test-internal-key"})
    monkeypatch.setattr(customers.aiohttp, "ClientSession", lambda **kwargs: Context(SimpleNamespace(get=get)))
    async def document(upload, *, caption, protect_content):
        assert protect_content is False and upload.filename == "downloads-789.xlsx" and "17 requests" in caption
        assert b"".join([part async for part in upload.read(None)]) == b"PKexcel"
    msg = message(document=AsyncMock(side_effect=document))
    lang = ui_language.set("en")
    try:
        assert await customers.send_download_export(msg, 42, 789) is (status == 200)
    finally:
        ui_language.reset(lang)
    if status == 200:
        msg.answer_document.assert_awaited_once()
    else:
        msg.answer_document.assert_not_awaited()
        msg.answer.assert_awaited_once()
