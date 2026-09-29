"""Quote a code before creating payment instructions or reserving capacity."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers.customers import SupportFormInput
from app.handlers.operations import keyboard
from app.handlers.payments import _offer_details_text
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError, _payment_request
from app.state.payment import PaymentStates
from app.utils.coupons import coupon_error, normalize_code, tr

router = Router(name="coupon-checkout")
router.message.filter(F.chat.type == "private", SupportFormInput())
router.callback_query.filter(F.message.chat.type == "private")


async def show_offer(message, state):
    data = await state.get_data()
    if not data.get("offer"):
        await state.clear()
        await message.answer(tr("پلن را دوباره انتخاب کنید.", "Choose a plan again."))
        return
    await state.set_state(PaymentStates.confirming_offer)
    await message.answer(_offer_details_text(data["offer"], ui_language.get()), parse_mode="HTML",
        reply_markup=build_payment_offer_detail_keyboard(ui_language.get(), has_coupon=bool(data.get("coupon_code"))))


@router.callback_query(F.data == "payment:coupon:enter")
async def enter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("offer") or data.get("order_id"):
        await callback.answer(tr("قبل از ساخت سفارش، از صفحهٔ انتخاب پلن کد را وارد کنید.",
                                 "Enter a code from the plan page before creating an order."), show_alert=True)
        return
    await state.set_state(PaymentStates.waiting_for_coupon)
    await callback.message.answer(tr("🎟 کد تخفیف را بفرستید:", "🎟 Send your discount code:"),
        reply_markup=keyboard([[(tr("بازگشت", "Back"), "payment:coupon:cancel")]]))
    await callback.answer()


@router.callback_query(F.data.in_({"payment:coupon:cancel", "payment:coupon:remove"}))
async def cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("order_id"):
        await callback.answer(tr("سفارش ساخته‌شده قابل تغییر نیست؛ از گزینهٔ لغو سفارش استفاده کنید.",
                                 "Created orders cannot be edited. Use Cancel order."), show_alert=True)
        return
    if callback.data.endswith(":remove"):
        await state.update_data(offer=data.get("coupon_base_offer") or data.get("offer"), coupon_code=None)
    await show_offer(callback.message, state)
    await callback.answer()


@router.message(PaymentStates.waiting_for_coupon, F.text)
async def receive(message: Message, state: FSMContext):
    data = await state.get_data()
    offer = data.get("coupon_base_offer") or data.get("offer")
    if not offer or data.get("order_id"):
        await state.clear()
        await message.answer(tr("پلن را دوباره انتخاب کنید.", "Choose a plan again."))
        return
    try:
        code = normalize_code(message.text)
        snapshot = await _payment_request("POST", "/payments/coupons/quote", payload={
            "telegram_id": message.from_user.id, "offer_code": offer["code"],
            "currency": offer.get("currency", "IRT"), "coupon_code": code})
        await state.update_data(coupon_code=snapshot["code"], coupon_base_offer=offer,
                                offer={**offer, "price": snapshot["final_price"], "coupon": snapshot})
        await show_offer(message, state)
    except ValueError:
        await message.answer(coupon_error(BackendAPIError(status_code=422, detail={"code": "coupon_code_invalid"})))
    except BackendAPIError as exc:
        await message.answer(coupon_error(exc))
