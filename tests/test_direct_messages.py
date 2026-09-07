import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.commands import CommandService
from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.core.state import DirectReplyContext
from xmpp_transport_telegram.core import transport as transport_module
from xmpp_transport_telegram.core.transport import TelegramTransport
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramAvatar, TelegramDialog
from xmpp_transport_telegram.xmpp.models import XmppForwardReference, XmppIncomingMessage, XmppOutgoingMedia


class FakeRepository:
    def __init__(self):
        self.account_id = 1
        self.session = None
        self.signatures = {}
        self.media_refs = {}
        self.avatar_files = {}
        self.contact_avatars = {}
        self.deleted_contact_avatars = []

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

    async def create_media_reference(
        self,
        token,
        owner_jid,
        peer_id,
        message_id,
        file_name,
        mime_type,
        bytes_count,
        width,
        height,
    ):
        self.media_refs[token] = {
            "owner_jid": owner_jid,
            "peer_id": peer_id,
            "message_id": message_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "bytes_count": bytes_count,
            "width": width,
            "height": height,
        }

    async def get_media_reference(self, token):
        return self.media_refs.get(token)

    async def upsert_avatar_file(self, content_hash, relative_path, mime_type, bytes_count):
        self.avatar_files[content_hash] = {
            "relative_path": relative_path,
            "mime_type": mime_type,
            "bytes_count": bytes_count,
        }

    async def upsert_contact_avatar(
        self,
        owner_jid,
        contact_jid,
        peer_id,
        photo_id,
        variant,
        content_hash,
        avatar_id,
        url,
        mime_type,
        bytes_count,
    ):
        self.contact_avatars[(owner_jid, contact_jid, variant)] = {
            "peer_id": peer_id,
            "photo_id": photo_id,
            "content_hash": content_hash,
            "avatar_id": avatar_id,
            "url": url,
            "mime_type": mime_type,
            "bytes_count": bytes_count,
        }

    async def delete_contact_avatar(self, owner_jid, contact_jid, variant="small"):
        self.deleted_contact_avatars.append((owner_jid, contact_jid, variant))
        self.contact_avatars.pop((owner_jid, contact_jid, variant), None)


class FakeTelegramClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False
        self.disconnected = False
        self.sent_messages = []
        self.handlers = []
        self.messages = {}
        self.downloaded_media = []
        self.profile_photo_downloads = []
        self.profile_photo_content = b"group-avatar"

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    def is_connected(self):
        return self.connected and not self.disconnected

    async def send_message(self, peer_id, body, reply_to=None):
        self.sent_messages.append((peer_id, body, reply_to))
        return type("FakeSentMessage", (), {"id": 777})()

    async def get_messages(self, entity, ids):
        return self.messages.get((entity, ids))

    def iter_download(self, media, request_size=524288, file_size=None):
        self.downloaded_media.append((media, request_size, file_size))

        async def chunks():
            for chunk in getattr(media, "chunks", (b"chunk",)):
                yield chunk

        return chunks()

    def add_event_handler(self, handler, event_builder):
        self.handlers.append((handler, event_builder))

    async def download_profile_photo(self, entity, file=None, download_big=True):
        self.profile_photo_downloads.append((entity, file, download_big))
        return self.profile_photo_content


class FakeTelegramBackend:
    def __init__(self):
        self.clients = []
        self.media_clients = []
        self.sent = []
        self.group_sent = []
        self.groups = []
        self.media = None

    def client_for_session(self, session_data=None):
        client = FakeTelegramClient()
        self.clients.append(client)
        return client

    def media_client_for_session(self, session_data=None):
        client = FakeTelegramClient()
        self.media_clients.append(client)
        return client

    async def send_direct_message(
        self,
        client,
        peer_id,
        body,
        reply_to_message_id=None,
        forward_reference=None,
        media=(),
    ):
        self.sent.append((peer_id, body, reply_to_message_id, forward_reference, media))
        sent = await client.send_message(peer_id, body, reply_to=reply_to_message_id)
        return str(sent.id)

    async def list_contacts(self, client):
        return []

    async def list_group_chats(self, client):
        return self.groups

    async def send_group_message(
        self,
        client,
        peer_id,
        body,
        reply_to_message_id=None,
        forward_reference=None,
        media=(),
    ):
        self.group_sent.append((peer_id, body, reply_to_message_id, forward_reference, media))
        sent = await client.send_message(peer_id, body, reply_to=reply_to_message_id)
        return str(sent.id)

    async def get_message_media(self, client, peer_id, message_id):
        if self.media is not None:
            return self.media
        message = await client.get_messages(peer_id, ids=int(message_id))
        if message is None or getattr(message, "media", None) is None:
            raise FileNotFoundError("missing media")
        return message.media

    def iter_media_download(self, client, media, request_size, file_size=None):
        return client.iter_download(media, request_size=request_size, file_size=file_size)


