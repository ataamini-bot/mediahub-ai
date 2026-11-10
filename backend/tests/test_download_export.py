"""Exercise the workbook itself without Telegram or a database service."""
from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.services import download_export as export


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "fa"])
async def test_export_rolls_sheets_without_dropping_links(monkeypatch, lang):
    monkeypatch.setattr(export, "MAX_DATA_ROWS", 2)
    now = datetime(2026, 11, 8, 23, tzinfo=timezone.utc)
    uri = "https://site.test/?a=1&b=2"
    closed = []
    class Cursor:
        async def partitions(self, size):
            for ids in ([5, 4, 3], [2, 1]):
                yield [(i, now, uri, "completed", None, "audio", None, "mp3") for i in ids]
        async def close(self):
            closed.append(True)
    class Db:
        async def stream(self, query):
            return Cursor()
    output = BytesIO()
    target = 123456789012345678
    assert await export.write_download_export(Db(), user_id=4, telegram_id=target, language=lang, dest=output) == 5
    assert closed
    with ZipFile(output) as book:
        assert book.testzip() is None
        sheets = [ET.fromstring(book.read(f"xl/worksheets/sheet{i}.xml")) for i in (1, 2, 3)]
    rows = [sheet.findall(".//{*}sheetData/{*}row") for sheet in sheets]
    assert [len(r) for r in rows] == [3, 3, 2]
    assert [sheet.find(".//{*}autoFilter").get("ref") for sheet in sheets] == ["A1:I3", "A1:I3", "A1:I2"]
    assert ["".join(r[0].itertext()) for sheet in rows for r in sheet[1:]] == ["5", "4", "3", "2", "1"]
    assert ["".join(r[3].itertext()) for sheet in rows for r in sheet[1:]] == [uri] * 5
    # Real dates can be sorted, and long Telegram IDs retain every digit.
    assert rows[0][1][2].get("s") and rows[0][1][2].get("t") in (None, "n")
    assert rows[0][1][1].get("t") == "inlineStr" and "".join(rows[0][1][1].itertext()) == str(target)
