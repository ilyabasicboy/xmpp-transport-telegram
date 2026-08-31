import asyncio

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.commands import CommandService
from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.core.transport import TelegramTransport
from xmpp_transport_telegram.runtime.config import Settings


class FakeRepository:
    def __init__(self):
        self.account_id = 1
        self.session = None

    async def ensure_xmpp_account(self, xmpp_jid):
        return self.account_id

    async def get_telegram_session(self, xmpp_account_id):
        return self.session

    async def list_connected_telegram_sessions(self):
        if self.session is None:
            return []
        return [
            {
                "xmpp_jid": "user@example.com",
                "encrypted_session": self.session["encrypted_session"],
            }
        ]

    async def get_synced_roster_item_signature(self, xmpp_jid, item_jid):
        return None

    async def set_synced_roster_item_signature(
        self,
        xmpp_jid,
        item_jid,
        item_kind,
        sync_signature,
    ):
        pass


class FakeTelegramClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False
        self.disconnected = False
        self.sent_messages = []
        self.handlers = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    def is_connected(self):
        return self.connected and not self.disconnected

    async def send_message(self, peer_id, body):
        self.sent_messages.append((peer_id, body))

    def add_event_handler(self, handler, event_builder):
        self.handlers.append((handler, event_builder))


class FakeTelegramBackend:
    def __init__(self):
        self.clients = []
        self.sent = []

    def client_for_session(self, session_data=None):
        client = FakeTelegramClient()
        self.clients.append(client)
        return client

    async def send_direct_message(self, client, peer_id, body):
        self.sent.append((peer_id, body))
        await client.send_message(peer_id, body)

    async def list_contacts(self, client):
        return []


class FakeXmppClient:
    def __init__(self):
        self.direct_messages = []

    def send_direct_message(self, to_jid, peer_id, body):
        self.direct_messages.append((to_jid, peer_id, body))


class FakeXmpp:
    def __init__(self):
        self.client = FakeXmppClient()


class FakeEvent:
    def __init__(
        self,
        chat_id=100,
        raw_text="hello",
        out=False,
        is_private=True,
        sender_id=None,
    ):
        self.chat_id = chat_id
        self.raw_text = raw_text
        self.out = out
        self.is_private = is_private
        self.sender_id = sender_id


class FakeSession:
    def save(self):
        return "saved-session"


class FakeUser:
    id = 42
    phone = None


def test_xmpp_direct_message_sends_to_telegram_peer():
    asyncio.run(_test_xmpp_direct_message_sends_to_telegram_peer())


async def _test_xmpp_direct_message_sends_to_telegram_peer():
    settings = _settings()
    cipher = SessionCipher(settings.session_encryption_key)
    repository = FakeRepository()
    repository.session = {
        "telegram_user_id": 42,
        "phone": None,
        "encrypted_session": cipher.encrypt("stored-session"),
        "connected": True,
    }
    transport = TelegramTransport(settings, repository)
    transport.telegram = FakeTelegramBackend()

    await transport.send_direct_message(
        "user@example.com",
        "chat-100@telegram.example.com",
        "hello telegram",
    )

    assert transport.telegram.sent == [(100, "hello telegram")]
    listener_client = transport.telegram.clients[0]
    assert len(listener_client.handlers) == 2
    assert listener_client.disconnected is False


def test_xmpp_direct_message_rejects_non_chat_contact_jid():
    asyncio.run(_test_xmpp_direct_message_rejects_non_chat_contact_jid())


async def _test_xmpp_direct_message_rejects_non_chat_contact_jid():
    transport = TelegramTransport(_settings(), FakeRepository())

    try:
        await transport.send_direct_message(
            "user@example.com",
            "bot@telegram.example.com",
            "hello telegram",
        )
    except ValueError as exc:
        assert str(exc) == "Unsupported Telegram contact JID."
    else:
        raise AssertionError("expected ValueError")


def test_incoming_telegram_direct_message_sends_to_xmpp_user():
    asyncio.run(_test_incoming_telegram_direct_message_sends_to_xmpp_user())


async def _test_incoming_telegram_direct_message_sends_to_xmpp_user():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="hello xabber"),
    )

    assert transport.xmpp.client.direct_messages == [
        ("user@example.com", 100, "hello xabber")
    ]


def test_incoming_telegram_direct_message_can_use_sender_id():
    asyncio.run(_test_incoming_telegram_direct_message_can_use_sender_id())


async def _test_incoming_telegram_direct_message_can_use_sender_id():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=None, sender_id=200, raw_text="hello from bot"),
    )

    assert transport.xmpp.client.direct_messages == [
        ("user@example.com", 200, "hello from bot")
    ]


def test_incoming_telegram_message_ignores_outgoing_group_and_empty_messages():
    asyncio.run(_test_incoming_telegram_message_ignores_outgoing_group_and_empty_messages())


async def _test_incoming_telegram_message_ignores_outgoing_group_and_empty_messages():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="outgoing", out=True),
    )
    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="group", is_private=False),
    )
    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="  "),
    )

    assert transport.xmpp.client.direct_messages == []


def test_command_login_callback_starts_telegram_listener():
    asyncio.run(_test_command_login_callback_starts_telegram_listener())


async def _test_command_login_callback_starts_telegram_listener():
    callback_calls = []

    async def connected_session(xmpp_jid, session_data, previous_owner):
        callback_calls.append((xmpp_jid, session_data, previous_owner))

    class Repository:
        async def upsert_telegram_session(
            self,
            xmpp_account_id,
            telegram_user_id,
            phone,
            encrypted_session,
            connected,
        ):
            return "old@example.com"

    service = CommandService(
        Repository(),
        None,
        SessionCipher(Fernet.generate_key().decode("ascii")),
        None,
        None,
        connected_session,
    )

    await service._notify_connected_session("new@example.com", "saved-session", "old@example.com")

    assert callback_calls == [
        ("new@example.com", "saved-session", "old@example.com")
    ]


class _ClientWithSession:
    session = FakeSession()


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