class FakeXmppClient:
    def __init__(self):
        self.direct_messages = []
        self.group_messages = []
        self.direct_media = []
        self.created_groups = []
        self.updated_groups = []
        self.invites = []
        self.direct_invites = []
        self.group_joins = []
        self.avatar_events = []
        self.bot_jid = "bot@telegram.example.com"
        self.invite_error = None
        self.group_avatar_error = None

    def send_direct_message(
        self,
        to_jid,
        peer_id,
        body,
        message_id=None,
        reply_reference=None,
        forward_references=(),
        media=(),
        fake_outgoing=False,
    ):
        self.direct_messages.append(
            (to_jid, peer_id, body, message_id, reply_reference, forward_references, fake_outgoing)
        )
        self.direct_media.append(media)

    def send_xabber_group_message(self, **kwargs):
        if kwargs.get("media") == ():
            kwargs = dict(kwargs)
            kwargs.pop("media")
        self.group_messages.append(kwargs)

    async def create_xabber_group(self, **kwargs):
        self.created_groups.append(kwargs)
        return "%s@example.com" % kwargs["localpart"]

    async def update_xabber_group_info(self, **kwargs):
        self.updated_groups.append(kwargs)

    async def update_xabber_group_avatar(self, **kwargs):
        if self.group_avatar_error is not None:
            raise self.group_avatar_error
        self.updated_groups.append(kwargs)

    async def invite_xabber_group_member(self, **kwargs):
        self.invites.append(kwargs)
        if self.invite_error is not None:
            raise self.invite_error

    def send_xabber_group_invite(self, **kwargs):
        self.direct_invites.append(kwargs)

    def join_xabber_group(self, **kwargs):
        self.group_joins.append(kwargs)

    def send_avatar_metadata_event(self, **kwargs):
        self.avatar_events.append(kwargs)

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
        sender_title=None,
        sender_first_name=None,
        sender_last_name=None,
        sender_username=None,
        reply_to_msg_id=None,
        fwd_from=None,
        media=None,
        file_info=None,
        photo=None,
        voice=None,
        date=None,
        chat_photo=None,
        action=None,
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
        self.sender_title = sender_title
        self.sender_first_name = sender_first_name
        self.sender_last_name = sender_last_name
        self.sender_username = sender_username
        self.date = date
        self.chat_photo = chat_photo
        self.message = type(
            "FakeEventMessage",
            (),
            {
                "id": message_id,
                "reply_to_msg_id": reply_to_msg_id,
                "fwd_from": fwd_from,
                "media": media,
                "file": file_info,
                "photo": photo,
                "voice": voice,
                "date": date,
                "action": action,
            },
        )()

    async def get_chat(self):
        return type("FakeChatEntity", (), {"id": self.chat_id, "title": self.title, "photo": self.chat_photo})()

    async def get_sender(self):
        return type(
            "FakeSenderEntity",
            (),
            {
                "title": self.sender_title,
                "first_name": self.sender_first_name,
                "last_name": self.sender_last_name,
                "username": self.sender_username,
            },
        )()


class FakeTelegramPeer:
    def __init__(self, user_id=None, chat_id=None, channel_id=None):
        self.user_id = user_id
        self.chat_id = chat_id
        self.channel_id = channel_id


class FakeStreamResponse:
    instances = []

    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.chunks = []
        self.prepared = False
        self.eof = False
        self.__class__.instances.append(self)

    async def prepare(self, request):
        self.prepared = True

    async def write(self, chunk):
        self.chunks.append(chunk)

    async def write_eof(self):
        self.eof = True


class DisconnectingStreamResponse(FakeStreamResponse):
    async def prepare(self, request):
        self.prepared = True
        raise ConnectionResetError("Cannot write to closing transport")


class FakeForwardHeader:
    def __init__(
        self,
        from_id=None,
        from_name=None,
        saved_from_peer=None,
        saved_from_msg_id=None,
        channel_post=None,
    ):
        self.from_id = from_id
        self.from_name = from_name
        self.saved_from_peer = saved_from_peer
        self.saved_from_msg_id = saved_from_msg_id
        self.channel_post = channel_post


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
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body="hello telegram",
            message_id="xmpp-1",
        )
    )

    assert transport.telegram.sent == [(100, "hello telegram", None, None, ())]
    listener_client = transport.telegram.clients[0]
    assert len(listener_client.handlers) == 1
    assert listener_client.disconnected is False


def test_xmpp_direct_message_rejects_non_chat_contact_jid():
    asyncio.run(_test_xmpp_direct_message_rejects_non_chat_contact_jid())


def test_xmpp_direct_reply_sends_telegram_reply_to_message_id():
    asyncio.run(_test_xmpp_direct_reply_sends_telegram_reply_to_message_id())


def test_xmpp_direct_reply_ignores_unknown_non_numeric_reply_id():
    asyncio.run(_test_xmpp_direct_reply_ignores_unknown_non_numeric_reply_id())


def test_xmpp_direct_reply_resolves_xabber_quote_fallback():
    asyncio.run(_test_xmpp_direct_reply_resolves_xabber_quote_fallback())


def test_xmpp_direct_forward_uses_telegram_native_forward():
    asyncio.run(_test_xmpp_direct_forward_uses_telegram_native_forward())


def test_xmpp_direct_media_reaches_telegram_backend():
    asyncio.run(_test_xmpp_direct_media_reaches_telegram_backend())


async def _test_xmpp_direct_reply_sends_telegram_reply_to_message_id():
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
    transport._remember_direct_reply_context(
        xmpp_jid="user@example.com",
        peer_id="100",
        context=DirectReplyContext(
            message_id="901",
            body="original",
            sender="chat-100@telegram.example.com",
            recipient="user@example.com",
        ),
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body="reply text",
            reply_to_message_ids=("901", "xabber-copy"),
            reply_to_message_id="901",
        )
    )

    assert transport.telegram.sent == [(100, "reply text", "901", None, ())]


async def _test_xmpp_direct_reply_ignores_unknown_non_numeric_reply_id():
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
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body="reply text",
            reply_to_message_id="a4d6aaf7-f8ee-42ae-84ee-ece96346093a",
        )
    )

    assert transport.telegram.sent == [(100, "reply text", None, None, ())]


