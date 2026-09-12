from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.schemas.experience import (
    BotConfigurationResponse,
    HomeButtonCreate,
    HomeButtonResponse,
    HomeButtonUpdate,
    RequiredChannelCreate,
    RequiredChannelResponse,
    RequiredChannelUpdate,
    SupportReplyCreate,
    SupportUserReplyCreate,
    SupportStatusUpdate,
    SupportAssignmentUpdate,
    SupportAdminMessageUpdate,
    SupportTicketCreate,
    SupportTicketPageResponse,
    SupportTicketResponse,
)
from app.services.admin_access import (
    AdminAccessDenied,
    AdminAccessService,
    PermissionCode,
)
from app.services.bot_experience import (
    BotExperienceConflict,
    BotExperienceError,
    BotExperienceNotFound,
    BotExperienceService,
    SupportTicketRecord,
)


router = APIRouter(
    tags=["bot-experience"],
    dependencies=[Depends(require_internal_api_key)],
)


def _error(exc: Exception) -> HTTPException:
    detail = {
        "code": getattr(exc, "code", "bot_experience_error"),
        "message": str(exc),
    }
    if isinstance(exc, AdminAccessDenied):
        return HTTPException(status_code=403, detail=detail)
    if isinstance(exc, BotExperienceNotFound):
        return HTTPException(status_code=404, detail=detail)
    if isinstance(exc, BotExperienceConflict):
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=422, detail=detail)


async def _actor(
    db: AsyncSession,
    telegram_id: int,
    permission: str,
):
    context = await AdminAccessService(db).require_permission(telegram_id, permission)
    if context.user_id is None:
        raise AdminAccessDenied("Administrator user is not registered")
    return context


def _serialize_ticket(
    record: SupportTicketRecord,
    *,
    recipients: list[int] | None = None,
) -> dict:
    return {
        "id": record.ticket.id,
        "category": record.ticket.category,
        "status": record.ticket.status,
        "created_at": record.ticket.created_at,
        "updated_at": record.ticket.updated_at,
        "closed_at": record.ticket.closed_at,
        "assigned_admin_user_id": record.ticket.assigned_admin_user_id,
        "assigned_at": record.ticket.assigned_at,
        "reopened_count": record.ticket.reopened_count,
        "plan_name": record.plan_name,
        "user": {
            "telegram_id": record.user.telegram_id,
            "username": record.user.username,
            "first_name": record.user.first_name,
            "last_name": record.user.last_name,
            "effective_language": (
                record.user.preferred_language
                or "fa"
            ),
        },
        "messages": [
            {
                "id": message.id,
                "sender_telegram_id": message.sender_telegram_id,
                "sender_kind": message.sender_kind,
                "body": message.body,
                "telegram_file_id": message.telegram_file_id,
                "file_type": message.file_type,
                "created_at": message.created_at,
            }
            for message in record.messages
        ],
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "actor_telegram_id": event.actor_telegram_id,
                "details": event.details,
                "created_at": event.created_at,
            }
            for event in record.events
        ],
        "recipients": recipients or [],
    }


