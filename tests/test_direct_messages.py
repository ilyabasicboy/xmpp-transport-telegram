import asyncio
from xml.etree import ElementTree as ET

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.commands import CommandService
from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.core.transport import TelegramTransport
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramDialog


class FakeRepository:
    def __init__(self):
        self.account_id = 1
        self.session = None
        self.signatures = {}

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
        return self.signatures.get((xmpp_jid, item_jid))

    async def set_synced_roster_item_signature(
        self,
        xmpp_jid,
        item_jid,
        item_kind,
        sync_signature,
    ):
        self.signatures[(xmpp_jid, item_jid)] = sync_signature


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
        self.group_sent = []
        self.groups = []

    def client_for_session(self, session_data=None):
        client = FakeTelegramClient()
        self.clients.append(client)
        return client

    async def send_direct_message(self, client, peer_id, body):
        self.sent.append((peer_id, body))
        await client.send_message(peer_id, body)

    async def list_contacts(self, client):
        return []

    async def list_group_chats(self, client):
        return self.groups

    async def send_group_message(self, client, peer_id, body):
        self.group_sent.append((peer_id, body))
        await client.send_message(peer_id, body)


class FakeXmppClient:
    def __init__(self):
        self.direct_messages = []
        self.group_messages = []
        self.created_groups = []
        self.updated_groups = []
        self.invites = []
        self.direct_invites = []
        self.bot_jid = "bot@telegram.example.com"
        self.invite_error = None

    def send_direct_message(self, to_jid, peer_id, body):
        self.direct_messages.append((to_jid, peer_id, body))

    def send_xabber_group_message(self, **kwargs):
        self.group_messages.append(kwargs)

    async def create_xabber_group(self, **kwargs):
        self.created_groups.append(kwargs)
        return "%s@example.com" % kwargs["localpart"]

    async def update_xabber_group_info(self, **kwargs):
        self.updated_groups.append(kwargs)

    async def invite_xabber_group_member(self, **kwargs):
        self.invites.append(kwargs)
        if self.invite_error is not None:
            raise self.invite_error

    def send_xabber_group_invite(self, **kwargs):
        self.direct_invites.append(kwargs)

    def parse_component_localpart(self, jid):
        suffix = "@telegram.example.com"
        if not jid.endswith(suffix):
            return None
        return jid[: -len(suffix)]


class FakeXmpp:
    def __init__(self):
        self.client = FakeXmppClient()


class FakeIqError(Exception):
    def __init__(self, xml):
        super().__init__("iq error")
        self.iq = type("FakeIq", (), {"xml": xml})()


class FakeEvent:
    def __init__(
        self,
        chat_id=100,
        raw_text="hello",
        out=False,
        is_private=True,
        is_group=False,
        is_channel=False,
        sender_id=None,
        message_id=900,
        title=None,
    ):
        self.chat_id = chat_id
        self.raw_text = raw_text
        self.out = out
        self.is_private = is_private
        self.is_group = is_group
        self.is_channel = is_channel
        self.sender_id = sender_id
        self.id = message_id
        self.title = title

    async def get_chat(self):
        return type("FakeChatEntity", (), {"title": self.title})()


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
    asyncio.run(_test_incoming_telegram_message_ignores_outgoing_private_and_empty_messages())


async def _test_incoming_telegram_message_ignores_outgoing_private_and_empty_messages():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="outgoing", out=True),
    )
    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="  "),
    )

    assert transport.xmpp.client.direct_messages == []
    assert transport.xmpp.client.group_messages == []


def test_incoming_telegram_group_message_sends_to_xabber_group():
    asyncio.run(_test_incoming_telegram_group_message_sends_to_xabber_group())


async def _test_incoming_telegram_group_message_sends_to_xabber_group():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="hello group",
            is_private=False,
            sender_id=200,
            message_id=901,
            title="Telegram Team",
        ),
    )

    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    assert transport.xmpp.client.created_groups[0]["localpart"] == (
        "telegramg-75736572406578616d706c652e636f6d--100500"
    )
    assert transport.xmpp.client.updated_groups == []
    assert transport.xmpp.client.invites[0] == {
        "owner_jid": "user@example.com",
        "actor_jid": "bot@telegram.example.com",
        "group_jid": group_jid,
        "member_jid": "user@example.com",
        "send": False,
        "reason": "Telegram group member",
    }
    assert transport.xmpp.client.invites[1] == {
        "owner_jid": "user@example.com",
        "actor_jid": "bot@telegram.example.com",
        "group_jid": group_jid,
        "member_jid": "chat-200@telegram.example.com",
        "send": False,
        "reason": "Telegram group member",
    }
    assert transport.xmpp.client.direct_invites[0] == {
        "from_jid": "bot@telegram.example.com",
        "to_jid": "user@example.com",
        "group_jid": group_jid,
        "reason": "Telegram group member",
    }
    assert transport.xmpp.client.group_messages == [
        {
            "sender": "chat-200@telegram.example.com",
            "group_jid": group_jid,
            "body": "hello group",
            "message_id": "901",
            "fake_outgoing": True,
        }
    ]


def test_outgoing_telegram_group_message_sends_to_xabber_group_as_owner():
    asyncio.run(_test_outgoing_telegram_group_message_sends_to_xabber_group_as_transport_member())