async def _test_xmpp_direct_reply_resolves_xabber_quote_fallback():
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
    bot_text = (
        "Привет, этот бот пока что умеет только привязывать ваш аккаунт и присылать "
        "новые вакансии по фильтрам)\n\n"
        "Настроить фильтры можно на сайте\n\n"
        "Не получается привязать аккаунт по ссылке с сайта? (бывает, если переход в "
        "Telegram блокируется сетью) — отправьте команду /connect, и бот сам пришлёт "
        "ссылку для подтверждения привязки.\n\n"
        "А если нужна помощь, пишите в @hirify_support_bot"
    )
    transport._remember_direct_reply_context(
        xmpp_jid="user@example.com",
        peer_id="100",
        context=DirectReplyContext(
            message_id="902",
            body=bot_text,
            sender="chat-100@telegram.example.com",
            recipient="user@example.com",
        ),
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body=(
                "> Tuesday, September 1, 2026\n"
                "> [15:17:57] Hirify.me Bot:\n"
                "> Привет, этот бот пока что умеет только привязывать ваш аккаунт и присылать "
                "новые вакансии по фильтрам)\n"
                "> \n"
                "> Настроить фильтры можно на сайте\n"
                "> \n"
                "> Не получается привязать аккаунт по ссылке с сайта? (бывает, если переход в "
                "Telegram блокируется сетью) — отправьте команду /connect, и бот сам пришлёт "
                "ссылку для подтверждения привязки.\n"
                "> \n"
                "> А если нужна помощь, пишите в @hirify_support_bot\n"
                "test"
            ),
            reply_to_message_id="a4d6aaf7-f8ee-42ae-84ee-ece96346093a",
        )
    )

    assert transport.telegram.sent == [(100, "test", "902", None, ())]


async def _test_xmpp_direct_forward_uses_telegram_native_forward():
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
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body="forward comment",
            forward_references=(
                XmppForwardReference(
                    message_id="777",
                    body="forwarded text",
                    sender="chat-200@telegram.example.com",
                    recipient="user@example.com",
                ),
            ),
        )
    )

    peer_id, body, reply_to_message_id, forward_reference, media = transport.telegram.sent[0]
    assert (peer_id, body, reply_to_message_id) == (100, "forward comment", None)
    assert forward_reference.source_peer_id == 200
    assert forward_reference.message_id == "777"
    assert media == ()


def test_xmpp_direct_forward_uses_direct_alias_for_xabber_message_id():
    asyncio.run(_test_xmpp_direct_forward_uses_direct_alias_for_xabber_message_id())


async def _test_xmpp_direct_forward_uses_direct_alias_for_xabber_message_id():
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
    transport._remember_direct_reply_alias(
        xmpp_jid="user@example.com",
        peer_id="100",
        source_message_id="xabber-direct-1",
        target_message_id="901",
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-200@telegram.example.com",
            body="",
            forward_references=(
                XmppForwardReference(
                    message_id="xabber-direct-1",
                    body="?",
                    sender="user@example.com",
                    recipient="chat-100@telegram.example.com",
                ),
            ),
        )
    )

    peer_id, body, reply_to_message_id, forward_reference, media = transport.telegram.sent[0]
    assert (peer_id, body, reply_to_message_id) == (200, "", None)
    assert forward_reference.source_peer_id == 100
    assert forward_reference.message_id == "901"
    assert media == ()


def test_xmpp_direct_forward_uses_group_alias_without_sender_fallback_text():
    asyncio.run(_test_xmpp_direct_forward_uses_group_alias_without_sender_fallback_text())


async def _test_xmpp_direct_forward_uses_group_alias_without_sender_fallback_text():
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
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    transport._remember_group_reply_alias(
        xmpp_jid="user@example.com",
        peer_id="-100500",
        source_message_id="xabber-group-1",
        target_message_id="902",
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-200@telegram.example.com",
            body="",
            forward_references=(
                XmppForwardReference(
                    message_id="xabber-group-1",
                    body="еуые1",
                    sender="admin@example.com",
                    recipient=group_jid,
                ),
            ),
        )
    )

    peer_id, body, reply_to_message_id, forward_reference, media = transport.telegram.sent[0]
    assert (peer_id, body, reply_to_message_id) == (200, "", None)
    assert forward_reference.source_peer_id == -100500
    assert forward_reference.message_id == "902"
    assert media == ()


async def _test_xmpp_direct_media_reaches_telegram_backend():
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
    media = (XmppOutgoingMedia(url="https://xabber.example/gallery/photo.jpg", mime_type="image/jpeg"),)

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="user@example.com",
            recipient="chat-100@telegram.example.com",
            body="caption",
            media=media,
        )
    )

    assert transport.telegram.sent == [(100, "caption", None, None, media)]


async def _test_xmpp_direct_message_rejects_non_chat_contact_jid():
    transport = TelegramTransport(_settings(), FakeRepository())

    try:
        await transport.send_direct_message(
            XmppIncomingMessage(
                sender="user@example.com",
                recipient="bot@telegram.example.com",
                body="hello telegram",
            )
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
        ("user@example.com", 100, "hello xabber", "900", None, (), False)
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
        ("user@example.com", 200, "hello from bot", "900", None, (), False)
    ]


def test_incoming_telegram_direct_media_only_sends_file_reference():
    asyncio.run(_test_incoming_telegram_direct_media_only_sends_file_reference())


def test_incoming_telegram_direct_webpage_preview_does_not_create_media_reference():
    asyncio.run(_test_incoming_telegram_direct_webpage_preview_does_not_create_media_reference())


async def _test_incoming_telegram_direct_webpage_preview_does_not_create_media_reference():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()
    media = type("MessageMediaWebPage", (), {})()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=100,
            raw_text="https://example.com",
            media=media,
        ),
    )

    assert transport.repository.media_refs == {}
    assert transport.xmpp.client.direct_media == [()]
    assert transport.xmpp.client.direct_messages == [
        ("user@example.com", 100, "https://example.com", "900", None, (), False)
    ]


