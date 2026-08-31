import io
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import qrcode
import qrcode.image.svg


@dataclass(frozen=True)
class StoredQrImage:
    url: str
    name: str
    mime_type: str
    size: int
    width: Optional[int] = None
    height: Optional[int] = None


class QrCodeStore:
    MIME_TYPE = "image/svg+xml"

    def __init__(self, storage_dir: str, qr_url_prefix: str) -> None:
        self.storage_dir = Path(storage_dir)
        self.qr_url_prefix = qr_url_prefix.rstrip("/")

    def create(self, qr_link: str) -> StoredQrImage:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        content = self._svg_content(qr_link)
        for _attempt in range(10):
            filename = self._filename()
            path = self.storage_dir / filename
            try:
                with path.open("xb") as file:
                    file.write(content)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Could not allocate unique login QR filename")
        return StoredQrImage(
            url="%s/%s" % (self.qr_url_prefix, filename),
            name=filename,
            mime_type=self.MIME_TYPE,
            size=len(content),
        )

    @staticmethod
    def _svg_content(qr_link: str) -> bytes:
        image = qrcode.make(qr_link, image_factory=qrcode.image.svg.SvgImage)
        stream = io.BytesIO()
        image.save(stream)
        return QrCodeStore._with_white_background(stream.getvalue())

    @staticmethod
    def _with_white_background(content: bytes) -> bytes:
        svg_start = content.find(b"<svg")
        if svg_start < 0:
            return content
        tag_end = content.find(b">", svg_start)
        if tag_end < 0:
            return content
        background = b'<rect width="100%" height="100%" fill="#fff"/>'
        insert_at = tag_end + 1
        return content[:insert_at] + background + content[insert_at:]

    @staticmethod
    def _filename() -> str:
        return "telegram-login-qr-%s.svg" % secrets.token_urlsafe(24)

    @staticmethod
    def cleanup(storage_dir: str, max_age_seconds: int, now: Optional[float] = None) -> int:
        cutoff = (now if now is not None else time.time()) - max_age_seconds
        removed = 0
        for path in Path(storage_dir).glob("telegram-login-qr-*.svg"):
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        return removed
