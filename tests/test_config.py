from xmpp_transport_telegram.runtime.config import load_settings


def test_load_settings_defaults(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        """
[database]
url = postgresql://example

[security]
session_encryption_key = key

[xmpp]
component_password = secret

[telegram]
api_id = 123
api_hash = hash
""",
        encoding="utf-8",
    )

    settings = load_settings(str(config_path))

    assert settings.xmpp_component_jid == "telegram.example.com"
    assert settings.xmpp_component_port == 5238
    assert settings.telegram_api_id == 123
    assert settings.qr_storage_dir == "data/login_qr"
    assert settings.qr_base_url == "http://127.0.0.1:8089"
    assert settings.avatar_storage_dir == "data/avatars"
    assert settings.avatar_base_url == "http://127.0.0.1:8089"
    assert settings.avatar_max_bytes == 524288
    assert settings.avatar_unreferenced_ttl_days == 7
    assert settings.avatar_cleanup_interval_seconds == 86400
    assert settings.media_base_url == "http://127.0.0.1:8089"
    assert settings.media_stream_request_size == 524288
