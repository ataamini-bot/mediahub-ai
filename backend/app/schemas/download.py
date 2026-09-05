from datetime import datetime
import re

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from app.models.download_job import (
    DownloadJobStatus,
)


# ============================================================
# Download create
# ============================================================

class DownloadCreate(
    BaseModel
):
    telegram_id: int = Field(
        gt=0,
    )

    source_url: str = Field(
        min_length=5,
        max_length=2048,
    )

    format_id: (
        str
        | None
    ) = None

    quality: (
        str
        | None
    ) = None

    media_type: (
        str
        | None
    ) = None

    estimated_size_bytes: (
        int
        | None
    ) = Field(
        default=None,
        ge=0,
    )

    # --------------------------------------------------------
    # Multi-video / playlist entry
    #
    # Single media:
    #   None
    #
    # Multi-video:
    #   1, 2, 3, ...
    # --------------------------------------------------------

    playlist_index: (
        int
        | None
    ) = Field(
        default=None,
        ge=1,
    )

    @field_validator(
        "source_url",
        mode="before",
    )
    @classmethod
    def normalize_source_url(
        cls,
        value: str,
    ) -> str:

        if not isinstance(
            value,
            str,
        ):
            return value

        value = (
            value.strip()
        )

        markdown_urls = re.findall(
            r"\]\((https?://[^)\s]+)\)",
            value,
        )

        if markdown_urls:

            return (
                markdown_urls[-1]
            )

        raw_urls = re.findall(
            r"https?://[^\s<>\[\]()]+",
            value,
        )

        if raw_urls:

            return (
                raw_urls[-1]
            )

        return value


class DownloadEntitlementResponse(BaseModel):
    plan_id: int | None
    plan_name: str
    forced_join_required: bool
    is_admin_bypass: bool


# ============================================================
# Download response
# ============================================================

class DownloadResponse(
    BaseModel
):
    id: int
    source_url: str

    plan_id: (
        int
        | None
    )

    plan_name_snapshot: (
        str
        | None
    )

    plan_limits_snapshot: (
        dict
        | None
    )

    format_id: (
        str
        | None
    )

    quality: (
        str
        | None
    )

    media_type: (
        str
        | None
    )

    playlist_index: (
        int
        | None
    )

    status: DownloadJobStatus
    progress: int

    downloaded_bytes: (
        int
        | None
    )

    total_bytes: (
        int
        | None
    )

    speed: (
        float
        | None
    )

    eta: (
        int
        | None
    )

    file_path: (
        str
        | None
    )

    file_size: (
        int
        | None
    )

    error_message: (
        str
        | None
    )

    started_at: (
        datetime
        | None
    )

    completed_at: (
        datetime
        | None
    )

    delivered_at: (
        datetime
        | None
    )

    paused_at: (
        datetime
        | None
    )

    cancelled_at: (
        datetime
        | None
    )

    expired_at: (
        datetime
        | None
    )

    celery_task_id: (
        str
        | None
    )

    model_config = {
        "from_attributes":
            True,
    }


# ============================================================
# Media format
# ============================================================

class MediaFormat(
    BaseModel
):
    format_id: str

    extension: (
        str
        | None
    ) = None

    resolution: (
        str
        | None
    ) = None

    filesize: (
        int
        | None
    ) = None

    has_video: bool = False
    has_audio: bool = False

    video_codec: (
        str
        | None
    ) = None

    audio_codec: (
        str
        | None
    ) = None


# ============================================================
# Media entry
#
# Used by:
# X multi-video posts
# Instagram carousel
# Other playlist/multi-media extractors
# ============================================================

class MediaEntry(
    BaseModel
):
    index: int = Field(
        ge=1,
    )

    id: (
        str
        | None
    ) = None

    title: (
        str
        | None
    ) = None

    duration: (
        int
        | None
    ) = None

    thumbnail: (
        str
        | None
    ) = None

    formats: list[
        MediaFormat
    ] = Field(
        default_factory=list
    )

    media_type: (
        str
        | None
    ) = None

    media_url: (
        str
        | None
    ) = None

    extension: (
        str
        | None
    ) = None

    width: (
        int
        | None
    ) = None

    height: (
        int
        | None
    ) = None


# ============================================================
# Media info response
# ============================================================

class MediaInfoResponse(
    BaseModel
):
    source_url: str

    title: (
        str
        | None
    ) = None

    duration: (
        int
        | None
    ) = None

    thumbnail: (
        str
        | None
    ) = None

    media_type: (
        str
        | None
    ) = None

    media_url: (
        str
        | None
    ) = None

    extension: (
        str
        | None
    ) = None

    width: (
        int
        | None
    ) = None

    height: (
        int
        | None
    ) = None

    # --------------------------------------------------------
    # Single-video formats
    # --------------------------------------------------------

    formats: list[
        MediaFormat
    ] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Multi-video information
    # --------------------------------------------------------

    is_playlist: bool = False

    entry_count: int = 0

    entries: list[
        MediaEntry
    ] = Field(
        default_factory=list
    )
