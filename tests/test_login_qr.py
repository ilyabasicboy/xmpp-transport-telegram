import os

from xmpp_transport_telegram.core.qr_store import QrCodeStore


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
