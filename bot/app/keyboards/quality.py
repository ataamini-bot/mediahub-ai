from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.utils.formatting import (
    format_file_size,
    normalize_quality_label,
)


def build_quality_keyboard(
    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ],
    token: str,
    audio_token: str | None = None,
    cover_token: str | None = None,
    description_token: str | None = None,
    language: str = "fa",
) -> InlineKeyboardMarkup:

    buttons: list[
        list[
            InlineKeyboardButton
        ]
    ] = []

    for index in range(
        0,
        len(
            quality_options
        ),
        2,
    ):

        row: list[
            InlineKeyboardButton
        ] = []

        for (
            height,
            file_size,
        ) in quality_options[
            index:
            index + 2
        ]:

            quality_label = (
                normalize_quality_label(
                    height
                )
            )

            size_label = (
                format_file_size(
                    file_size
                )
            )

            if size_label:

                button_text = (
                    f"🎬 "
                    f"{quality_label}"
                    f" • ~"
                    f"{size_label}"
                )

            else:

                button_text = (
                    f"🎬 "
                    f"{quality_label}"
                )

            row.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"quality:"
                        f"{token}:"
                        f"{height}"
                    ),
                )
            )

        buttons.append(
            row
        )

    if audio_token:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    "🎵 Extract audio"
                    if language == "en"
                    else "🎵 استخراج صدا"
                ),
                callback_data=f"audio:open:{audio_token}",
            )
        ])

    media_actions: list[
        InlineKeyboardButton
    ] = []

    if cover_token:
        media_actions.append(
            InlineKeyboardButton(
                text=(
                    "🖼 Extract cover"
                    if language == "en"
                    else "🖼 استخراج کاور"
                ),
                callback_data=(
                    f"media:cover:"
                    f"{cover_token}"
                ),
            )
        )

    if description_token:
        media_actions.append(
            InlineKeyboardButton(
                text=(
                    "📝 Description"
                    if language == "en"
                    else "📝 توضیحات"
                ),
                callback_data=(
                    f"media:description:"
                    f"{description_token}"
                ),
            )
        )

    if media_actions:
        buttons.append(
            media_actions
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# Smaller quality keyboard
# ============================================================
