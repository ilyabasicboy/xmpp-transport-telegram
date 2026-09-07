import asyncio
import os
import time

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.transport import TelegramTransport
from xmpp_transport_telegram.core.qr_store import QrCodeStore
from xmpp_transport_telegram.runtime.config import Settings


def test_qr_image_is_stored_svg_url(tmp_path):
    store = QrCodeStore(str(tmp_path), "https://transport.example/qr")

    image = store.create("tg://login?token=test-token")

    assert image.name.startswith("telegram-login-qr-")
    assert image.name.endswith(".svg")
    assert image.mime_type == "image/svg+xml"
    assert image.url == "https://transport.example/qr/%s" % image.name
    assert image.width is None
    assert image.height is None

    content = (tmp_path / image.name).read_bytes()
    assert content.startswith(b"<?xml")
    assert b"<svg" in content
    assert b'<rect width="100%" height="100%" fill="#fff"/>' in content
    assert image.size == len(content)


def test_cleanup_removes_old_qr_files_only(tmp_path):
    old_qr = tmp_path / "telegram-login-qr-old.svg"
    fresh_qr = tmp_path / "telegram-login-qr-fresh.svg"
    unrelated = tmp_path / "other.svg"
    old_qr.write_text("<svg/>", encoding="utf-8")
    fresh_qr.write_text("<svg/>", encoding="utf-8")
    unrelated.write_text("<svg/>", encoding="utf-8")
    os.utime(old_qr, (1000, 1000))
    os.utime(fresh_qr, (2000, 2000))
    os.utime(unrelated, (1000, 1000))

    removed = QrCodeStore.cleanup(str(tmp_path), max_age_seconds=500, now=2000)

    assert removed == 1
    assert not old_qr.exists()
    assert fresh_qr.exists()
    assert unrelated.exists()


def test_qr_cleanup_loop_removes_expired_files(tmp_path):
    asyncio.run(_test_qr_cleanup_loop_removes_expired_files(tmp_path))


async def _test_qr_cleanup_loop_removes_expired_files(tmp_path):
    old_qr = tmp_path / "telegram-login-qr-old.svg"
    fresh_qr = tmp_path / "telegram-login-qr-fresh.svg"
    old_qr.write_text("<svg/>", encoding="utf-8")
    fresh_qr.write_text("<svg/>", encoding="utf-8")
    now = time.time()
    os.utime(old_qr, (now - 7200, now - 7200))
    os.utime(fresh_qr, (now, now))

    settings = Settings(
        database_url="postgresql://example",
        session_encryption_key=Fernet.generate_key().decode("ascii"),
        xmpp_component_jid="telegram.example.com",
        xmpp_component_secret="secret",
        xmpp_component_host="127.0.0.1",
        xmpp_component_port=5238,
        xmpp_component_connect_timeout=20,
        xmpp_component_retry_interval=10,
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_storage_dir="data/telegram_sessions",
        transport_server_domain="example.com",
        transport_pid_file="run/xmpp_transport_telegram.pid",
        health_host="127.0.0.1",
        health_port=8089,
        qr_storage_dir=str(tmp_path),
        qr_base_url="http://127.0.0.1:8089",
        avatar_storage_dir="data/avatars",
        avatar_base_url="http://127.0.0.1:8089",
        avatar_max_bytes=524288,
        avatar_unreferenced_ttl_days=7,
        avatar_cleanup_interval_seconds=86400,
        media_base_url="http://127.0.0.1:8089",
        media_stream_request_size=524288,
        log_level="INFO",
        log_file="",
        log_max_bytes=10485760,
        log_backup_count=5,
        qr_max_age_seconds=3600,
        qr_cleanup_interval_seconds=3600,
    )
    transport = TelegramTransport(settings, repository=None)
    task = asyncio.create_task(transport._qr_cleanup_loop())
    try:
        await asyncio.sleep(0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert not old_qr.exists()
    assert fresh_qr.exists()
