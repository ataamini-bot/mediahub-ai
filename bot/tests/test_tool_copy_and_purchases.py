import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import runtime_config
from app.tool_texts import TOOL_TEXTS, tool_text, validate_tool_text
from app.handlers import media_tools, payments
from app.keyboards.admin import build_admin_plan_detail_keyboard
from app.keyboards.admin_experience import build_copy_items_keyboard
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError


def test_catalogs_agree_and_all_defaults_validate():
    root = Path(__file__).resolve().parents[2]
    assert (root/'bot/app/media_tool_texts.json').read_bytes() == (root/'backend/app/media_tool_texts.json').read_bytes()
    for key, entry in TOOL_TEXTS.items():
        for language in ('fa','en'):
            validate_tool_text(key, entry[language])
    callbacks = [b.callback_data for row in build_copy_items_keyboard('fa','tools').inline_keyboard for b in row]
    assert all(len(value.encode()) <= 64 for value in callbacks)


@pytest.mark.parametrize('value', ['{title.__class__}', '{title[0]}', '{secret}', '{title!r}', '{title:>1000000}', '{'])
def test_text_override_rejects_nonliteral_placeholder_access(value):
    with pytest.raises(ValueError):
        validate_tool_text('music_identity', value)


def test_runtime_overrides_language_html_and_invalid_fallback(monkeypatch):
    monkeypatch.setattr(runtime_config, '_cache', {'fa':(9999999999, {'tool_texts':{
        'music_identity':'<script>{title}</script>', 'confirm_and_start':'بزن بریم',
        'other_formats':'{private}'}})})
    token=ui_language.set('fa')
    try:
        assert tool_text('music_identity',title='A &amp; B',artist='Artist') == '&lt;script&gt;A &amp; B&lt;/script&gt;'
        assert tool_text('confirm_and_start') == 'بزن بریم'
        assert tool_text('other_formats') == TOOL_TEXTS['other_formats']['fa']
        ui_language.set('en')
        assert tool_text('confirm_and_start') == TOOL_TEXTS['confirm_and_start']['en']
    finally:
        ui_language.reset(token)


def test_portrait_offer_uses_separate_product_and_no_coupon():
    item={'code':'portrait-once','purpose':'portrait','label':'One portrait','price':'1.2500'}
    assert payments._find_offer({'offers':[], 'portrait_offer':item},'portrait-once') is item
    token=ui_language.set('en')
    try:
        body=payments._offer_details_text(item,'en')
        assert 'without a subscription' in body and '1.25' in body
    finally:
        ui_language.reset(token)
    buttons=[b.callback_data for row in build_payment_offer_detail_keyboard('en',portrait=True).inline_keyboard for b in row]
    assert 'credit:pay:start' in buttons and 'payment:offer:continue' in buttons
    assert not any('coupon' in data for data in buttons)


def test_both_free_and_paid_plan_have_separate_quota_controls():
    for system in (False, True):
        plan={'id':9,'is_system':system,'is_active':True,'is_deleted':False,'tool_limits':{'music':{'limit':3,'period':'weekly'}}}
        controls=[b.callback_data for row in build_admin_plan_detail_keyboard(plan).inline_keyboard for b in row]
        for kind in ('music','portrait'):
            assert f'admin:plan:edit:{kind}:9' in controls
            assert f'admin:plan:toolperiod:{kind}:9' in controls


def test_general_authored_messages_and_start_templates_are_editable(monkeypatch):
    from app.localization import tr
    from app.i18n import translate
    from app.tool_texts import SOURCE_KEYS
    source='دسترسی ندارید.'
    monkeypatch.setattr(runtime_config, '_cache', {'fa':(9999999999, {'tool_texts':{
        SOURCE_KEYS[source]:'این بخش برای مدیر است.',
        'i18n_start_telegram_id':'شماره حساب شما: <code>{telegram_id}</code>'}})})
    token=ui_language.set('fa')
    try:
        assert tr(source)=='این بخش برای مدیر است.'
        assert translate('fa','start.telegram_id',telegram_id=42)=='شماره حساب شما: <code>42</code>'
    finally:ui_language.reset(token)
