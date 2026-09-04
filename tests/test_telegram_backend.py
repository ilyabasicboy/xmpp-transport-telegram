import asyncio

from cryptography.fernet import Fernet
from telethon.errors import WebpageMediaEmptyError

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.backend import TelegramBackend
from xmpp_transport_telegram.telegram.models import TelegramForwardReference
from xmpp_transport_telegram.xmpp.models import XmppOutgoingMedia


def test_media_client_does_not_receive_updates_and_uses_short_retries():
    client = TelegramBackend(_settings()).media_client_for_session()

    assert client._no_updates is True
    assert client._connection_retries == 1
    assert client._request_retries == 1
    assert client._timeout == 10


class FakeEntity:
    def __init__(
        self,
        user_id=None,
        first_name=None,
        last_name=None,
        username=None,
        phone=None,
        bot=False,
        photo=None,
    ):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.phone = phone
        self.bot = bot
        self.photo = photo


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
        self.sent_files = []
        self.forwarded_messages = []
        self.profile_photo_downloads = []
        self.fail_profile_photo_download = False
        self.fail_url_send_file = False

    async def __call__(self, request):
        return type("FakeContactsResult", (), {"users": list(self.users)})()

    async def iter_dialogs(self, limit=None):
        for dialog in self.dialogs:
            yield dialog

    async def send_message(self, entity, body, reply_to=None):
        self.sent_messages.append((entity, body, reply_to))
        return type("FakeSentMessage", (), {"id": 777})()

    async def send_file(self, entity, file, caption=None, reply_to=None, **kwargs):
        self.sent_files.append((entity, file, caption, reply_to))
        if self.fail_url_send_file and isinstance(file, str) and file.startswith(("http://", "https://")):
            raise WebpageMediaEmptyError(request=None)
        return type("FakeSentFileMessage", (), {"id": 778})()

    async def forward_messages(self, entity, message_id, from_peer=None):
        self.forwarded_messages.append((entity, message_id, from_peer))
        return type("FakeForwardedMessage", (), {"id": 888})()

    async def download_profile_photo(self, entity, file=None, download_big=True):
        self.profile_photo_downloads.append((entity, file, download_big))
        if self.fail_profile_photo_download:
            raise RuntimeError("download failed")
        return b"avatar"


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


def test_list_contacts_downloads_small_avatar_from_user_photo():
    asyncio.run(_test_list_contacts_downloads_small_avatar_from_user_photo())


async def _test_list_contacts_downloads_small_avatar_from_user_photo():
    backend = TelegramBackend(_settings())
    photo = type("FakePhoto", (), {"photo_id": 12345})()
    user = FakeEntity(user_id=100, first_name="Alice", photo=photo)
    client = FakeClient([], [user])

    contacts = await backend.list_contacts(client)

    assert contacts[0].avatar.photo_id == "12345"
    assert contacts[0].avatar.content == b"avatar"
    assert contacts[0].avatar.variant == "small"
    assert client.profile_photo_downloads == [(user, bytes, False)]


def test_list_contacts_marks_avatar_download_failure():
    asyncio.run(_test_list_contacts_marks_avatar_download_failure())


async def _test_list_contacts_marks_avatar_download_failure():
    backend = TelegramBackend(_settings())
    photo = type("FakePhoto", (), {"photo_id": 12345})()
    user = FakeEntity(user_id=100, first_name="Alice", photo=photo)
    client = FakeClient([], [user])
    client.fail_profile_photo_download = True

    contacts = await backend.list_contacts(client)

    assert contacts[0].avatar is None
    assert contacts[0].avatar_download_failed


def test_list_contacts_can_skip_avatar_downloads():
    asyncio.run(_test_list_contacts_can_skip_avatar_downloads())


async def _test_list_contacts_can_skip_avatar_downloads():
    backend = TelegramBackend(_settings())
    photo = type("FakePhoto", (), {"photo_id": 12345})()
    user = FakeEntity(user_id=100, first_name="Alice", photo=photo)
    client = FakeClient([], [user])

    contacts = await backend.list_contacts(client, include_avatars=False)

    assert contacts[0].avatar is None
    assert contacts[0].avatar_photo_id == "12345"
    assert not contacts[0].avatar_download_failed
    assert client.profile_photo_downloads == []


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


def test_send_direct_message_sends_media_url_with_caption():
    asyncio.run(_test_send_direct_message_sends_media_url_with_caption())


def test_send_direct_message_uploads_downloaded_media_when_telegram_rejects_url():
    asyncio.run(_test_send_direct_message_uploads_downloaded_media_when_telegram_rejects_url())


def test_send_direct_message_sends_link_when_media_download_fails():
    asyncio.run(_test_send_direct_message_sends_link_when_media_download_fails())


def test_send_direct_message_forwards_from_source_peer():
    asyncio.run(_test_send_direct_message_forwards_from_source_peer())


async def _test_send_direct_message_passes_reply_to_telegram():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])

    message_id = await backend.send_direct_message(client, 200, "hello bot", reply_to_message_id="123")

    assert message_id == "777"
    assert client.sent_messages == [(bot_entity, "hello bot", 123)]