async def _test_incoming_telegram_direct_media_only_sends_file_reference():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    file_info = type(
        "FakeFileInfo",
        (),
        {
            "name": "photo.jpg",
            "mime_type": "image/jpeg",
            "size": 1234,
            "width": 640,
            "height": 480,
        },
    )()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="", message_id=901, media=object(), file_info=file_info, photo=object()),
    )

    assert len(repository.media_refs) == 1
    media = transport.xmpp.client.direct_media[0][0]
    assert media.name == "photo.jpg"
    assert media.mime_type == "image/jpeg"
    assert media.size == 1234
    assert media.width == 640
    assert media.height == 480
    assert media.url.startswith("http://127.0.0.1:8089/media/")
    assert transport.xmpp.client.direct_messages == [
        ("user@example.com", 100, "", "901", None, (), False)
    ]


def test_incoming_telegram_direct_voice_sends_xabber_voice_media():
    asyncio.run(_test_incoming_telegram_direct_voice_sends_xabber_voice_media())


async def _test_incoming_telegram_direct_voice_sends_xabber_voice_media():
    repository = FakeRepository()
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    file_info = type(
        "FakeFileInfo",
        (),
        {
            "name": None,
            "mime_type": "audio/ogg",
            "size": 4321,
            "width": None,
            "height": None,
            "duration": 3,
        },
    )()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="", message_id=901, media=object(), file_info=file_info, voice=object()),
    )

    media = transport.xmpp.client.direct_media[0][0]
    assert media.name == "telegram-901.webm"
    assert media.mime_type == "audio/webm;codecs=opus"
    assert media.size is None
    assert media.duration == 3
    assert media.voice


def test_stream_voice_media_converts_to_xabber_webm_without_content_length(monkeypatch):
    asyncio.run(_test_stream_voice_media_converts_to_xabber_webm_without_content_length(monkeypatch))


async def _test_stream_voice_media_converts_to_xabber_webm_without_content_length(monkeypatch):
    settings = _settings()
    cipher = SessionCipher(settings.session_encryption_key)
    repository = FakeRepository()
    repository.session = {
        "telegram_user_id": 42,
        "phone": None,
        "encrypted_session": cipher.encrypt("stored-session"),
        "connected": True,
    }
    repository.media_refs["token"] = {
        "owner_jid": "user@example.com",
        "peer_id": 100,
        "message_id": "901",
        "file_name": "telegram-901.webm",
        "mime_type": "audio/webm;codecs=opus",
        "bytes_count": 4321,
        "width": None,
        "height": None,
    }
    transport = TelegramTransport(settings, repository)
    telegram = FakeTelegramBackend()
    telegram.media = SimpleNamespace(chunks=(b"ogg"))
    transport.telegram = telegram

    async def stream_converted_voice(response, client, media, bytes_count):
        assert client is telegram.media_clients[0]
        assert media is telegram.media
        assert bytes_count == 4321
        await response.write(b"webm")

    transport._stream_converted_voice_media = stream_converted_voice
    FakeStreamResponse.instances = []
    monkeypatch.setattr(transport_module.web, "StreamResponse", FakeStreamResponse)
    request = SimpleNamespace(match_info={"token": "token"})

    response = await transport.stream_media(request)

    assert response.headers["Content-Type"] == "audio/webm;codecs=opus"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Type" in response.headers["Access-Control-Expose-Headers"]
    assert "Content-Length" not in response.headers
    assert response.chunks == [b"webm"]
    assert response.eof
    assert telegram.media_clients[0].disconnected


def test_stream_media_proxies_telegram_chunks(monkeypatch):
    asyncio.run(_test_stream_media_proxies_telegram_chunks(monkeypatch))


def test_stream_media_ignores_client_disconnect_during_prepare(monkeypatch):
    asyncio.run(_test_stream_media_ignores_client_disconnect_during_prepare(monkeypatch))


def test_stream_media_returns_404_for_stale_webpage_preview_reference(monkeypatch):
    asyncio.run(_test_stream_media_returns_404_for_stale_webpage_preview_reference(monkeypatch))


async def _test_stream_media_proxies_telegram_chunks(monkeypatch):
    settings = _settings()
    cipher = SessionCipher(settings.session_encryption_key)
    repository = FakeRepository()
    repository.session = {
        "telegram_user_id": 42,
        "phone": None,
        "encrypted_session": cipher.encrypt("stored-session"),
        "connected": True,
    }
    repository.media_refs["token"] = {
        "owner_jid": "user@example.com",
        "peer_id": 100,
        "message_id": "901",
        "file_name": "photo.jpg",
        "mime_type": "image/jpeg",
        "bytes_count": 7,
        "width": 640,
        "height": 480,
    }
    transport = TelegramTransport(settings, repository)
    telegram = FakeTelegramBackend()
    telegram.media = SimpleNamespace(chunks=(b"abc", b"defg"))
    transport.telegram = telegram
    FakeStreamResponse.instances = []
    monkeypatch.setattr(transport_module.web, "StreamResponse", FakeStreamResponse)
    request = SimpleNamespace(match_info={"token": "token"})

    response = await transport.stream_media(request)

    assert response.prepared
    assert response.eof
    assert response.chunks == [b"abc", b"defg"]
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["Content-Length"] == "7"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Disposition" in response.headers["Access-Control-Expose-Headers"]
    assert telegram.media_clients[0].disconnected
    assert telegram.clients == []


