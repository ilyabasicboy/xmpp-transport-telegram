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
