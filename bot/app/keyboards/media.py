from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


MEDIA_ENTRY_PAGE_SIZE = 10


def build_media_entry_keyboard(
    entries: list[dict],
    token: str,
    page: int = 0,
) -> InlineKeyboardMarkup:

    rows: list[
        list[
            InlineKeyboardButton
        ]
    ] = []

    total_entries = (
        len(
            entries
        )
    )

    total_pages = max(
        1,
        (
            total_entries
            + MEDIA_ENTRY_PAGE_SIZE
            - 1
        )
        // MEDIA_ENTRY_PAGE_SIZE,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start_index = (
        page
        * MEDIA_ENTRY_PAGE_SIZE
    )

    end_index = (
        start_index
        + MEDIA_ENTRY_PAGE_SIZE
    )

    page_entries = (
        entries[
            start_index:
            end_index
        ]
    )

    for entry in page_entries:

        if not isinstance(
            entry,
            dict,
        ):

            continue

        try:

            index = int(
                entry.get(
                    "index"
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        media_type = (
            str(
                entry.get(
                    "media_type"
                )
                or "video"
            )
            .strip()
            .lower()
        )

        if media_type == "image":

            button_text = (
                f"📷 عکس {index}"
            )

        else:

            # Backward compatible:
            # entries without media_type
            # are still treated as video.
            button_text = (
                f"🎬 ویدئو {index}"
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"media_entry:"
                        f"{token}:"
                        f"{index}"
                    ),
                )
            ]
        )

    # ========================================================
    # Pagination navigation
    # ========================================================

    if total_pages > 1:

        navigation_row: list[
            InlineKeyboardButton
        ] = []

        if page > 0:

            navigation_row.append(
                InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=(
                        f"media_page:"
                        f"{token}:"
                        f"{page - 1}"
                    ),
                )
            )

        navigation_row.append(
            InlineKeyboardButton(
                text=(
                    f"📄 "
                    f"{page + 1}"
                    f"/"
                    f"{total_pages}"
                ),
                callback_data=(
                    "media_page_info"
                ),
            )
        )

        if page < (
            total_pages - 1
        ):

            navigation_row.append(
                InlineKeyboardButton(
                    text="بعدی ➡️",
                    callback_data=(
                        f"media_page:"
                        f"{token}:"
                        f"{page + 1}"
                    ),
                )
            )

        rows.append(
            navigation_row
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
