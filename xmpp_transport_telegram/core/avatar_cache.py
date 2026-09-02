import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xmpp_transport_telegram.telegram.models import TelegramAvatar


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedAvatar:
    avatar_id: str
    url: str
    mime_type: str
    bytes_count: int
    content_hash: str


class AvatarCache:
    def __init__(self, storage_dir: str, base_url: str, max_bytes: int) -> None:
        self.storage_dir = Path(storage_dir)
        self.base_url = base_url.rstrip("/")
        self.max_bytes = max_bytes

    async def store(
        self,
        repository,
        *,
        owner_jid: str,
        contact_jid: str,
        peer_id: int,
        avatar: TelegramAvatar,
    ) -> Optional[CachedAvatar]:
        if not avatar.content:
            return None
        if len(avatar.content) > self.max_bytes:
            log.warning(
                "Skipping oversized Telegram avatar owner=%s contact=%s peer_id=%s photo_id=%s bytes=%s max=%s",
                owner_jid,
                contact_jid,
                peer_id,
                avatar.photo_id,
                len(avatar.content),
                self.max_bytes,
            )
            await repository.delete_contact_avatar(owner_jid, contact_jid, avatar.variant)
            return None
        content_hash = hashlib.sha256(avatar.content).hexdigest()
        filename = "%s.jpg" % content_hash
        path = self.storage_dir / filename
        self._write_once(path, avatar.content)
        bytes_count = len(avatar.content)
        await repository.upsert_avatar_file(
            content_hash=content_hash,
            relative_path=filename,
            mime_type=avatar.mime_type,
            bytes_count=bytes_count,
        )
        avatar_id = "telegram-%s-%s-%s" % (peer_id, avatar.photo_id, content_hash[:16])
        url = "%s/avatar/%s" % (self.base_url, filename)
        await repository.upsert_contact_avatar(
            owner_jid=owner_jid,
            contact_jid=contact_jid,
            peer_id=peer_id,
            photo_id=avatar.photo_id,
            variant=avatar.variant,
            content_hash=content_hash,
            avatar_id=avatar_id,
            url=url,
            mime_type=avatar.mime_type,
            bytes_count=bytes_count,
        )
        return CachedAvatar(
            avatar_id=avatar_id,
            url=url,
            mime_type=avatar.mime_type,
            bytes_count=bytes_count,
            content_hash=content_hash,
        )

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        tmp_path = path.with_name("%s.tmp.%s" % (path.name, os.getpid()))
        try:
            with tmp_path.open("xb") as handle:
                handle.write(content)
            os.replace(str(tmp_path), str(path))
        except FileExistsError:
            return
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    async def cleanup_unreferenced(self, repository, ttl_days: int) -> int:
        removed = 0
        files = await repository.list_unreferenced_avatar_files(ttl_days)
        for row in files:
            content_hash = row["content_hash"]
            relative_path = row["relative_path"]
            path = self.storage_dir / relative_path
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("Could not remove unreferenced Telegram avatar file %s", path, exc_info=True)
                continue
            await repository.delete_avatar_file(content_hash)
            removed += 1
        return removed
