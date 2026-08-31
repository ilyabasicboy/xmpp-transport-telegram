import asyncio
import logging
from dataclasses import dataclass
from datetime import timezone
from typing import Awaitable, Callable, Dict, Optional

from telethon.errors import SessionPasswordNeededError

from xmpp_transport_telegram.core.qr_store import QrCodeStore, StoredQrImage
from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.core.state import AuthStatus


HELP_TEXT = """Telegram transport commands:
/login - start Telegram QR authorization
/password <password> - complete Telegram cloud password after QR scan
/status
/contacts
/add <number>
/logout
/help"""


NotifyCallback = Callable[[str], Awaitable[None]]


@dataclass
class QrLoginAttempt:
    account_id: int
    client: object
    qr_login: object
    qr_image: StoredQrImage
    notify: NotifyCallback
    status: AuthStatus
    task: asyncio.Task


@dataclass(frozen=True)
class ControlResponse:
    body: str
    media: tuple = ()


log = logging.getLogger(__name__)


def command_response(body: str) -> str:
    text = body.strip()
    command = text.split(None, 1)[0].lower() if text else "/help"

    if command == "/help":
        return HELP_TEXT
    if command == "/login":
        return "Telegram QR authorization requires the running transport service."
    if command == "/code":
        return "Telegram QR authorization does not use SMS code commands. Send /login to start QR authorization."
    if command == "/password":
        return "Send /password only after scanning a Telegram QR login link that asks for a cloud password."
    if command == "/status":
        return "Telegram account is not connected."
    if command == "/contacts":
        return "Telegram contact sync is not implemented yet."
    if command == "/add":
        return "Telegram contact add is not implemented yet."
    if command == "/logout":
        return "Telegram logout requires the running transport service."
    return "Unknown command.\n\n%s" % HELP_TEXT