async def _test_stream_media_ignores_client_disconnect_during_prepare(monkeypatch):
    settings = _settings()
    cipher = SessionCipher(settings.session_encryption_key)
    repository = FakeRepository()
    repository.session = {
        "telegram_user_id": 42,
        "phone": None,
        "encrypted_session": cipher.encrypt("stored-session"),
        "connected": True,
    }
    repository.media_refs["token"] = {
        "owner_jid": "user@example.com",
        "peer_id": 100,
        "message_id": "901",
        "file_name": "photo.jpg",
        "mime_type": "image/jpeg",
        "bytes_count": 7,
        "width": 640,
        "height": 480,
    }
    transport = TelegramTransport(settings, repository)
    telegram = FakeTelegramBackend()
    telegram.media = SimpleNamespace(chunks=(b"abc",))
    transport.telegram = telegram
    DisconnectingStreamResponse.instances = []
    monkeypatch.setattr(transport_module.web, "StreamResponse", DisconnectingStreamResponse)
    request = SimpleNamespace(match_info={"token": "token"})

    response = await transport.stream_media(request)

    assert response.prepared
    assert response.chunks == []
    assert telegram.media_clients[0].disconnected
    assert telegram.clients == []


async def _test_stream_media_returns_404_for_stale_webpage_preview_reference(monkeypatch):
    settings = _settings()
    cipher = SessionCipher(settings.session_encryption_key)
    repository = FakeRepository()
    repository.session = {
        "telegram_user_id": 42,
        "phone": None,
        "encrypted_session": cipher.encrypt("stored-session"),
        "connected": True,
    }
    repository.media_refs["token"] = {
        "owner_jid": "user@example.com",
        "peer_id": 100,
        "message_id": "901",
        "file_name": "telegram-901.bin",
        "mime_type": "application/octet-stream",
        "bytes_count": None,
        "width": None,
        "height": None,
    }
    transport = TelegramTransport(settings, repository)
    telegram = FakeTelegramBackend()
    telegram.media = type("MessageMediaWebPage", (), {})()
    transport.telegram = telegram
    FakeStreamResponse.instances = []
    monkeypatch.setattr(transport_module.web, "StreamResponse", FakeStreamResponse)
    request = SimpleNamespace(match_info={"token": "token"})

    try:
        await transport.stream_media(request)
    except transport_module.web.HTTPNotFound:
        pass
    else:
        raise AssertionError("expected HTTPNotFound")

    assert FakeStreamResponse.instances == []
    assert telegram.media_clients[0].disconnected
    assert telegram.clients == []


def test_incoming_telegram_direct_reply_sends_xabber_reply_reference():
    asyncio.run(_test_incoming_telegram_direct_reply_sends_xabber_reply_reference())


def test_outgoing_telegram_direct_self_reply_is_ignored():
    asyncio.run(_test_outgoing_telegram_direct_self_reply_is_ignored())


def test_incoming_telegram_direct_forward_sends_xabber_forward_reference():
    asyncio.run(_test_incoming_telegram_direct_forward_sends_xabber_forward_reference())


async def _test_incoming_telegram_direct_reply_sends_xabber_reply_reference():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()
    transport._remember_direct_reply_context(
        xmpp_jid="user@example.com",
        peer_id="100",
        context=DirectReplyContext(
            message_id="900",
            body="original",
            sender="chat-100@telegram.example.com",
            recipient="user@example.com",
        ),
    )

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="reply", message_id=901, reply_to_msg_id=900),
    )

    assert len(transport.xmpp.client.direct_messages) == 1
    to_jid, peer_id, body, message_id, reply_reference, forward_references, fake_outgoing = (
        transport.xmpp.client.direct_messages[0]
    )
    assert (to_jid, peer_id, body, message_id) == ("user@example.com", 100, "reply", "901")
    assert forward_references == ()
    assert fake_outgoing is False
    assert reply_reference is not None
    assert reply_reference.message_id == "900"
    assert reply_reference.body == "original"


async def _test_outgoing_telegram_direct_self_reply_is_ignored():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()
    transport._remember_direct_reply_context(
        xmpp_jid="user@example.com",
        peer_id="100",
        context=DirectReplyContext(
            message_id="900",
            body="incoming",
            sender="chat-100@telegram.example.com",
            recipient="user@example.com",
        ),
    )

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(chat_id=100, raw_text="self reply", out=True, message_id=901, reply_to_msg_id=900),
    )

    assert transport.xmpp.client.direct_messages == []


async def _test_incoming_telegram_direct_forward_sends_xabber_forward_reference():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=100,
            raw_text="forwarded direct text",
            message_id=920,
            fwd_from=FakeForwardHeader(
                from_id=FakeTelegramPeer(user_id=200),
                saved_from_msg_id=777,
            ),
        ),
    )

    assert len(transport.xmpp.client.direct_messages) == 1
    to_jid, peer_id, body, message_id, reply_reference, forward_references, fake_outgoing = (
        transport.xmpp.client.direct_messages[0]
    )
    assert (to_jid, peer_id, body, message_id, reply_reference, fake_outgoing) == (
        "user@example.com",
        100,
        "",
        "920",
        None,
        False,
    )
    assert len(forward_references) == 1
    assert forward_references[0].message_id == "777"
    assert forward_references[0].body == "forwarded direct text"
    assert forward_references[0].sender == "chat-200@telegram.example.com"


def test_incoming_telegram_message_ignores_outgoing_private_and_empty_messages():
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


def test_incoming_telegram_group_message_syncs_group_avatar(tmp_path):
    asyncio.run(_test_incoming_telegram_group_message_syncs_group_avatar(tmp_path))


def test_telegram_group_avatar_change_event_updates_xabber_group_avatar(tmp_path):
    asyncio.run(_test_telegram_group_avatar_change_event_updates_xabber_group_avatar(tmp_path))


