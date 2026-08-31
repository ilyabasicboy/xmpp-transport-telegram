import asyncio
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from xmpp_transport_telegram.core.commands import CommandService, HELP_TEXT, command_response
from xmpp_transport_telegram.core.session_manager import SessionCipher


def test_help_command_returns_command_list():
    assert command_response("/help") == HELP_TEXT


def test_empty_message_returns_help():
    assert command_response("   ") == HELP_TEXT


def test_status_is_disconnected_dummy_response():
    assert command_response("/status") == "Telegram account is not connected."


def test_known_placeholder_command():
    assert "requires the running transport service" in command_response("/login")


def test_unknown_command_includes_help():
    response = command_response("/unknown")

    assert response.startswith("Unknown command.")
    assert HELP_TEXT in response


class FakeRepository:
    def __init__(self):
        self.sessions = {}

    async def ensure_xmpp_account(self, xmpp_jid):
        return 1

    async def get_telegram_session(self, xmpp_account_id):
        return self.sessions.get(xmpp_account_id)

    async def upsert_telegram_session(
        self,
        xmpp_account_id,
        telegram_user_id,
        phone,
        encrypted_session,
        connected,
    ):
        self.sessions[xmpp_account_id] = {
            "telegram_user_id": telegram_user_id,
            "phone": phone,
            "encrypted_session": encrypted_session,
            "connected": connected,
        }

    async def delete_telegram_session(self, xmpp_account_id):
        self.sessions.pop(xmpp_account_id, None)


class FakeSession:
    def save(self):
        return "saved-session"


class FakeQrLogin:
    url = "tg://login?token=test-token"
    expires = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    def __init__(self):
        self._event = asyncio.Event()

    async def wait(self):
        await self._event.wait()
        return FakeUser()

    def complete(self):
        self._event.set()


class FakeUser:
    id = 42
    username = "telegram_user"
    phone = "+15551234567"


class FakeClient:
    def __init__(self):
        self.session = FakeSession()
        self.qr_login_value = FakeQrLogin()
        self.disconnected = False

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return False

    async def qr_login(self):
        return self.qr_login_value


class FakeTelegramBackend:
    def __init__(self):
        self.client = FakeClient()

    def client_for_session(self, session_data=None):
        return self.client


class FakeQrStore:
    def create(self, qr_link):
        return type(
            "FakeStoredQrImage",
            (),
            {
                "url": "https://transport.example/qr/telegram-login-qr-test.svg",
                "name": "telegram-login-qr-test.svg",
                "mime_type": "image/svg+xml",
                "size": 123,
            },
        )()


def test_login_starts_qr_authorization():
    asyncio.run(_test_login_starts_qr_authorization())


async def _test_login_starts_qr_authorization():
    repository = FakeRepository()
    telegram = FakeTelegramBackend()
    cipher = SessionCipher(Fernet.generate_key().decode("ascii"))
    service = CommandService(repository, telegram, cipher, FakeQrStore())
    notifications = []

    async def notify(body):
        notifications.append(body)

    response = await service.handle("user@example.com", "/login", notify)

    assert "https://transport.example/qr/telegram-login-qr-test.svg" in response.body
    assert "tg://login?token=test-token" in response.body
    assert response.media[0].mime_type == "image/svg+xml"
    status = await service.handle("user@example.com", "/status", notify)
    assert status.body == (
        "Telegram QR authorization is waiting for scan."
    )

    telegram.client.qr_login_value.complete()
    await asyncio.wait_for(telegram.client.qr_login_value._event.wait(), timeout=1)
    await asyncio.sleep(0)

    assert notifications == ["Telegram account connected as @telegram_user."]
    assert repository.sessions[1]["connected"] is True
