import asyncio
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456789:test-token')
os.environ.setdefault('BOT_BACKEND_API_KEY', 'a' * 64)

from aiogram.methods import EditMessageReplyMarkup, SendMessage, GetUpdates
from aiogram.types import CallbackQuery, Chat, Message, User, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from app.middleware.interface import InterfaceRequests, InterfaceCallbacks, ui_actor, ui_state, ui_language
from app.middleware.health import PollHeartbeatMiddleware
from app.handlers.admin_statistics import _section_text, statistics_section
from app.keyboards.admin_statistics import build_statistics_section_keyboard
from app.keyboards.experience import build_user_ticket_list_keyboard
from app.keyboards.admin import build_admin_home_keyboard
from app.admin_runtime_settings import runtime_settings_text
from app.main import build_progress_text
from app.handlers.experience import _ticket_detail_text
from app.handlers.operations import LABELS


class MemoryRedis:
    """Small atomic key store for middleware transport tests."""
    def __init__(self): self.values = {}
    async def set(self, key, value, **kwargs): self.values[key] = value
    async def get(self, key): return self.values.get(key)
    async def getdel(self, key): return self.values.pop(key, None)


def message(id=40, user_id=100):
    return Message(message_id=id, date=datetime.now(timezone.utc), chat=Chat(id=user_id, type='private'), text='Review change')


def callback(data, user_id=100, msg=None):
    return CallbackQuery(id='test', from_user=User(id=user_id, is_bot=False, first_name='A'), chat_instance='x', data=data, message=msg or message())


def test_english_download_admin_settings_and_statistics():
    token = ui_language.set('en')
    try:
        values = [build_progress_text(1, '1080p', {'progress': 25, 'speed': 1000, 'eta': 30}, paused=True),
            runtime_settings_text([]),
            _section_text('downloads', {'downloads': {'by_site': [{'site': 'Vimeo', 'count': 2}]}}, '1mo'),
            _section_text('finance', {'finance': {'currencies': {'USDT': {'total': '2.5000', 'successful': 1}, 'IRT': {'total': '10000'}}}}, '7d'),
            _section_text('subscriptions', {'subscriptions': {'by_plan': [{'name': 'پلن فارسی', 'name_en': 'Starter', 'count': 1}]}}, '1mo')]
        # Static role and setting labels must resolve at render time too.
        values += [b.text for r in build_admin_home_keyboard(set(), is_superadmin=True).inline_keyboard for b in r]
        assert all(not re.search('[\u0600-\u06ff]', value) for value in values)
        assert 'Vimeo: 2' in values[2]
        assert '2.5000 USDT' in values[3]
        assert '10,000 Toman' in values[3]
        assert 'Starter: 1' in values[4]
        assert 'پلن فارسی' not in values[4]
        assert 'Download paused' in values[0]
        assert 'Ticket #7' in _ticket_detail_text({
            'id': 7, 'category': 'payment', 'status': 'waiting_user',
            'user': {'telegram_id': 100}, 'plan_name': 'Starter', 'messages': [],
        }, 'en')
        assert 'Notifications' in __import__('app.localization', fromlist=['tr']).tr(LABELS['notifications.enabled'])
    finally:
        ui_language.reset(token)


def test_six_requested_periods_and_active_selection():
    board = build_statistics_section_keyboard('downloads', period='6mo')
    periods = [b.callback_data.rsplit(':', 1)[-1] for r in board.inline_keyboard[:2] for b in r]
    assert periods == ['1yr', '6mo', '3mo', '1mo', '7d', 'today']
    assert board.inline_keyboard[0][1].text.startswith('✓ ')
    chart_board = build_statistics_section_keyboard('charts', period='6mo')
    chart_periods = [b.callback_data.rsplit(':', 1)[-1] for r in chart_board.inline_keyboard[:2] for b in r]
    assert chart_periods == periods
    assert chart_board.inline_keyboard[0][1].text.startswith('✓ ')
    assert chart_board.inline_keyboard[-3][0].callback_data == 'admin:stats:charts:6mo'
    assert chart_board.inline_keyboard[2][0].callback_data.endswith(':6mo')


def test_english_ticket_list_localizes_status_codes():
    board = build_user_ticket_list_keyboard(
        [{'id': 7, 'status': 'in_progress'}], page=1, total=1, language='en'
    )
    assert 'In progress' in board.inline_keyboard[0][0].text
    assert 'in_progress' not in board.inline_keyboard[0][0].text


def test_statistics_callback_applies_period_and_site_page(monkeypatch):
    from app.handlers import admin_statistics as module
    show = AsyncMock()
    monkeypatch.setattr(module, '_show', show)
    asyncio.run(statistics_section(callback('admin:stats:downloads:6mo:2')))
    show.assert_awaited_once()
    assert show.call_args.kwargs == {'period': '6mo', 'page': 2}