def test_group_avatar_update_failure_does_not_block_incoming_group_message(tmp_path):
    asyncio.run(_test_group_avatar_update_failure_does_not_block_incoming_group_message(tmp_path))


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
            sender_first_name="Alice",
            sender_last_name="Smith",
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
        "timeout": 2,
    }
    assert transport.xmpp.client.invites[1] == {
        "owner_jid": "user@example.com",
        "actor_jid": "bot@telegram.example.com",
        "group_jid": group_jid,
        "member_jid": "chat-200@telegram.example.com",
        "send": False,
        "reason": "Telegram group member",
        "timeout": 2,
    }
    assert transport.xmpp.client.direct_invites[0] == {
        "from_jid": "bot@telegram.example.com",
        "to_jid": "user@example.com",
        "group_jid": group_jid,
        "reason": "Telegram group member",
    }
    assert transport.xmpp.client.group_joins == [
        {
            "member_jid": "chat-200@telegram.example.com",
            "group_jid": group_jid,
            "nickname": "Alice Smith",
        }
    ]
    assert transport.xmpp.client.group_messages == [
        {
            "sender": "chat-200@telegram.example.com",
            "group_jid": group_jid,
            "body": "hello group",
            "message_id": "901",
            "reply_reference": None,
            "forward_references": (),
            "fake_outgoing": True,
        }
    ]


async def _test_incoming_telegram_group_message_syncs_group_avatar(tmp_path):
    settings = _settings()
    settings = Settings(
        **{
            **settings.__dict__,
            "avatar_storage_dir": str(tmp_path / "avatars"),
            "avatar_base_url": "http://transport.example",
        }
    )
    repository = FakeRepository()
    transport = TelegramTransport(settings, repository)
    transport.xmpp = FakeXmpp()
    client = FakeTelegramClient()
    transport._telegram_clients["user@example.com"] = client
    chat_photo = type("FakePhoto", (), {"photo_id": 777})()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="hello xabber group",
            is_private=False,
            sender_id=200,
            message_id=902,
            title="Telegram Team",
            chat_photo=chat_photo,
        ),
    )

    assert client.profile_photo_downloads[0][1:] == (bytes, False)
    assert transport.xmpp.client.updated_groups == [
        {
            "owner_jid": "user@example.com",
            "actor_jid": "bot@telegram.example.com",
            "group_jid": group_jid,
            "avatar_id": repository.contact_avatars[
                ("user@example.com", group_jid, "small")
            ]["avatar_id"],
            "url": repository.contact_avatars[
                ("user@example.com", group_jid, "small")
            ]["url"],
            "mime_type": "image/jpeg",
            "bytes_count": len(client.profile_photo_content),
            "timeout": 2,
        }
    ]
    assert transport.xmpp.client.updated_groups[0]["url"].startswith("http://transport.example/avatar/")
    assert transport.xmpp.client.avatar_events == []


async def _test_telegram_group_avatar_change_event_updates_xabber_group_avatar(tmp_path):
    settings = _settings()
    settings = Settings(
        **{
            **settings.__dict__,
            "avatar_storage_dir": str(tmp_path / "avatars"),
            "avatar_base_url": "http://transport.example",
        }
    )
    repository = FakeRepository()
    transport = TelegramTransport(settings, repository)
    transport.xmpp = FakeXmpp()
    client = FakeTelegramClient()
    client.profile_photo_content = b"changed-group-avatar"
    transport._telegram_clients["user@example.com"] = client
    chat_photo = type("FakePhoto", (), {"photo_id": 888})()
    action = type("MessageActionChatEditPhoto", (), {})()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    repository.signatures[("user@example.com", group_jid)] = "old-signature"

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="",
            is_private=False,
            sender_id=200,
            message_id=903,
            title="Telegram Team",
            chat_photo=chat_photo,
            action=action,
        ),
    )

    assert client.profile_photo_downloads[0][1:] == (bytes, False)
    assert transport.xmpp.client.created_groups == []
    assert transport.xmpp.client.group_messages == []
    assert transport.xmpp.client.updated_groups == [
        {
            "owner_jid": "user@example.com",
            "actor_jid": "bot@telegram.example.com",
            "group_jid": group_jid,
            "avatar_id": repository.contact_avatars[
                ("user@example.com", group_jid, "small")
            ]["avatar_id"],
            "url": repository.contact_avatars[
                ("user@example.com", group_jid, "small")
            ]["url"],
            "mime_type": "image/jpeg",
            "bytes_count": len(client.profile_photo_content),
            "timeout": 2,
        }
    ]
    assert repository.signatures[("user@example.com", group_jid)] != "old-signature"


async def _test_group_avatar_update_failure_does_not_block_incoming_group_message(tmp_path):
    settings = _settings()
    settings = Settings(
        **{
            **settings.__dict__,
            "avatar_storage_dir": str(tmp_path / "avatars"),
            "avatar_base_url": "http://transport.example",
        }
    )
    repository = FakeRepository()
    transport = TelegramTransport(settings, repository)
    transport.xmpp = FakeXmpp()
    transport.xmpp.client.group_avatar_error = RuntimeError("avatar update unsupported")
    client = FakeTelegramClient()
    transport._telegram_clients["user@example.com"] = client
    chat_photo = type("FakePhoto", (), {"photo_id": 777})()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="message after invite",
            is_private=False,
            sender_id=200,
            message_id=904,
            title="Telegram Team",
            sender_first_name="Alice",
            chat_photo=chat_photo,
        ),
    )

    assert transport.xmpp.client.updated_groups == []
    assert transport.xmpp.client.group_messages == [
        {
            "sender": "chat-200@telegram.example.com",
            "group_jid": group_jid,
            "body": "message after invite",
            "message_id": "904",
            "reply_reference": None,
            "forward_references": (),
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
            "reply_reference": None,
            "forward_references": (),
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
            "reply_reference": None,
            "forward_references": (),
            "fake_outgoing": True,
        }
    ]


