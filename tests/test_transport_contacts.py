import asyncio
from xml.etree import ElementTree as ET

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.core.transport import TelegramTransport
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramContact
from xmpp_transport_telegram.xmpp.component import XmppComponent


class FakeRepository:
    def __init__(self):
        self.signatures = {}
        self.connected_sessions = []

    async def get_synced_roster_item_signature(self, xmpp_jid, item_jid):
        return self.signatures.get((xmpp_jid, item_jid))

    async def set_synced_roster_item_signature(
        self,
        xmpp_jid,
        item_jid,
        item_kind,
        sync_signature,
    ):
        self.signatures[(xmpp_jid, item_jid)] = sync_signature

    async def list_connected_telegram_sessions(self):
        return self.connected_sessions


class FakeSession:
    def __init__(self, session_data):
        self._session_data = session_data

    def save(self):
        return self._session_data


class FakeTelegramClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False
        self.disconnected = False
        self.session = FakeSession("stored-session")

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized


class FakeTelegramBackend:
    def __init__(self, contacts, authorized=True):
        self.contacts = contacts
        self.authorized = authorized
        self.clients = []

    def client_for_session(self, session_data=None):
        client = FakeTelegramClient(authorized=self.authorized)
        self.clients.append(client)
        return client

    async def list_contacts(self, client):
        return self.contacts


class FakeXmppClient:
    def __init__(self):
        self.operations = []

    async def send_transport_operation(self, operation, fields, groups=(), timeout=10):
        self.operations.append((operation, fields, groups))
        return "updated"


class FakeXmpp:
    def __init__(self):
        self.client = FakeXmppClient()


def test_ensure_telegram_contact_pushes_contact_into_telegram_circle():
    asyncio.run(_test_ensure_telegram_contact_pushes_contact_into_telegram_circle())


async def _test_ensure_telegram_contact_pushes_contact_into_telegram_circle():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    contact = TelegramContact(peer_id=100, title="Alice")

    await transport._ensure_telegram_contact("user@example.com", contact)
    await transport._ensure_telegram_contact("user@example.com", contact)

    assert transport.xmpp.client.operations == [
        (
            "add-roster-contact",
            {
                "owner_jid": "user@example.com",
                "contact_jid": "chat-100@telegram.example.com",
                "name": "Alice",
                "create_chat": "true",
            },
            ("Telegram",),
        )
    ]


def test_restart_sync_pushes_contacts_for_connected_sessions():
    asyncio.run(_test_restart_sync_pushes_contacts_for_connected_sessions())


async def _test_restart_sync_pushes_contacts_for_connected_sessions():
    settings = _settings()
    repository = FakeRepository()
    encrypted_session = SessionCipher(settings.session_encryption_key).encrypt("stored-session")
    repository.connected_sessions = [
        {
            "xmpp_jid": "user@example.com",
            "encrypted_session": encrypted_session,
        }
    ]
    transport = TelegramTransport(settings, repository)
    transport.xmpp = FakeXmpp()
    transport.telegram = FakeTelegramBackend(
        [
            TelegramContact(peer_id=100, title="Alice"),
            TelegramContact(peer_id=200, title="Bob"),
        ]
    )

    await transport._sync_connected_contacts_after_restart()

    assert transport.xmpp.client.operations == [
        (
            "add-roster-contact",
            {
                "owner_jid": "user@example.com",
                "contact_jid": "chat-100@telegram.example.com",
                "name": "Alice",
                "create_chat": "true",
            },
            ("Telegram",),
        ),
        (
            "add-roster-contact",
            {
                "owner_jid": "user@example.com",
                "contact_jid": "chat-200@telegram.example.com",
                "name": "Bob",
                "create_chat": "true",
            },
            ("Telegram",),
        ),
    ]


def test_restart_sync_skips_expired_telegram_session():
    asyncio.run(_test_restart_sync_skips_expired_telegram_session())


async def _test_restart_sync_skips_expired_telegram_session():
    settings = _settings()
    repository = FakeRepository()
    encrypted_session = SessionCipher(settings.session_encryption_key).encrypt("stored-session")
    repository.connected_sessions = [
        {
            "xmpp_jid": "user@example.com",
            "encrypted_session": encrypted_session,
        }
    ]
    transport = TelegramTransport(settings, repository)
    transport.xmpp = FakeXmpp()
    transport.telegram = FakeTelegramBackend(
        [TelegramContact(peer_id=100, title="Alice")],
        authorized=False,
    )

    await transport._sync_connected_contacts_after_restart()

    assert transport.xmpp.client.operations == []


def test_transport_query_xml_has_single_namespace_declaration():
    asyncio.run(_test_transport_query_xml_has_single_namespace_declaration())


async def _test_transport_query_xml_has_single_namespace_declaration():
    component = XmppComponent(_settings(), None)

    query = component.client._transport_query_element(
        "add-roster-contact",
        {
            "owner_jid": "user@example.com",
            "contact_jid": "chat-100@telegram.example.com",
            "name": "Alice",
        },
        ("Telegram",),
    )
    serialized = ET.tostring(query).decode("utf-8")

    assert serialized.count("urn:xabber:transport:telegram:1") == 1
    assert 'op="add-roster-contact"' in serialized


def _settings():
    return Settings(
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
        qr_storage_dir="data/login_qr",
        qr_base_url="http://127.0.0.1:8089",
        log_level="INFO",
        log_file="",
        log_max_bytes=10485760,
        log_backup_count=5,
    )
