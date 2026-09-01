import asyncio

from cryptography.fernet import Fernet

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.backend import TelegramBackend


class FakeEntity:
    def __init__(self, user_id=None, first_name=None, last_name=None, username=None, phone=None, bot=False):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.phone = phone
        self.bot = bot


class FakeDialog:
    def __init__(self, dialog_id, name, entity, is_group=False, is_channel=False):
        self.id = dialog_id
        self.name = name
        self.entity = entity
        self.is_group = is_group
        self.is_channel = is_channel


class FakeClient:
    def __init__(self, dialogs, users=()):
        self.dialogs = dialogs
        self.users = users
        self.sent_messages = []

    async def __call__(self, request):
        return type("FakeContactsResult", (), {"users": list(self.users)})()

    async def iter_dialogs(self, limit=None):
        for dialog in self.dialogs:
            yield dialog

    async def send_message(self, entity, body, reply_to=None):
        self.sent_messages.append((entity, body, reply_to))
        return type("FakeSentMessage", (), {"id": 777})()


def test_list_contacts_merges_address_book_and_private_dialogs():
    asyncio.run(_test_list_contacts_merges_address_book_and_private_dialogs())


async def _test_list_contacts_merges_address_book_and_private_dialogs():
    backend = TelegramBackend(_settings())
    users = [
        FakeEntity(
            user_id=100,
            first_name="Alice",
            username="alice",
            phone="15551230000",
        ),
        FakeEntity(user_id=500, first_name="Charlie", username="charlie"),
    ]
    dialogs = [
        FakeDialog(300, "Group", FakeEntity(username="group"), is_group=True),
        FakeDialog(200, "Bot", FakeEntity(username="test_bot", bot=True)),
        FakeDialog(100, "Alice Dialog", FakeEntity(username="alice_dialog")),
        FakeDialog(400, "Channel", FakeEntity(username="channel"), is_channel=True),
    ]

    contacts = await backend.list_contacts(FakeClient(dialogs, users))

    assert [(contact.peer_id, contact.title, contact.username) for contact in contacts] == [
        (100, "Alice", "alice"),
        (200, "Bot", "test_bot"),
        (500, "Charlie", "charlie"),
    ]


def test_send_direct_message_resolves_private_dialog_entity():
    asyncio.run(_test_send_direct_message_resolves_private_dialog_entity())


async def _test_send_direct_message_resolves_private_dialog_entity():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])

    await backend.send_direct_message(client, 200, "hello bot")

    assert client.sent_messages == [(bot_entity, "hello bot", None)]


def test_send_direct_message_resolves_address_book_entity_first():
    asyncio.run(_test_send_direct_message_resolves_address_book_entity_first())


async def _test_send_direct_message_resolves_address_book_entity_first():
    backend = TelegramBackend(_settings())
    contact_entity = FakeEntity(user_id=100, first_name="Alice", username="alice")
    dialog_entity = FakeEntity(user_id=100, username="alice_dialog")
    client = FakeClient([FakeDialog(100, "Alice Dialog", dialog_entity)], [contact_entity])

    await backend.send_direct_message(client, 100, "hello alice")

    assert client.sent_messages == [(contact_entity, "hello alice", None)]


def test_send_direct_message_passes_reply_to_telegram():
    asyncio.run(_test_send_direct_message_passes_reply_to_telegram())


async def _test_send_direct_message_passes_reply_to_telegram():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])

    message_id = await backend.send_direct_message(client, 200, "hello bot", reply_to_message_id="123")

    assert message_id == "777"
    assert client.sent_messages == [(bot_entity, "hello bot", 123)]


def test_list_group_chats_returns_groups_and_channels():
    asyncio.run(_test_list_group_chats_returns_groups_and_channels())


async def _test_list_group_chats_returns_groups_and_channels():
    backend = TelegramBackend(_settings())
    dialogs = [
        FakeDialog(200, "Bot", FakeEntity(username="test_bot")),
        FakeDialog(-100500, "Team", FakeEntity(username="team"), is_group=True),
        FakeDialog(-100600, "News", FakeEntity(username="news"), is_channel=True),
    ]

    groups = await backend.list_group_chats(FakeClient(dialogs))

    assert [(group.peer_id, group.title, group.is_group, group.is_channel) for group in groups] == [
        (-100600, "News", False, True),
        (-100500, "Team", True, False),
    ]


def test_send_group_message_resolves_group_dialog_entity():
    asyncio.run(_test_send_group_message_resolves_group_dialog_entity())


def test_send_group_message_passes_reply_to_telegram():
    asyncio.run(_test_send_group_message_passes_reply_to_telegram())


async def _test_send_group_message_resolves_group_dialog_entity():
    backend = TelegramBackend(_settings())
    group_entity = FakeEntity(username="team")
    client = FakeClient(
        [
            FakeDialog(200, "Bot", FakeEntity(username="test_bot")),
            FakeDialog(-100500, "Team", group_entity, is_group=True),
        ]
    )

    await backend.send_group_message(client, -100500, "hello team")

    assert client.sent_messages == [(group_entity, "hello team", None)]


async def _test_send_group_message_passes_reply_to_telegram():
    backend = TelegramBackend(_settings())
    group_entity = FakeEntity(username="team")
    client = FakeClient([FakeDialog(-100500, "Team", group_entity, is_group=True)])

    message_id = await backend.send_group_message(client, -100500, "hello team", reply_to_message_id="321")

    assert message_id == "777"
    assert client.sent_messages == [(group_entity, "hello team", 321)]


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
