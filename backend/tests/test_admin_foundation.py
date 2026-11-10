import os

import pytest
from cryptography.fernet import Fernet


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")


from app.core.config import settings  # noqa: E402
from app.core.language import (  # noqa: E402
    effective_language,
    infer_language,
    normalize_language,
)
from app.services.admin_access import AdminContext  # noqa: E402
from app.services.admin_management import (  # noqa: E402
    AdminManagementService,
    AdminRoleValidationError,
)
from app.services.application_settings import (  # noqa: E402
    ApplicationSettingsService,
    SettingEncryptionError,
    SettingValidationError,
)
from app.services.audit import sanitize_audit_details  # noqa: E402


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("fa", "fa"),
        ("fa-IR", "fa"),
        ("EN_us", "en"),
        ("de", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_language(raw_value, expected):
    assert normalize_language(raw_value) == expected


def test_effective_language_preserves_explicit_choice_and_defaults_to_persian():
    assert (
        effective_language(
            preferred_language="en",
            telegram_language_code="fa",
        )
        == "en"
    )
    assert infer_language("fa-IR") == "fa"
    assert infer_language("de-DE") == "fa"
    assert infer_language(None) == "fa"


def test_explicit_superadmin_wins_over_legacy_bootstrap(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "100, 200")
    monkeypatch.setattr(settings, "telegram_superadmin_id", 300)

    assert settings.telegram_admin_id_set == frozenset({100, 200, 300})
    assert settings.bootstrap_superadmin_id_set == frozenset({300})


def test_admin_context_superadmin_bypasses_individual_permissions():
    context = AdminContext(
        telegram_id=1,
        user_id=1,
        admin_account_id=1,
        is_admin=True,
        is_superadmin=True,
        roles=frozenset(),
        permissions=frozenset(),
    )

    assert context.has_permission("future.permission") is True


def test_sensitive_setting_round_trip(monkeypatch):
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "data_encryption_key", encryption_key)
    original = {
        "address": "TXyz",
        "label": "کیف پول اصلی",
        "confirmations": 20,
    }

    encrypted = ApplicationSettingsService.encrypt_value(original)

    assert "TXyz" not in encrypted
    assert ApplicationSettingsService.decrypt_value(encrypted) == original


def test_sensitive_setting_rejects_placeholder_key(monkeypatch):
    monkeypatch.setattr(settings, "data_encryption_key", "CHANGE_ME")

    with pytest.raises(SettingEncryptionError):
        ApplicationSettingsService.encrypt_value({"secret": "value"})


@pytest.mark.parametrize(
    "invalid_key",
    ["", "contains space", "../unsafe", "a"],
)
def test_setting_key_validation(invalid_key):
    with pytest.raises(SettingValidationError):
        ApplicationSettingsService.normalize_key(invalid_key)


def test_setting_key_is_normalized_to_lowercase():
    assert (
        ApplicationSettingsService.normalize_key("PAYMENT.Card-Number")
        == "payment.card-number"
    )


def test_audit_details_are_recursively_redacted():
    details = {
        "token": "secret-token",
        "nested": {
            "password": "secret-password",
            "safe": "visible",
        },
        "items": [{"private_key": "secret-key"}],
    }

    assert sanitize_audit_details(details) == {
        "token": "[REDACTED]",
        "nested": {
            "password": "[REDACTED]",
            "safe": "visible",
        },
        "items": [{"private_key": "[REDACTED]"}],
    }


@pytest.mark.parametrize(
    ("raw_code", "expected"),
    [
        ("Support_Lead", "support_lead"),
        ("finance.review", "finance.review"),
        ("ops-2", "ops-2"),
    ],
)
def test_role_code_normalization(raw_code, expected):
    assert AdminManagementService.normalize_role_code(raw_code) == expected


@pytest.mark.parametrize(
    "invalid_code",
    ["", "1admin", "contains space", "../unsafe", "a"],
)
def test_role_code_validation(invalid_code):
    with pytest.raises(AdminRoleValidationError):
        AdminManagementService.normalize_role_code(invalid_code)
