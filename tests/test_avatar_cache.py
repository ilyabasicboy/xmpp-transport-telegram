import asyncio
import hashlib
from types import SimpleNamespace

from aiohttp import web

from xmpp_transport_telegram.core.avatar_cache import AvatarCache
from xmpp_transport_telegram.runtime.web import avatar


class FakeRepository:
    def __init__(self):
        self.files = {}
        self.deleted_files = []
        self.touched_hashes = []
        self.unreferenced_files = []

    async def upsert_avatar_file(self, content_hash, relative_path, mime_type, bytes_count):
        self.files[content_hash] = {
            "relative_path": relative_path,
            "mime_type": mime_type,
            "bytes_count": bytes_count,
        }

    async def upsert_contact_avatar(self, **kwargs):
        pass

    async def delete_contact_avatar(self, *args, **kwargs):
        pass

    async def get_avatar_file(self, content_hash):
        self.touched_hashes.append(content_hash)
        return self.files.get(content_hash)

    async def list_unreferenced_avatar_files(self, ttl_days):
        return list(self.unreferenced_files)

    async def delete_avatar_file(self, content_hash):
        self.deleted_files.append(content_hash)
        self.files.pop(content_hash, None)


def test_cleanup_unreferenced_avatar_files_removes_disk_and_metadata(tmp_path):
    asyncio.run(_test_cleanup_unreferenced_avatar_files_removes_disk_and_metadata(tmp_path))


async def _test_cleanup_unreferenced_avatar_files_removes_disk_and_metadata(tmp_path):
    repository = FakeRepository()
    content_hash = "a" * 64
    path = tmp_path / ("%s.jpg" % content_hash)
    path.write_bytes(b"old-avatar")
    repository.unreferenced_files = [
        {
            "content_hash": content_hash,
            "relative_path": path.name,
        }
    ]
    cache = AvatarCache(str(tmp_path), "http://transport.example", 524288)

    removed = await cache.cleanup_unreferenced(repository, ttl_days=7)

    assert removed == 1
    assert not path.exists()
    assert repository.deleted_files == [content_hash]


def test_avatar_http_handler_serves_file_and_touches_metadata(tmp_path):
    asyncio.run(_test_avatar_http_handler_serves_file_and_touches_metadata(tmp_path))


async def _test_avatar_http_handler_serves_file_and_touches_metadata(tmp_path):
    repository = FakeRepository()
    content = b"avatar-content"
    content_hash = hashlib.sha256(content).hexdigest()
    filename = "%s.jpg" % content_hash
    (tmp_path / filename).write_bytes(content)
    repository.files[content_hash] = {
        "relative_path": filename,
        "mime_type": "image/jpeg",
        "bytes_count": len(content),
    }
    request = SimpleNamespace(
        match_info={"filename": filename},
        app={
            "repository": repository,
            "avatar_storage_dir": tmp_path,
        },
    )

    response = await avatar(request)

    assert isinstance(response, web.FileResponse)
    assert repository.touched_hashes == [content_hash]