async def _test_outgoing_telegram_group_message_sends_to_xabber_group_as_transport_member():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="sent from telegram",
            out=True,
            is_private=False,
            sender_id=42,
            message_id=903,
            title="Telegram Team",
        ),
    )

    assert transport.xmpp.client.group_messages == [
        {
            "sender": "bot@telegram.example.com",
            "group_jid": "telegramg-75736572406578616d706c652e636f6d--100500@example.com",
            "body": "sent from telegram",
            "message_id": "903",
            "fake_outgoing": True,
        }
    ]


def test_outgoing_telegram_public_message_with_ambiguous_private_flag_syncs_to_xabber():
    asyncio.run(_test_outgoing_telegram_public_message_with_ambiguous_private_flag_syncs_to_xabber())


async def _test_outgoing_telegram_public_message_with_ambiguous_private_flag_syncs_to_xabber():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100600,
            raw_text="public post from telegram",
            out=True,
            is_private=None,
            is_channel=True,
            message_id=904,
            title="Telegram Public",
        ),
    )

    assert transport.xmpp.client.created_groups[0]["localpart"] == (
        "telegramg-75736572406578616d706c652e636f6d--100600"
    )
    assert transport.xmpp.client.invites[0]["member_jid"] == "user@example.com"
    assert transport.xmpp.client.invites[0]["send"] is False
    assert transport.xmpp.client.direct_invites[0]["to_jid"] == "user@example.com"
    assert transport.xmpp.client.group_messages == [
        {
            "sender": "bot@telegram.example.com",
            "group_jid": "telegramg-75736572406578616d706c652e636f6d--100600@example.com",
            "body": "public post from telegram",
            "message_id": "904",
            "fake_outgoing": True,
        }
    ]


def test_incoming_telegram_group_message_does_not_create_existing_xabber_group():
    asyncio.run(_test_incoming_telegram_group_message_does_not_create_existing_xabber_group())


async def _test_incoming_telegram_group_message_does_not_create_existing_xabber_group():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Team\nTrue\nFalse"
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="hello existing group",
            is_private=False,
            sender_id=200,
            message_id=902,
            title="Telegram Team",
        ),
    )

    assert transport.xmpp.client.created_groups == []
    assert transport.xmpp.client.updated_groups == []
    assert transport.xmpp.client.invites == [
        {
            "owner_jid": "user@example.com",
            "actor_jid": "bot@telegram.example.com",
            "group_jid": group_jid,
            "member_jid": "user@example.com",
            "send": False,
            "reason": "Telegram group member",
        },
        {
            "owner_jid": "user@example.com",
            "actor_jid": "bot@telegram.example.com",
            "group_jid": group_jid,
            "member_jid": "chat-200@telegram.example.com",
            "send": False,
            "reason": "Telegram group member",
        }
    ]
    assert transport.xmpp.client.direct_invites[0]["to_jid"] == "user@example.com"
    assert transport.xmpp.client.group_messages[0]["body"] == "hello existing group"


def test_existing_xabber_group_already_invited_owner_still_sends_direct_invite():
    asyncio.run(_test_existing_xabber_group_already_invited_owner_still_sends_direct_invite())


async def _test_existing_xabber_group_already_invited_owner_still_sends_direct_invite():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100600@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Public\nTrue\nFalse"
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    error_xml = _already_invited_error_xml()
    transport.xmpp.client.invite_error = FakeIqError(error_xml)

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100600,
            raw_text="public post",
            out=True,
            is_private=False,
            is_channel=True,
            message_id=905,
            title="Telegram Public",
        ),
    )

    assert transport.xmpp.client.created_groups == []
    assert transport.xmpp.client.direct_invites == [
        {
            "from_jid": "bot@telegram.example.com",
            "to_jid": "user@example.com",
            "group_jid": group_jid,
            "reason": "Telegram group member",
        }
    ]
    assert transport.xmpp.client.group_messages[0]["body"] == "public post"


def test_xabber_group_fanout_sends_to_telegram_group():
    asyncio.run(_test_xabber_group_fanout_sends_to_telegram_group())


async def _test_xabber_group_fanout_sends_to_telegram_group():
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
    transport.telegram.groups = [TelegramDialog(peer_id=-100500, title="Telegram Team", is_group=True)]
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"

    await transport.send_direct_message(
        group_jid,
        "bot@telegram.example.com",
        "hello telegram group",
        group_sender_jid="user@example.com",
    )

    assert transport.telegram.group_sent == [(-100500, "hello telegram group")]


def test_transport_group_fanout_copy_is_ignored():
    asyncio.run(_test_transport_group_fanout_copy_is_ignored())


async def _test_transport_group_fanout_copy_is_ignored():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.telegram = FakeTelegramBackend()
    transport.xmpp = FakeXmpp()

    await transport.send_direct_message(
        "telegramg-75736572406578616d706c652e636f6d--100500@example.com",
        "bot@telegram.example.com",
        "echo copy",
        group_sender_jid="bot@telegram.example.com",
    )

    assert transport.telegram.group_sent == []


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


def _already_invited_error_xml():
    iq = ET.Element("iq")
    error = ET.SubElement(iq, "error", {"code": "409", "type": "cancel"})
    ET.SubElement(error, "{urn:ietf:params:xml:ns:xmpp-stanzas}conflict")
    text = ET.SubElement(error, "{urn:ietf:params:xml:ns:xmpp-stanzas}text")
    text.text = "User was already invited"
    return iq