async def _test_send_direct_message_sends_media_url_with_caption():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])

    message_id = await backend.send_direct_message(
        client,
        200,
        "caption",
        reply_to_message_id="123",
        media=(XmppOutgoingMedia(url="https://xabber.example/gallery/photo.jpg", mime_type="image/jpeg"),),
    )

    assert message_id == "778"
    assert client.sent_files == [(bot_entity, "https://xabber.example/gallery/photo.jpg", "caption", 123)]
    assert client.sent_messages == []


async def _test_send_direct_message_uploads_downloaded_media_when_telegram_rejects_url():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])
    client.fail_url_send_file = True

    async def download_media(media_items):
        assert [item.url for item in media_items] == ["https://xabber.example/gallery/photo.jpg"]
        return [
            {
                "path": "/tmp/photo.jpg",
                "mime_type": "image/jpeg",
                "file_size": 1234,
            }
        ]

    backend._download_outgoing_media_files = download_media

    message_id = await backend.send_direct_message(
        client,
        200,
        "caption",
        media=(XmppOutgoingMedia(url="https://xabber.example/gallery/photo.jpg", mime_type="image/jpeg"),),
    )

    assert message_id == "778"
    assert client.sent_files == [
        (bot_entity, "https://xabber.example/gallery/photo.jpg", "caption", None),
        (bot_entity, "/tmp/photo.jpg", "caption", None),
    ]
    assert client.sent_messages == []


async def _test_send_direct_message_sends_link_when_media_download_fails():
    backend = TelegramBackend(_settings())
    bot_entity = FakeEntity(user_id=200, username="test_bot", bot=True)
    client = FakeClient([FakeDialog(200, "Bot", bot_entity)])
    client.fail_url_send_file = True

    async def download_media(_media_items):
        raise RuntimeError("download failed")

    backend._download_outgoing_media_files = download_media

    message_id = await backend.send_direct_message(
        client,
        200,
        "caption",
        media=(XmppOutgoingMedia(url="https://xabber.example/gallery/photo.jpg", mime_type="image/jpeg"),),
    )

    assert message_id == "777"
    assert client.sent_messages == [(bot_entity, "caption\nhttps://xabber.example/gallery/photo.jpg", None)]


async def _test_send_direct_message_forwards_from_source_peer():
    backend = TelegramBackend(_settings())
    target_entity = FakeEntity(user_id=200, username="target_bot", bot=True)
    source_entity = FakeEntity(user_id=100, first_name="Alice")
    client = FakeClient([FakeDialog(200, "Target", target_entity)], [source_entity])

    message_id = await backend.send_direct_message(
        client,
        200,
        "comment",
        forward_reference=TelegramForwardReference(source_peer_id=100, message_id="456"),
    )

    assert message_id == "888"
    assert client.forwarded_messages == [(target_entity, 456, source_entity)]
    assert client.sent_messages == [(target_entity, "comment", None)]


def test_list_group_chats_returns_groups_and_channels():
    asyncio.run(_test_list_group_chats_returns_groups_and_channels())


def test_list_group_chats_downloads_small_avatar_from_group_photo():
    asyncio.run(_test_list_group_chats_downloads_small_avatar_from_group_photo())


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


async def _test_list_group_chats_downloads_small_avatar_from_group_photo():
    backend = TelegramBackend(_settings())
    photo = type("FakePhoto", (), {"photo_id": 777})()
    group_entity = FakeEntity(user_id=-100500, username="team", photo=photo)
    client = FakeClient([FakeDialog(-100500, "Team", group_entity, is_group=True)])

    groups = await backend.list_group_chats(client)

    assert groups[0].avatar.photo_id == "777"
    assert groups[0].avatar.content == b"avatar"
    assert groups[0].avatar_photo_id == "777"
    assert client.profile_photo_downloads == [(group_entity, bytes, False)]


def test_send_group_message_resolves_group_dialog_entity():
    asyncio.run(_test_send_group_message_resolves_group_dialog_entity())


def test_send_group_message_passes_reply_to_telegram():
    asyncio.run(_test_send_group_message_passes_reply_to_telegram())


def test_send_group_message_forwards_from_group_source_peer():
    asyncio.run(_test_send_group_message_forwards_from_group_source_peer())


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


async def _test_send_group_message_forwards_from_group_source_peer():
    backend = TelegramBackend(_settings())
    target_entity = FakeEntity(username="team")
    source_entity = FakeEntity(username="news")
    client = FakeClient(
        [
            FakeDialog(-100500, "Team", target_entity, is_group=True),
            FakeDialog(-100600, "News", source_entity, is_channel=True),
        ]
    )

    message_id = await backend.send_group_message(
        client,
        -100500,
        "",
        forward_reference=TelegramForwardReference(source_peer_id=-100600, message_id="457"),
    )

    assert message_id == "888"
    assert client.forwarded_messages == [(target_entity, 457, source_entity)]
    assert client.sent_messages == []


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
    )