def test_incoming_telegram_group_message_does_not_create_existing_xabber_group():
    asyncio.run(_test_incoming_telegram_group_message_does_not_create_existing_xabber_group())


async def _test_incoming_telegram_group_message_does_not_create_existing_xabber_group():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Team\nTrue\nFalse\n\n"
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
            "timeout": 2,
        },
        {
            "owner_jid": "user@example.com",
            "actor_jid": "bot@telegram.example.com",
            "group_jid": group_jid,
            "member_jid": "chat-200@telegram.example.com",
            "send": False,
            "reason": "Telegram group member",
            "timeout": 2,
        }
    ]
    assert transport.xmpp.client.direct_invites[0]["to_jid"] == "user@example.com"
    assert transport.xmpp.client.group_joins == [
        {
            "member_jid": "chat-200@telegram.example.com",
            "group_jid": group_jid,
            "nickname": "Telegram user 200",
        }
    ]
    assert transport.xmpp.client.group_messages[0]["body"] == "hello existing group"


def test_existing_xabber_group_already_invited_owner_still_sends_direct_invite():
    asyncio.run(_test_existing_xabber_group_already_invited_owner_still_sends_direct_invite())


async def _test_existing_xabber_group_already_invited_owner_still_sends_direct_invite():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100600@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Public\nTrue\nTrue\n\n"
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


def test_already_invited_telegram_group_sender_is_auto_joined_before_message():
    asyncio.run(_test_already_invited_telegram_group_sender_is_auto_joined_before_message())


async def _test_already_invited_telegram_group_sender_is_auto_joined_before_message():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--5386493808@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Test\nTrue\nFalse\n\n"
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    transport._group_protocol_members.add(("user@example.com", group_jid, "user@example.com"))
    transport.xmpp.client.invite_error = FakeIqError(_already_invited_error_xml())

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-5386493808,
            raw_text="test123",
            is_private=False,
            sender_id=356739513,
            message_id=178887,
            title="Test",
            sender_username="member_name",
        ),
    )

    assert transport.xmpp.client.group_joins == [
        {
            "member_jid": "chat-356739513@telegram.example.com",
            "group_jid": group_jid,
            "nickname": "member_name",
        }
    ]
    assert transport.xmpp.client.group_messages == [
        {
            "sender": "chat-356739513@telegram.example.com",
            "group_jid": group_jid,
            "body": "test123",
            "message_id": "178887",
            "reply_reference": None,
            "forward_references": (),
            "fake_outgoing": True,
        }
    ]


def test_xabber_group_fanout_sends_to_telegram_group():
    asyncio.run(_test_xabber_group_fanout_sends_to_telegram_group())


def test_xabber_group_reply_sends_telegram_reply_to_message_id():
    asyncio.run(_test_xabber_group_reply_sends_telegram_reply_to_message_id())


def test_xabber_group_structured_reply_strips_visible_quote_fallback():
    asyncio.run(_test_xabber_group_structured_reply_strips_visible_quote_fallback())


def test_xabber_group_media_only_strips_sender_prefix():
    asyncio.run(_test_xabber_group_media_only_strips_sender_prefix())


def test_incoming_telegram_group_reply_sends_xabber_reply_reference():
    asyncio.run(_test_incoming_telegram_group_reply_sends_xabber_reply_reference())


def test_xabber_group_forward_uses_telegram_native_forward():
    asyncio.run(_test_xabber_group_forward_uses_telegram_native_forward())


def test_incoming_telegram_group_forward_sends_xabber_forward_reference():
    asyncio.run(_test_incoming_telegram_group_forward_sends_xabber_forward_reference())


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
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="user@example.com:\nhello telegram group",
            group_sender_jid="user@example.com",
        )
    )

    assert transport.telegram.group_sent == [(-100500, "hello telegram group", None, None, ())]


async def _test_xabber_group_reply_sends_telegram_reply_to_message_id():
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
    transport._remember_group_reply_context(
        xmpp_jid="user@example.com",
        peer_id="-100500",
        context=DirectReplyContext(
            message_id="910",
            body="Alice:\noriginal group message",
            sender="chat-200@telegram.example.com",
            recipient=group_jid,
        ),
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="user@example.com:\nreply text",
            group_sender_jid="user@example.com",
            reply_to_message_id="910",
            reply_to_message_ids=("910",),
        )
    )

    assert transport.telegram.group_sent == [(-100500, "reply text", "910", None, ())]


async def _test_xabber_group_structured_reply_strips_visible_quote_fallback():
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
    quoted_body = (
        "> Tuesday, September 1, 2026\n"
        "> [16:09:25] bot@telegram.example.com:\n"
        "> bot@telegram.example.com:\n"
        "> test\n"
        "test"
    )

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="user@example.com:\n%s" % quoted_body,
            group_sender_jid="user@example.com",
            reply_to_message_id="910",
            reply_to_message_ids=("910",),
        )
    )

    assert transport.telegram.group_sent == [(-100500, "test", "910", None, ())]


async def _test_xabber_group_media_only_strips_sender_prefix():
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
    group_jid = "telegramg-7465737431406578616d706c652e636f6d--100500@example.com"
    media = (XmppOutgoingMedia(url="https://xabber.example/gallery/photo.jpg", mime_type="image/jpeg"),)

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="test1@example.com:",
            media=media,
            group_sender_jid="test1@example.com",
        )
    )

    assert transport.telegram.group_sent == [(-100500, "", None, None, media)]


