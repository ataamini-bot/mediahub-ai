import uuid


PENDING_SELECTIONS: dict[
    str,
    dict,
] = {}

PENDING_MEDIA_ENTRIES: dict[
    str,
    dict,
] = {}


def add_pending_selection(
    source_url: str,
    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ]
    | None = None,
    playlist_index: int | None = None,
    media_info: dict | None = None,
    telegram_id: int | None = None,
    source_job_id: int | None = None,
) -> str:

    if telegram_id is None:
        from app.middleware.interface import ui_actor
        telegram_id = ui_actor.get()

    token = (
        uuid.uuid4()
        .hex[:10]
    )

    sizes: dict[
        int,
        int | None,
    ] = {}

    if quality_options:

        for (
            height,
            file_size,
        ) in quality_options:

            sizes[
                height
            ] = (
                file_size
            )

    PENDING_SELECTIONS[
        token
    ] = {
        "telegram_id": telegram_id,
        "source_job_id": source_job_id,
        "source_url":
            source_url,

        "sizes":
            sizes,

        "playlist_index":
            playlist_index,

        # Keep the non-sensitive extraction result that powers the quality
        # menu.  The Bot uses it for FPS/duration in the delivered-file
        # caption and for the cover/description actions.  The source URL is
        # already present above; no user credentials are kept here.
        "media_info": (
            dict(
                media_info
            )
            if isinstance(
                media_info,
                dict,
            )
            else {}
        ),
    }

    if (
        len(
            PENDING_SELECTIONS
        )
        > 1000
    ):

        oldest_token = next(
            iter(
                PENDING_SELECTIONS
            )
        )

        PENDING_SELECTIONS.pop(
            oldest_token,
            None,
        )

    return token


# ============================================================
# Pending media entries
# ============================================================

def add_pending_media_entries(
    source_url: str,
    entries: list[dict],
) -> str:

    token = (
        uuid.uuid4()
        .hex[:10]
    )

    PENDING_MEDIA_ENTRIES[
        token
    ] = {
        "source_url":
            source_url,

        "entries":
            entries,
    }

    if (
        len(
            PENDING_MEDIA_ENTRIES
        )
        > 1000
    ):

        oldest_token = next(
            iter(
                PENDING_MEDIA_ENTRIES
            )
        )

        PENDING_MEDIA_ENTRIES.pop(
            oldest_token,
            None,
        )

    return token