@router.get("/bot/configuration", response_model=BotConfigurationResponse)
async def bot_configuration(
    language: str = Query(default="fa", pattern="^(fa|en)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await BotExperienceService(db).configuration(language)


@router.get("/admin/home-buttons", response_model=list[HomeButtonResponse])
async def list_home_buttons(
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> list:
    try:
        await _actor(db, actor_telegram_id, PermissionCode.SETTINGS_VIEW)
        rows = await BotExperienceService(db).list_home_buttons()
        return [BotExperienceService.serialize_home_button(row) for row in rows]
    except (AdminAccessDenied, BotExperienceError) as exc:
        raise _error(exc) from exc


@router.post("/admin/home-buttons", response_model=HomeButtonResponse, status_code=201)
async def create_home_button(
    data: HomeButtonCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, PermissionCode.SETTINGS_MANAGE)
        row = await BotExperienceService(db).create_home_button(
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            data=data.model_dump(exclude={"actor_telegram_id"}),
        )
        await db.commit()
        await db.refresh(row)
        return BotExperienceService.serialize_home_button(row)
    except (AdminAccessDenied, BotExperienceError, BotExperienceConflict) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.patch("/admin/home-buttons/{button_id}", response_model=HomeButtonResponse)
async def update_home_button(
    button_id: int,
    data: HomeButtonUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, PermissionCode.SETTINGS_MANAGE)
        row = await BotExperienceService(db).update_home_button(
            button_id=button_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            changes=data.model_dump(exclude={"actor_telegram_id"}, exclude_unset=True),
        )
        await db.commit()
        await db.refresh(row)
        return BotExperienceService.serialize_home_button(row)
    except (
        AdminAccessDenied,
        BotExperienceError,
        BotExperienceConflict,
        BotExperienceNotFound,
    ) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.delete("/admin/home-buttons/{button_id}", status_code=204)
async def delete_home_button(
    button_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        context = await _actor(db, actor_telegram_id, PermissionCode.SETTINGS_MANAGE)
        await BotExperienceService(db).delete_home_button(
            button_id=button_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return Response(status_code=204)
    except (AdminAccessDenied, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.get("/admin/required-channels", response_model=list[RequiredChannelResponse])
async def list_required_channels(
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> list:
    try:
        await _actor(db, actor_telegram_id, "forced_join.manage")
        rows = await BotExperienceService(db).list_channels()
        return [BotExperienceService.serialize_channel(row) for row in rows]
    except (AdminAccessDenied, BotExperienceError) as exc:
        raise _error(exc) from exc


@router.post(
    "/admin/required-channels",
    response_model=RequiredChannelResponse,
    status_code=201,
)
async def create_required_channel(
    data: RequiredChannelCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, "forced_join.manage")
        row = await BotExperienceService(db).create_channel(
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            data=data.model_dump(exclude={"actor_telegram_id"}),
        )
        await db.commit()
        await db.refresh(row)
        return BotExperienceService.serialize_channel(row)
    except (AdminAccessDenied, BotExperienceError, BotExperienceConflict) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.patch(
    "/admin/required-channels/{channel_id}",
    response_model=RequiredChannelResponse,
)
async def update_required_channel(
    channel_id: int,
    data: RequiredChannelUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, "forced_join.manage")
        row = await BotExperienceService(db).update_channel(
            channel_id=channel_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            changes=data.model_dump(exclude={"actor_telegram_id"}, exclude_unset=True),
        )
        await db.commit()
        await db.refresh(row)
        return BotExperienceService.serialize_channel(row)
    except (
        AdminAccessDenied,
        BotExperienceError,
        BotExperienceConflict,
        BotExperienceNotFound,
    ) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.delete("/admin/required-channels/{channel_id}", status_code=204)
async def delete_required_channel(
    channel_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        context = await _actor(db, actor_telegram_id, "forced_join.manage")
        await BotExperienceService(db).delete_channel(
            channel_id=channel_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return Response(status_code=204)
    except (AdminAccessDenied, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post("/support/tickets", response_model=SupportTicketResponse, status_code=201)
async def create_support_ticket(
    data: SupportTicketCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        service = BotExperienceService(db)
        record = await service.create_ticket(
            telegram_id=data.telegram_id,
            category=data.category,
            body=data.body,
            telegram_file_id=data.telegram_file_id,
            file_type=data.file_type,
        )
        recipients = await service.support_recipient_telegram_ids()
        await db.commit()
        return _serialize_ticket(record, recipients=recipients)
    except (BotExperienceError, BotExperienceNotFound, BotExperienceConflict) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.get("/support/tickets", response_model=SupportTicketPageResponse)
async def list_user_support_tickets(
    telegram_id: int = Query(gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=8, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await BotExperienceService(db).list_tickets(
        user_telegram_id=telegram_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_serialize_ticket(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.get("/support/tickets/{ticket_id}", response_model=SupportTicketResponse)
async def get_user_support_ticket(
    ticket_id: int,
    telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return _serialize_ticket(
            await BotExperienceService(db).get_ticket(
                ticket_id,
                user_telegram_id=telegram_id,
            )
        )
    except BotExperienceNotFound as exc:
        raise _error(exc) from exc


@router.post(
    "/support/tickets/{ticket_id}/reply",
    response_model=SupportTicketResponse,
)
async def user_reply_support_ticket(
    ticket_id: int,
    data: SupportUserReplyCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        record = await BotExperienceService(db).user_reply_ticket(
            ticket_id=ticket_id,
            telegram_id=data.telegram_id,
            body=data.body,
            telegram_file_id=data.telegram_file_id,
            file_type=data.file_type,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (BotExperienceError, BotExperienceConflict, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.get("/admin/support/tickets", response_model=SupportTicketPageResponse)
async def list_support_tickets(
    actor_telegram_id: int = Query(gt=0),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=8, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await _actor(db, actor_telegram_id, "tickets.view")
        result = await BotExperienceService(db).list_tickets(
            status=status,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [_serialize_ticket(record) for record in result.items],
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }
    except (AdminAccessDenied, BotExperienceError) as exc:
        raise _error(exc) from exc


@router.get("/admin/support/tickets/{ticket_id}", response_model=SupportTicketResponse)
async def get_support_ticket(
    ticket_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await _actor(db, actor_telegram_id, "tickets.view")
        return _serialize_ticket(await BotExperienceService(db).get_ticket(ticket_id))
    except (AdminAccessDenied, BotExperienceNotFound) as exc:
        raise _error(exc) from exc


@router.post(
    "/admin/support/tickets/{ticket_id}/reply",
    response_model=SupportTicketResponse,
)
async def reply_support_ticket(
    ticket_id: int,
    data: SupportReplyCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, "tickets.reply")
        record = await BotExperienceService(db).reply_ticket(
            ticket_id=ticket_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            body=data.body,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (
        AdminAccessDenied,
        BotExperienceError,
        BotExperienceConflict,
        BotExperienceNotFound,
    ) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post(
    "/admin/support/tickets/{ticket_id}/close",
    response_model=SupportTicketResponse,
)
async def close_support_ticket(
    ticket_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, actor_telegram_id, "tickets.manage")
        record = await BotExperienceService(db).close_ticket(
            ticket_id=ticket_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (AdminAccessDenied, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post(
    "/admin/support/tickets/{ticket_id}/status",
    response_model=SupportTicketResponse,
)
async def update_support_ticket_status(
    ticket_id: int,
    data: SupportStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, "tickets.manage")
        record = await BotExperienceService(db).set_ticket_status(
            ticket_id=ticket_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            status=data.status,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (AdminAccessDenied, BotExperienceError, BotExperienceConflict, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post(
    "/admin/support/tickets/{ticket_id}/assign",
    response_model=SupportTicketResponse,
)
async def assign_support_ticket(
    ticket_id: int,
    data: SupportAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, data.actor_telegram_id, "tickets.manage")
        record = await BotExperienceService(db).assign_ticket(
            ticket_id=ticket_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            assignee_telegram_id=data.assignee_telegram_id,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (AdminAccessDenied, BotExperienceError, BotExperienceConflict, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post(
    "/admin/support/tickets/{ticket_id}/reopen",
    response_model=SupportTicketResponse,
)
async def reopen_support_ticket(
    ticket_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        context = await _actor(db, actor_telegram_id, "tickets.manage")
        record = await BotExperienceService(db).reopen_ticket(
            ticket_id=ticket_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return _serialize_ticket(record)
    except (AdminAccessDenied, BotExperienceConflict, BotExperienceNotFound) as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.patch(
    "/admin/support/tickets/{ticket_id}/admin-message",
    response_model=SupportTicketResponse,
)
async def set_support_ticket_admin_message(
    ticket_id: int,
    data: SupportAdminMessageUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        record = await BotExperienceService(db).set_ticket_admin_message(
            ticket_id=ticket_id,
            admin_chat_id=data.admin_chat_id,
            admin_message_id=data.admin_message_id,
            admin_message_thread_id=data.admin_message_thread_id,
        )
        await db.commit()
        return _serialize_ticket(record)
    except BotExperienceNotFound as exc:
        await db.rollback()
        raise _error(exc) from exc