def test_confirmation_binds_actor_message_form_and_consumes_once(monkeypatch):
    monkeypatch.setattr(CallbackQuery, 'answer', AsyncMock())
    async def run():
        store = MemoryRedis()
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=100, user_id=100))
        await state.update_data(amount=1)
        context_tokens = [ui_actor.set(100), ui_state.set(state)]
        try:
            outgoing = InterfaceRequests(store)
            sent = AsyncMock(return_value=message())
            method = SendMessage(chat_id=100, text='Review', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='Confirm', callback_data='admin:finance:confirm')]]))
            await outgoing(sent, None, method)
            data = sent.call_args.args[1].reply_markup.inline_keyboard[0][0].callback_data
            handler = AsyncMock()
            incoming = InterfaceCallbacks(store)
            await incoming(handler, callback(data, user_id=999), {'state': state})
            await incoming(handler, callback(data, msg=message(id=41)), {'state': state})
            handler.assert_not_awaited()
            await state.update_data(amount=2)
            await incoming(handler, callback(data), {'state': state})
            handler.assert_not_awaited()
            await state.update_data(amount=1)
            await asyncio.gather(*[incoming(handler, callback(data), {'state': state}) for _ in range(2)])
            handler.assert_awaited_once()
            assert handler.call_args.args[0].data == 'admin:finance:confirm'
            # Old raw callbacks cannot bypass the signed review screen.
            await incoming(handler, callback('admin:finance:confirm'), {'state': state})
            assert handler.await_count == 1
        finally:
            ui_actor.reset(context_tokens[0]); ui_state.reset(context_tokens[1])
    asyncio.run(run())


def test_pagination_retains_all_rows_and_footer(monkeypatch):
    monkeypatch.setattr(CallbackQuery, 'answer', AsyncMock())
    edit = AsyncMock()
    monkeypatch.setattr(Message, 'edit_reply_markup', edit)
    async def run():
        redis = MemoryRedis(); token = ui_actor.set(100)
        try:
            rows = [[InlineKeyboardButton(text=str(i), callback_data=f'item:{i}')] for i in range(20)]
            rows += [[InlineKeyboardButton(text='Back', callback_data='admin:open')]]
            send = AsyncMock(return_value=message())
            await InterfaceRequests(redis)(send, None, SendMessage(chat_id=100, text='Items', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)))
            initial = send.call_args.args[1].reply_markup.inline_keyboard
            assert initial[-1][0].callback_data == 'admin:open'
            next_page = initial[-2][-1].callback_data
            handler = AsyncMock()
            await InterfaceCallbacks(redis)(handler, callback(next_page), {})
            page2 = edit.call_args.kwargs['reply_markup'].inline_keyboard
            assert page2[0][0].callback_data == 'item:7'
            assert page2[-1][0].callback_data == 'admin:open'
            next_page = page2[-2][-1].callback_data
            await InterfaceCallbacks(redis)(handler, callback(next_page), {})
            page3 = edit.call_args.kwargs['reply_markup'].inline_keyboard
            assert page3[-3][0].callback_data == 'item:19'
            handler.assert_not_awaited()
        finally: ui_actor.reset(token)
    asyncio.run(run())


def test_heartbeat_requires_successful_poll():
    async def run():
        redis = MemoryRedis(); middleware = PollHeartbeatMiddleware(redis)
        failed = AsyncMock(side_effect=RuntimeError('offline'))
        try: await middleware(failed, None, GetUpdates())
        except RuntimeError: pass
        assert not redis.values
        await middleware(AsyncMock(return_value=[]), None, GetUpdates())
        assert float(redis.values['mediahub:bot:poll_heartbeat']) > 0
    asyncio.run(run())


def test_long_text_pagination_is_lossless_and_keeps_inline_navigation(monkeypatch):
    monkeypatch.setattr(CallbackQuery, 'answer', AsyncMock())
    edit = AsyncMock()
    monkeypatch.setattr(Message, 'edit_text', edit)

    async def run():
        redis = MemoryRedis()
        token = ui_actor.set(100)
        try:
            # Include astral Unicode and an HTML link. The middleware stores
            # complete pages, while Telegram receives plain text pages.
            body = ('A' * 4100) + ' 🧪 <a href="https://example.test/x">link</a> ' + ('B' * 1200)
            send = AsyncMock(return_value=message())
            method = SendMessage(chat_id=100, text=body, parse_mode='HTML')
            await InterfaceRequests(redis)(send, None, method)
            sent_method = send.call_args.args[1]
            first = sent_method.text
            markup = sent_method.reply_markup
            assert len(markup.inline_keyboard) == 1
            page_callback = markup.inline_keyboard[0][-1].callback_data
            raw = next(value for key, value in redis.values.items() if key.startswith('mediahub:ui:textpage:'))
            payload = __import__('json').loads(raw)
            assert ''.join(payload['parts']) == 'A' * 4100 + ' 🧪 link (https://example.test/x) ' + 'B' * 1200
            assert first == payload['parts'][0]
            await InterfaceCallbacks(redis)(AsyncMock(), callback(page_callback), {})
            assert edit.await_count == 1
            assert edit.call_args.args[0] == payload['parts'][1]
        finally:
            ui_actor.reset(token)
    asyncio.run(run())
