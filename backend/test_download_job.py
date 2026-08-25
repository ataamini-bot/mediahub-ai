import asyncio

from app.db.session import AsyncSessionLocal
from app.repositories.download_job import DownloadJobRepository


async def main():
    async with AsyncSessionLocal() as session:
        repository = DownloadJobRepository(session)

        job = await repository.create(
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            format_id="18",
            quality="360p",
            media_type="video",
        )

        print("JOB_ID:", job.id)
        print("FORMAT_ID:", job.format_id)
        print("QUALITY:", job.quality)
        print("MEDIA_TYPE:", job.media_type)


asyncio.run(main())
