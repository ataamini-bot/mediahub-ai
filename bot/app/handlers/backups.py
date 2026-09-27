import uuid
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.handlers.customers import tr, SupportFormInput
from app.handlers.operations import api, keyboard
from app.services.backend import BackendAPIError

router = Router(name="backups")
router.message.filter(F.chat.type == "private")
router.message.filter(SupportFormInput())
router.callback_query.filter(F.message.chat.type == "private")


class BackupStates(StatesGroup):
    value = State()
    confirm = State()


def label(key):
    return {"backup.enabled": tr("بکاپ روزانه", "Daily backup"),
        "backup.hour": tr("ساعت اجرا در منطقه زمانی سهمیه", "Hour in quota timezone"),
        "backup.daily": tr("تعداد نسخه روزانه", "Daily copies"),
        "backup.weekly": tr("تعداد نسخه هفتگی", "Weekly copies"),
        "backup.monthly": tr("تعداد نسخه ماهانه", "Monthly copies")}[key]


async def show(message, actor_id):
    result = await api("GET", "/admin/backups", actor_id)
    beat = result.get("heartbeat")
    healthy = False
    if beat:
        healthy = beat.get("healthy") and (datetime.now(timezone.utc) - datetime.fromisoformat(beat["checked_at"])).total_seconds() < 7200
    lines = [tr("💾 بکاپ و بازیابی", "💾 Backup and recovery"),
             tr("سرویس بکاپ: ", "Backup service: ") + ("✅" if healthy else "⚠️"),
             tr("در صف: ", "Queued: ") + str(result["queued"])]
    for row in result["items"][:5]:
        verified = "✅" if row.get("restore_verified_at") else "—"
        lines.append(f"\n{row['created_at'][:16]} UTC · {row['status']}\n<code>{row['id']}</code>\n{tr('آزمایش بازیابی', 'Restore drill')}: {verified}")
    lines.append("\n" + tr("بازیابی دیتابیس اصلی با دستور سرور و دو تأیید انجام می‌شود. کلید رمزگشایی و یک کپی بکاپ را خارج از سرور نگه دارید.",
                          "Production restore uses the server command with two confirmations. Keep the decryption key and a backup copy off-server."))
    rows = []
    if result["can_manage"]:
        rows.append([(tr("بکاپ همین حالا", "Back up now"), "backup:request")])
        rows.extend([[(f"{label(r['key'])}: {r['value']}", f"backup:edit:{r['key']}")] for r in result["settings"]])
    rows += [[(tr("تازه‌سازی", "Refresh"), "backup:open")], [(tr("پنل مدیریت", "Admin panel"), "admin:open")]]
    await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard(rows))


@router.callback_query(F.data == "backup:open")
async def open_backups(callback: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await show(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError:
        await callback.answer(tr("پنل بکاپ در دسترس نیست.", "Backup panel is unavailable."), show_alert=True)


@router.callback_query(F.data == "backup:request")
async def request_backup(callback: CallbackQuery, state: FSMContext):
    try:
        result = await api("GET", "/admin/backups", callback.from_user.id)
        if not result["can_manage"]:
            raise ValueError()
        await state.clear()
        await state.set_state(BackupStates.confirm)
        await state.update_data(backup_request_id=str(uuid.uuid4()))
        await callback.message.answer(tr("یک بکاپ رمزنگاری‌شده از دیتابیس و تنظیمات ساخته شود؟", "Create an encrypted database and configuration backup?"),
            reply_markup=keyboard([[(tr("تأیید", "Confirm"), "backup:confirm")], [(tr("انصراف", "Cancel"), "backup:open")]]))
        await callback.answer()
    except (BackendAPIError, ValueError):
        await callback.answer(tr("دسترسی ندارید یا سرویس در دسترس نیست.", "Access denied or service unavailable."), show_alert=True)


@router.callback_query(F.data.startswith("backup:edit:"))
async def edit(callback: CallbackQuery, state: FSMContext):
    try:
        result = await api("GET", "/admin/backups", callback.from_user.id)
        row = next(r for r in result["settings"] if r["key"] == callback.data.removeprefix("backup:edit:"))
        if not result["can_manage"]:
            raise ValueError()
        await state.clear()
        await state.set_state(BackupStates.value)
        await state.update_data(backup_setting=row)
        hint = "true / false" if row["key"] == "backup.enabled" else tr("عدد صحیح", "Integer")
        await callback.message.answer(label(row["key"]) + " — " + hint,
            reply_markup=keyboard([[(tr("انصراف", "Cancel"), "backup:open")]]))
        await callback.answer()
    except (BackendAPIError, ValueError, StopIteration):
        await callback.answer(tr("تنظیم قابل ویرایش نیست.", "Cannot edit this setting."), show_alert=True)


@router.message(BackupStates.value, F.text)
async def value(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = message.text.strip().lower().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        if data["backup_setting"]["key"] == "backup.enabled":
            if raw not in {"true", "false"}:
                raise ValueError()
            parsed = raw == "true"
        else:
            parsed = int(raw)
        await state.update_data(backup_value=parsed)
        await state.set_state(BackupStates.confirm)
        await message.answer(f"{label(data['backup_setting']['key'])}: {parsed}",
            reply_markup=keyboard([[(tr("تأیید نهایی", "Confirm"), "backup:confirm")], [(tr("انصراف", "Cancel"), "backup:open")]]))
    except ValueError:
        await message.answer(tr("مقدار معتبر وارد کنید.", "Enter a valid value."))


@router.callback_query(BackupStates.confirm, F.data == "backup:confirm")
async def confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        if data.get("backup_request_id"):
            await api("POST", "/admin/backups/requests", callback.from_user.id, request_id=data["backup_request_id"])
            text = tr("درخواست بکاپ در صف قرار گرفت؛ نتیجه را در همین پنل بررسی کنید.", "Backup queued; check this panel for the result.")
        else:
            row = data["backup_setting"]
            await api("PUT", f"/admin/backups/settings/{row['key']}", callback.from_user.id,
                      value=data["backup_value"], expected_version=row["version"])
            text = tr("تنظیم ثبت شد.", "Setting saved.")
        await state.clear()
        await callback.message.answer(text)
        await show(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError:
        await callback.answer(tr("ثبت نشد؛ مقدار و وضعیت فعلی تنظیم را بررسی کنید.", "Not saved; check the value and current setting."), show_alert=True)
