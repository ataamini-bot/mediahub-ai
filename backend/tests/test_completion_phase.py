"""Pure contract and boundary tests; the database flows have separate integration tests."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

for key, value in {'SECRET_KEY':'test', 'JWT_SECRET_KEY':'test', 'POSTGRES_DB':'test', 'POSTGRES_USER':'test', 'POSTGRES_PASSWORD':'test', 'DATABASE_URL':'postgresql+asyncpg://test:test@localhost/test', 'TELEGRAM_BOT_TOKEN':'123456789:test-token'}.items():
    os.environ.setdefault(key, value)

from app.schemas.payment import PaymentCreate
from app.services.payment import canonical_network, normalize_transaction_id, classify_plan_change
from app.services.operations import validate_operation, resolve_routes
from app.services.admin_statistics import _period_start
from app.api.reporting import period_bounds, csv_cell


def test_usdt_requires_txid_but_accepts_no_screenshot():
    base = dict(telegram_id=123, offer_code='test', currency='USDT', usdt_destination_id=1)
    with pytest.raises(ValueError): PaymentCreate(**base)
    payment = PaymentCreate(**base, txid='a' * 64)
    assert payment.receipt_file_id is None
    assert payment.receipt_file_type is None
    with pytest.raises(ValueError): PaymentCreate(**base, txid='a' * 64, receipt_file_id='file')
    with pytest.raises(ValueError): PaymentCreate(telegram_id=123, offer_code='test', currency='IRT')


def test_txid_normalization_preserves_base58_case_and_collapses_hex_aliases():
    assert canonical_network('trc20') == canonical_network('TRON') == 'TRON'
    assert canonical_network('ERC20') == canonical_network('ethereum')
    assert canonical_network('BEP-20') == canonical_network('bsc')
    assert normalize_transaction_id('0x' + 'AB' * 32) == normalize_transaction_id('ab' * 32)
    assert normalize_transaction_id('Abzy' * 20) != normalize_transaction_id('abzy' * 20)


def test_dynamic_plan_upgrade_downgrade_and_mixed_change():
    def plan(**overrides):
        values = dict(daily_download_limit=10, max_file_size_mb=300, max_quality=720,
            max_concurrent_downloads=1, priority_processing=False, forced_join_required=True)
        return SimpleNamespace(**(values | overrides))
    assert classify_plan_change(plan(), plan(daily_download_limit=50)) == 'upgrade'
    assert classify_plan_change(plan(daily_download_limit=50), plan()) == 'downgrade'
    assert classify_plan_change(plan(), plan(daily_download_limit=5, max_quality=2160)) == 'switch'
    assert classify_plan_change(plan(), plan(daily_download_limit=None)) == 'upgrade'


def test_six_statistics_periods_handle_calendar_boundaries():
    now = datetime(2026, 3, 31, 13, 5, tzinfo=ZoneInfo('Asia/Tehran'))
    assert _period_start(now, 'today') == now.replace(hour=0, minute=0)
    assert _period_start(now, '7d').day == 25
    assert _period_start(now, '1mo').date().isoformat() == '2026-02-28'
    assert _period_start(now, '3mo').date().isoformat() == '2025-12-31'
    assert _period_start(now, '6mo').date().isoformat() == '2025-09-30'
    assert _period_start(now, '1yr').date().isoformat() == '2025-03-31'
    for period in ['today', '7d', '1mo', '3mo', '6mo', '1yr']:
        lower, upper = period_bounds(period, None, None, 'Asia/Tehran', now)
        assert lower == _period_start(now, period).astimezone(timezone.utc)
        assert upper == now


def test_custom_finance_range_includes_whole_end_day_and_blocks_csv_formulas():
    from datetime import date
    lower, upper = period_bounds('custom', date(2026, 9, 1), date(2026, 9, 1), 'Asia/Tehran')
    assert lower.isoformat() == '2026-08-31T20:30:00+00:00'
    assert (upper - lower).days == 1
    assert csv_cell('=SUM(A1)') == "'=SUM(A1)"
    assert csv_cell('  +cmd') == "'  +cmd"
    assert csv_cell('Premium plan') == 'Premium plan'
    with pytest.raises(ValueError): period_bounds('custom', date(2026, 9, 2), date(2026, 9, 1), 'UTC')


def test_operations_thresholds_and_routing_fallback(monkeypatch):
    monkeypatch.setenv('ADMIN_NOTIFICATIONS_CHAT_ID', '-100123')
    monkeypatch.setenv('ADMIN_NOTIFICATIONS_SUPPORT_TOPIC_ID', '8')
    routes = resolve_routes({'notifications.chat_id': None, 'notifications.topic.support': 0})
    assert routes['chat_id'] == -100123
    assert routes['topics']['support'] is None
    assert validate_operation('monitor.cpu_percent', 90) == 90
    for key, value in [('monitor.cpu_percent', True), ('monitor.ram_percent', 101),
        ('notifications.chat_id', 123), ('notifications.topic.support', -5), ('notifications.enabled', 1)]:
        with pytest.raises(ValueError): validate_operation(key, value)
