import configparser
from dataclasses import dataclass


DEFAULT_CONFIG_PATH = "config.ini"


def _load_config(path: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.read(path, encoding="utf-8")
    return config


def _default_server_domain(component_jid: str) -> str:
    parts = component_jid.split(".", 1)
    return parts[1] if len(parts) == 2 else component_jid


def _default_qr_base_url(health_host: str, health_port: int) -> str:
    host = "127.0.0.1" if health_host in {"", "0.0.0.0", "::"} else health_host
    return "http://%s:%s" % (host, health_port)


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_encryption_key: str
    xmpp_component_jid: str
    xmpp_component_secret: str
    xmpp_component_host: str
    xmpp_component_port: int
    xmpp_component_connect_timeout: int
    xmpp_component_retry_interval: int
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_storage_dir: str
    transport_server_domain: str
    transport_pid_file: str
    health_host: str
    health_port: int
    qr_storage_dir: str
    qr_base_url: str
    log_level: str
    log_file: str
    log_max_bytes: int
    log_backup_count: int


def load_settings(config_path: str = DEFAULT_CONFIG_PATH) -> Settings:
    config = _load_config(config_path)
    xmpp_component_jid = config.get("xmpp", "component_jid", fallback="telegram.example.com")
    health_host = config.get("server", "health_host", fallback="127.0.0.1")
    health_port = config.getint("server", "health_port", fallback=8089)
    qr_base_url = config.get(
        "server",
        "qr_base_url",
        fallback=_default_qr_base_url(health_host, health_port),
    )
    return Settings(
        database_url=config.get("database", "url", fallback=""),
        session_encryption_key=config.get("security", "session_encryption_key", fallback=""),
        xmpp_component_jid=xmpp_component_jid,
        xmpp_component_secret=config.get("xmpp", "component_password", fallback=""),
        xmpp_component_host=config.get("xmpp", "server_ip", fallback="127.0.0.1"),
        xmpp_component_port=config.getint("xmpp", "server_port", fallback=5238),
        xmpp_component_connect_timeout=config.getint("xmpp", "component_connect_timeout", fallback=20),
        xmpp_component_retry_interval=config.getint("xmpp", "component_retry_interval", fallback=10),
        telegram_api_id=config.getint("telegram", "api_id", fallback=0),
        telegram_api_hash=config.get("telegram", "api_hash", fallback=""),
        telegram_session_storage_dir=config.get(
            "telegram",
            "session_storage_dir",
            fallback="data/telegram_sessions",
        ),
        transport_server_domain=config.get(
            "transport",
            "server_domain",
            fallback=_default_server_domain(xmpp_component_jid),
        ),
        transport_pid_file=config.get(
            "transport",
            "pid_file",
            fallback="run/xmpp_transport_telegram.pid",
        ),
        health_host=health_host,
        health_port=health_port,
        qr_storage_dir=config.get("server", "qr_storage_dir", fallback="data/login_qr"),
        qr_base_url=qr_base_url.rstrip("/"),
        log_level=config.get("logging", "level", fallback="INFO"),
        log_file=config.get("logging", "file", fallback="logs/xmpp_transport_telegram.log"),
        log_max_bytes=config.getint("logging", "max_bytes", fallback=10485760),
        log_backup_count=config.getint("logging", "backup_count", fallback=5),
    )


def validate_settings(settings: Settings) -> None:
    missing = []
    if not settings.database_url:
        missing.append("database.url")
    if not settings.session_encryption_key:
        missing.append("security.session_encryption_key")
    if not settings.xmpp_component_jid:
        missing.append("xmpp.component_jid")
    if not settings.xmpp_component_secret:
        missing.append("xmpp.component_password")
    if not settings.telegram_api_id:
        missing.append("telegram.api_id")
    if not settings.telegram_api_hash:
        missing.append("telegram.api_hash")
    if missing:
        raise RuntimeError("Missing required settings: " + ", ".join(missing))