async def _test_xabber_group_forward_uses_telegram_native_forward():
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
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="user@example.com:\nforward comment",
            group_sender_jid="user@example.com",
            forward_references=(
                XmppForwardReference(
                    message_id="778",
                    body="forwarded group text",
                    sender="group--100600@telegram.example.com",
                    recipient="user@example.com",
                ),
            ),
        )
    )

    peer_id, body, reply_to_message_id, forward_reference, media = transport.telegram.group_sent[0]
    assert (peer_id, body, reply_to_message_id) == (-100500, "", None)
    assert forward_reference.source_peer_id == -100600
    assert forward_reference.message_id == "778"
    assert media == ()


async def _test_incoming_telegram_group_reply_sends_xabber_reply_reference():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Team\nTrue\nFalse\n\n"
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()
    transport._remember_group_reply_context(
        xmpp_jid="user@example.com",
        peer_id="-100500",
        context=DirectReplyContext(
            message_id="910",
            body="Alice:\noriginal group message",
            sender="chat-200@telegram.example.com",
            recipient=group_jid,
        ),
    )

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="group reply",
            is_private=False,
            sender_id=201,
            message_id=911,
            reply_to_msg_id=910,
            title="Telegram Team",
            sender_first_name="Bob",
        ),
    )

    assert len(transport.xmpp.client.group_messages) == 1
    sent = transport.xmpp.client.group_messages[0]
    assert sent["body"] == "group reply"
    assert sent["reply_reference"] is not None
    assert sent["reply_reference"].message_id == "910"


async def _test_incoming_telegram_group_forward_sends_xabber_forward_reference():
    repository = FakeRepository()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"
    repository.signatures[("user@example.com", group_jid)] = "Telegram Team\nTrue\nFalse\n\n"
    transport = TelegramTransport(_settings(), repository)
    transport.xmpp = FakeXmpp()

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="forwarded group text",
            is_private=False,
            sender_id=201,
            message_id=921,
            title="Telegram Team",
            sender_first_name="Bob",
            fwd_from=FakeForwardHeader(
                from_id=FakeTelegramPeer(channel_id=100600),
                channel_post=778,
            ),
        ),
    )

    assert len(transport.xmpp.client.group_messages) == 1
    sent = transport.xmpp.client.group_messages[0]
    assert sent["body"] == ""
    assert len(sent["forward_references"]) == 1
    assert sent["forward_references"][0].message_id == "778"
    assert sent["forward_references"][0].body == "forwarded group text"
    assert sent["forward_references"][0].sender == "group--100100600@telegram.example.com"


def test_transport_group_fanout_copy_is_ignored():
    asyncio.run(_test_transport_group_fanout_copy_is_ignored())


async def _test_transport_group_fanout_copy_is_ignored():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.telegram = FakeTelegramBackend()
    transport.xmpp = FakeXmpp()

    await transport.send_direct_message(
        XmppIncomingMessage(
            sender="telegramg-75736572406578616d706c652e636f6d--100500@example.com",
            recipient="bot@telegram.example.com",
            body="echo copy",
            group_sender_jid="bot@telegram.example.com",
        )
    )

    assert transport.telegram.group_sent == []


def test_reflected_incoming_telegram_group_message_without_marker_is_ignored():
    asyncio.run(_test_reflected_incoming_telegram_group_message_without_marker_is_ignored())


async def _test_reflected_incoming_telegram_group_message_without_marker_is_ignored():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.telegram = FakeTelegramBackend()
    transport.xmpp = FakeXmpp()
    group_jid = "telegramg-75736572406578616d706c652e636f6d--100500@example.com"

    await transport._handle_incoming_telegram_message(
        "user@example.com",
        FakeEvent(
            chat_id=-100500,
            raw_text="echo from telegram",
            is_private=False,
            sender_id=200,
            message_id=901,
            title="Telegram Team",
            sender_first_name="Alice",
        ),
    )
    await transport.send_direct_message(
        XmppIncomingMessage(
            sender=group_jid,
            recipient="bot@telegram.example.com",
            body="echo from telegram",
            message_id="901",
            group_sender_jid="user@example.com",
        )
    )

    assert transport.telegram.group_sent == []


def test_command_login_callback_starts_telegram_listener():
    asyncio.run(_test_command_login_callback_starts_telegram_listener())


def test_telegram_listener_ignores_messages_older_than_listener_start():
    asyncio.run(_test_telegram_listener_ignores_messages_older_than_listener_start())


async def _test_telegram_listener_ignores_messages_older_than_listener_start():
    transport = TelegramTransport(_settings(), FakeRepository())
    transport.xmpp = FakeXmpp()
    transport.telegram = FakeTelegramBackend()

    await transport._start_telegram_listener("user@example.com", "stored-session")

    client = transport.telegram.clients[0]
    new_message_handler = client.handlers[0][0]
    old_message_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    new_message_time = datetime.now(timezone.utc) + timedelta(seconds=1)

    await new_message_handler(FakeEvent(chat_id=100, raw_text="old", date=old_message_time))
    await new_message_handler(FakeEvent(chat_id=100, raw_text="new", date=new_message_time))

    assert transport.xmpp.client.direct_messages == [
        ("user@example.com", 100, "new", "900", None, (), False)
    ]


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


def _already_invited_error_xml():
    iq = ET.Element("iq")
    error = ET.SubElement(iq, "error", {"code": "409", "type": "cancel"})
    ET.SubElement(error, "{urn:ietf:params:xml:ns:xmpp-stanzas}conflict")
    text = ET.SubElement(error, "{urn:ietf:params:xml:ns:xmpp-stanzas}text")
    text.text = "User was already invited"
    return iq
