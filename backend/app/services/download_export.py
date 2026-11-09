"""Build a private, per-customer workbook from the existing download journal."""
from datetime import datetime
from tempfile import TemporaryDirectory
from typing import BinaryIO
from zoneinfo import ZoneInfo

import anyio
from sqlalchemy import String, case, cast, func, literal, or_, select, union_all
from xlsxwriter import Workbook

from app.models.cover_request import CoverRequest
from app.models.download_job import DownloadJob

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEHRAN = ZoneInfo("Asia/Tehran")
MAX_DATA_ROWS = 1_048_575  # Excel's limit, excluding the header.
HEADERS = {
    "fa": ("شناسه درخواست", "شناسه تلگرام", "زمان درخواست (تهران)", "لینک دانلود", "وضعیت", "زمان تحویل (تهران)", "نوع رسانه", "کیفیت", "فرمت خروجی"),
    "en": ("Request ID", "Telegram ID", "Requested at (Tehran)", "Download link", "Status", "Delivered at (Tehran)", "Media type", "Quality", "Output format"),
}
STATUS_FA = {"pending": "در صف", "processing": "در حال پردازش", "paused": "متوقف", "completed": "تکمیل‌شده",
             "failed": "ناموفق", "cancelled": "لغوشده", "expired": "منقضی", "unconfirmed": "تحویل تأیید نشده"}
MEDIA_FA = {"video": "ویدئو", "audio": "صدا", "image": "تصویر", "cover": "کاور", "convert": "تبدیل"}


class DownloadWorkbook:
    def __init__(self, book, telegram_id, language):
        self.book, self.telegram_id, self.lang = book, str(telegram_id), language
        self.count = self.sheet_number = self.row = 0
        self.sheet = None
        self.header = book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#174A59", "valign": "vcenter"})
        self.date = book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})

    def end_sheet(self):
        if self.sheet is not None:
            self.sheet.autofilter(0, 0, self.row, len(HEADERS[self.lang]) - 1)

    def new_sheet(self):
        self.end_sheet()
        self.sheet_number += 1
        name = "دانلودها" if self.lang == "fa" else "Downloads"
        self.sheet = self.book.add_worksheet(name if self.sheet_number == 1 else f"{name} {self.sheet_number}")
        if self.lang == "fa":
            self.sheet.right_to_left()
        self.sheet.freeze_panes(1, 0)
        self.sheet.set_row(0, 28)
        for i, (title, width) in enumerate(zip(HEADERS[self.lang], (16, 23, 25, 78, 18, 25, 16, 14, 18))):
            self.sheet.set_column(i, i, width)
            self.sheet.write_string(0, i, title, self.header)
        self.row = 0

    def append(self, rows):
        for job_id, created, source, status, delivered, media, quality, fmt in rows:
            if self.sheet is None or self.row >= MAX_DATA_ROWS:
                self.new_sheet()
            self.row += 1
            self.count += 1
            fa = self.lang == "fa"
            values = (str(job_id), self.telegram_id, created, source,
                      STATUS_FA.get(status, status) if fa else status, delivered,
                      MEDIA_FA.get(media, media) if fa else media, quality,
                      fmt or ("اصلی" if fa else "original"))
            for i, value in enumerate(values):
                if isinstance(value, datetime):
                    self.sheet.write_datetime(self.row, i, value.astimezone(TEHRAN).replace(tzinfo=None), self.date)
                elif value is not None:
                    # Links are plain text. No formulas, remote relationships or 15-digit ID truncation.
                    self.sheet.write_string(self.row, i, str(value))


async def write_download_export(db, *, user_id: int, telegram_id: int, language: str, dest: BinaryIO) -> int:
    j, c = DownloadJob, CoverRequest
    jobs = select(j.id.label("sort_id"), cast(j.id, String).label("reference"), j.created_at, j.source_url,
                  func.lower(cast(j.status, String)).label("state"), j.delivered_at, j.media_type, j.quality, j.output_format
                 ).where(j.user_id == user_id, or_(j.source_url.ilike("https://%"), j.source_url.ilike("http://%")))
    covers = select(c.id, literal("C-") + cast(c.id, String), c.created_at, c.source_url,
                    case((c.delivered_at.is_not(None), "completed"), else_="unconfirmed"), c.delivered_at,
                    literal("cover"), literal(None, String), literal(None, String)).where(c.user_id == user_id)
    links = union_all(jobs, covers).subquery().c
    query = (select(links.reference, links.created_at, links.source_url, links.state, links.delivered_at,
                    links.media_type, links.quality, links.output_format)
             .order_by(links.created_at.desc(), links.sort_id.desc(), links.reference)
             .execution_options(yield_per=512))
    rows = await db.stream(query)
    try:
        # All writer scratch files live in one private directory, also removed on exceptions.
        with TemporaryDirectory(prefix="mediahub-xlsx-") as tmpdir:
            book = Workbook(dest, {"constant_memory": True, "tmpdir": tmpdir})
            report = DownloadWorkbook(book, telegram_id, language)
            try:
                async for batch in rows.partitions(512):
                    await anyio.to_thread.run_sync(report.append, batch)
                report.end_sheet()
            finally:
                await anyio.to_thread.run_sync(book.close)
        return report.count
    finally:
        await rows.close()