class CommandService:
    def __init__(
        self,
        repository,
        telegram,
        session_cipher: SessionCipher,
        qr_store: QrCodeStore,
    ) -> None:
        self.repository = repository
        self.telegram = telegram
        self.session_cipher = session_cipher
        self.qr_store = qr_store
        self._qr_attempts: Dict[str, QrLoginAttempt] = {}

    async def handle(self, xmpp_jid: str, body: str, notify: NotifyCallback) -> ControlResponse:
        text = body.strip()
        if not text:
            return self._response(HELP_TEXT)

        parts = text.split(None, 1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            return self._response(HELP_TEXT)
        if command == "/login":
            return await self._start_qr_login(xmpp_jid, notify)
        if command == "/password":
            return self._response(await self._complete_password(xmpp_jid, argument))
        if command == "/code":
            return self._response(
                "Telegram QR authorization does not use SMS code commands. Send /login to start QR authorization."
            )
        if command == "/status":
            return self._response(await self._status(xmpp_jid))
        if command == "/contacts":
            return self._response("Telegram contact sync is not implemented yet.")
        if command == "/add":
            return self._response("Telegram contact add is not implemented yet.")
        if command == "/logout":
            return self._response(await self._logout(xmpp_jid))
        return self._response("Unknown command.\n\n%s" % HELP_TEXT)

    async def _start_qr_login(self, xmpp_jid: str, notify: NotifyCallback) -> ControlResponse:
        existing_attempt = self._qr_attempts.get(xmpp_jid)
        if existing_attempt is not None and not existing_attempt.task.done():
            return self._qr_response(existing_attempt.qr_login, existing_attempt.qr_image)

        account_id = await self.repository.ensure_xmpp_account(xmpp_jid)
        session_data = await self._load_session(account_id)
        client = self.telegram.client_for_session(session_data)
        await client.connect()

        if await client.is_user_authorized():
            user = await client.get_me()
            await self._save_connected_session(account_id, client, user)
            await client.disconnect()
            return self._response(
                "Telegram account is already connected as %s." % self._format_user(user)
            )

        qr_login = await client.qr_login()
        qr_image = self.qr_store.create(qr_login.url)
        task = asyncio.create_task(self._wait_for_qr_login(xmpp_jid))
        self._qr_attempts[xmpp_jid] = QrLoginAttempt(
            account_id=account_id,
            client=client,
            qr_login=qr_login,
            qr_image=qr_image,
            notify=notify,
            status=AuthStatus.WAITING_QR,
            task=task,
        )
        return self._qr_response(qr_login, qr_image)

    async def _wait_for_qr_login(self, xmpp_jid: str) -> None:
        attempt = self._qr_attempts.get(xmpp_jid)
        if attempt is None:
            return

        try:
            user = await attempt.qr_login.wait()
        except SessionPasswordNeededError:
            attempt.status = AuthStatus.WAITING_PASSWORD
            await attempt.notify(
                "Telegram QR scan accepted. Send /password <password> to complete authorization."
            )
            return
        except asyncio.TimeoutError:
            await self._finish_failed_attempt(
                xmpp_jid,
                "Telegram QR authorization expired. Send /login to create a new QR login link.",
            )
        except Exception:
            log.exception("Telegram QR authorization failed for %s", xmpp_jid)
            await self._finish_failed_attempt(
                xmpp_jid,
                "Telegram QR authorization failed. Send /login to try again.",
            )
        else:
            await self._save_connected_session(attempt.account_id, attempt.client, user)
            await attempt.notify("Telegram account connected as %s." % self._format_user(user))
            await self._discard_attempt(xmpp_jid)

    async def _complete_password(self, xmpp_jid: str, password: str) -> str:
        if not password:
            return "Usage: /password <password>"

        attempt = self._qr_attempts.get(xmpp_jid)
        if attempt is None or attempt.status != AuthStatus.WAITING_PASSWORD:
            return "No Telegram QR authorization is waiting for a cloud password. Send /login first."

        try:
            user = await attempt.client.sign_in(password=password)
        except Exception:
            log.exception("Telegram cloud password failed for %s", xmpp_jid)
            return "Telegram cloud password was rejected. Send /password <password> to try again."

        await self._save_connected_session(attempt.account_id, attempt.client, user)
        await self._discard_attempt(xmpp_jid)
        return "Telegram account connected as %s." % self._format_user(user)

    async def _status(self, xmpp_jid: str) -> str:
        attempt = self._qr_attempts.get(xmpp_jid)
        if attempt is not None and not attempt.task.done():
            if attempt.status == AuthStatus.WAITING_PASSWORD:
                return "Telegram QR scan accepted. Waiting for /password <password>."
            return "Telegram QR authorization is waiting for scan."

        account_id = await self.repository.ensure_xmpp_account(xmpp_jid)
        row = await self.repository.get_telegram_session(account_id)
        if row is not None and row["connected"]:
            user_id = row["telegram_user_id"]
            return "Telegram account is connected%s." % (" as %s" % user_id if user_id else "")
        return "Telegram account is not connected."

    async def _logout(self, xmpp_jid: str) -> str:
        await self._discard_attempt(xmpp_jid)
        account_id = await self.repository.ensure_xmpp_account(xmpp_jid)
        session_data = await self._load_session(account_id)
        if session_data:
            client = self.telegram.client_for_session(session_data)
            await client.connect()
            try:
                if await client.is_user_authorized():
                    await client.log_out()
            finally:
                await client.disconnect()
        await self.repository.delete_telegram_session(account_id)
        return "Telegram account disconnected."

    async def _load_session(self, account_id: int) -> Optional[str]:
        row = await self.repository.get_telegram_session(account_id)
        if row is None or not row["encrypted_session"]:
            return None
        return self.session_cipher.decrypt(row["encrypted_session"])

    async def _save_connected_session(self, account_id: int, client, user) -> None:
        session_data = client.session.save()
        encrypted_session = self.session_cipher.encrypt(session_data)
        await self.repository.upsert_telegram_session(
            account_id,
            int(user.id),
            getattr(user, "phone", None),
            encrypted_session,
            True,
        )

    async def _finish_failed_attempt(self, xmpp_jid: str, message: str) -> None:
        attempt = self._qr_attempts.get(xmpp_jid)
        if attempt is not None:
            await attempt.notify(message)
        await self._discard_attempt(xmpp_jid)

    async def _discard_attempt(self, xmpp_jid: str) -> None:
        attempt = self._qr_attempts.pop(xmpp_jid, None)
        if attempt is None:
            return
        if not attempt.task.done() and attempt.task is not asyncio.current_task():
            attempt.task.cancel()
        await attempt.client.disconnect()

    def _qr_response(self, qr_login, qr_image: StoredQrImage) -> ControlResponse:
        return ControlResponse(
            body=self._format_qr_response(qr_login, qr_image),
            media=(qr_image,),
        )

    @classmethod
    def _response(cls, body: str) -> ControlResponse:
        return ControlResponse(body=body)

    def _format_qr_response(self, qr_login, qr_image: StoredQrImage) -> str:
        expires = qr_login.expires.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            "Scan this Telegram login QR SVG before %s:\n\n"
            "%s\n\n"
            "Direct Telegram login link:\n%s\n\n"
            "In Telegram, use Settings > Devices > Link Desktop Device. "
            "If Telegram asks for a cloud password after accepting the login, send /password <password>."
        ) % (expires, qr_image.url, qr_login.url)

    def _format_user(self, user) -> str:
        username = getattr(user, "username", None)
        if username:
            return "@%s" % username
        first_name = getattr(user, "first_name", None)
        last_name = getattr(user, "last_name", None)
        full_name = " ".join(part for part in (first_name, last_name) if part)
        return full_name or str(getattr(user, "id", "unknown user"))
