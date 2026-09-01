from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


SENSITIVE_MARKERS = (
    "token",
    "secret",
    "password",
    "private_key",
    "seed",
    "receipt_file_id",
    "encrypted",
)


def sanitize_audit_details(value: Any) -> Any:
    """Recursively redact values whose keys may contain credentials."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}

        for raw_key, raw_value in value.items():
            key = str(raw_key)
            lowered_key = key.lower()

            if any(marker in lowered_key for marker in SENSITIVE_MARKERS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_audit_details(raw_value)

        return sanitized

    if isinstance(value, (list, tuple)):
        return [sanitize_audit_details(item) for item in value]

    return value


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def record(
        self,
        *,
        action: str,
        actor_user_id: int | None = None,
        actor_telegram_id: int | None = None,
        target_type: str | None = None,
        target_id: str | int | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            action=action,
            target_type=target_type,
            target_id=(str(target_id) if target_id is not None else None),
            details=sanitize_audit_details(details or {}),
            success=success,
        )
        self.session.add(audit_log)
        return audit_log
