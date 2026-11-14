from app.localization import tr as _tr, localized_collection as _localized_collection
import html
from dataclasses import dataclass
from typing import Literal


SettingKind = Literal["boolean", "integer", "timezone"]


@dataclass(frozen=True, slots=True)
class RuntimeSettingDefinition:
    key: str
    category: str
    label: str
    description: str
    kind: SettingKind
    minimum: int | None = None
    maximum: int | None = None

    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        return _tr(value) if name in {"label", "description"} else value


RUNTIME_SETTINGS: tuple[RuntimeSettingDefinition, ...] = (
    RuntimeSettingDefinition(
        key="bot.maintenance_mode",
        category="bot",
        label="حالت تعمیرات",
        description="توقف موقت شروع دانلود و خرید جدید",
        kind="boolean",
    ),
    RuntimeSettingDefinition(
        key="downloads.enabled",
        category="downloads",
        label="دانلود برای کاربران",
        description="اجازه بررسی لینک و ساخت دانلود جدید",
        kind="boolean",
    ),
    RuntimeSettingDefinition(
        key="payments.enabled",
        category="payments",
        label="خرید اشتراک",
        description="اجازه شروع خرید و ثبت رسید جدید",
        kind="boolean",
    ),
    RuntimeSettingDefinition(
        key="payments.receipt_max_size_mb",
        category="payments",
        label="حداکثر حجم رسید",
        description="حداکثر حجم تصویر یا PDF رسید به مگابایت",
        kind="integer",
        minimum=1,
        maximum=50,
    ),
    RuntimeSettingDefinition(
        key="quota.timezone",
        category="quota",
        label="منطقه زمانی سهمیه",
        description="مرز نیمه‌شب برای بازنشانی سهمیه روزانه",
        kind="timezone",
    ),
)


RUNTIME_SETTINGS_BY_KEY = {
    definition.key: definition for definition in RUNTIME_SETTINGS
}


def setting_value_text(definition: RuntimeSettingDefinition, value: object) -> str:
    if definition.kind == "boolean":
        return _tr("فعال ✅") if bool(value) else _tr("غیرفعال ⛔️")
    if definition.kind == "integer":
        try:
            return f"{int(value)}{_tr(' مگابایت')}"
        except (TypeError, ValueError):
            return _tr("نامعتبر ⚠️")
    return str(value or "—")


def runtime_settings_text(rows: list[dict]) -> str:
    rows_by_key = {str(row.get("key")): row for row in rows}
    lines = [
        _tr("⚙️ <b>تنظیمات ربات</b>"),
        "",
        _tr("تغییرها بلافاصله و بدون ویرایش فایل سرور اعمال می‌شوند."),
        "",
    ]

    for definition in RUNTIME_SETTINGS:
        row = rows_by_key.get(definition.key)
        if row is None:
            rendered = _tr("ثبت نشده ⚠️")
        else:
            rendered = setting_value_text(definition, row.get("value"))
        lines.append(
            f"• <b>{html.escape(definition.label)}:</b> "
            f"<code>{html.escape(rendered)}</code>"
        )

    return "\n".join(lines)


# Resolve static labels using the language of the current update.
RUNTIME_SETTINGS = _localized_collection(RUNTIME_SETTINGS)
