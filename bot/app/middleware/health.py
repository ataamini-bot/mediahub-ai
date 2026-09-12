"""Publish health only after a successful Telegram long poll."""
import logging
import time

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import GetUpdates

logger = logging.getLogger(__name__)


class PollHeartbeatMiddleware(BaseRequestMiddleware):
    def __init__(self, redis):
        self.redis = redis

    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            try:
                await self.redis.set("mediahub:bot:poll_heartbeat", str(time.time()), ex=600)
            except Exception as exc:
                logger.warning("Bot heartbeat unavailable: %s", type(exc).__name__)
        return result
