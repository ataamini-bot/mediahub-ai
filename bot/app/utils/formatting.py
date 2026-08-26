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
