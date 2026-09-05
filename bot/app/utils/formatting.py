def normalize_quality_label(
    height: int,
) -> str:

    if (
        height >= 2160
    ):

        return "4K"

    if (
        height >= 1440
    ):

        return "2K"

    return (
        f"{height}p"
    )


def format_file_size(
    file_size: (
        int
        | float
        | None
    ),
) -> str | None:

    if (
        file_size is None
        or file_size <= 0
    ):

        return None

    size = float(
        file_size
    )

    if (
        size < 1024
    ):

        return (
            f"{size:.0f} B"
        )

    kb = (
        size
        / 1024
    )

    if (
        kb < 1024
    ):

        if kb >= 100:
            return f"{kb:.0f} KB"

        if kb >= 10:
            return f"{kb:.0f} KB"

        return f"{kb:.1f} KB"

    mb = (
        kb
        / 1024
    )

    if (
        mb < 1024
    ):

        if mb >= 100:
            return f"{mb:.0f} MB"

        if mb >= 10:
            return f"{mb:.0f} MB"

        return f"{mb:.1f} MB"

    gb = (
        mb
        / 1024
    )

    if gb >= 10:
        return f"{gb:.0f} GB"

    return (
        f"{gb:.1f} GB"
    )


def _gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Convert a Gregorian date to the Persian calendar without extra deps."""
    gy = year - 1600
    gm = month - 1
    gd = day - 1
    g_days = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    g_month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month > 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        g_days += 1
    g_days += sum(g_month_days[:gm]) + gd
    j_days = g_days - 79
    j_np = j_days // 12053
    j_days %= 12053
    jy = 979 + 33 * j_np + 4 * (j_days // 1461)
    j_days %= 1461
    if j_days >= 366:
        jy += (j_days - 1) // 365
        j_days = (j_days - 1) % 365
    if j_days < 186:
        jm = 1 + j_days // 31
        jd = 1 + j_days % 31
    else:
        jm = 7 + (j_days - 186) // 30
        jd = 1 + (j_days - 186) % 30
    return jy, jm, jd


def format_date_for_language(value: object, language: str = "fa") -> str:
    """Render dates in Jalali for Persian and Gregorian for English."""
    from datetime import datetime

    if not value:
        return "نامشخص" if language == "fa" else "Unknown"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if language == "fa":
            year, month, day = _gregorian_to_jalali(parsed.year, parsed.month, parsed.day)
            return f"{year:04d}/{month:02d}/{day:02d} {parsed:%H:%M}"
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)
