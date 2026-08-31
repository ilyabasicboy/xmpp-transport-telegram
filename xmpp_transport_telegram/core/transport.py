import asyncio
import logging
from typing import Dict, Optional

from telethon import events

from xmpp_transport_telegram.core.commands import CommandService
from xmpp_transport_telegram.core.qr_store import QrCodeStore
from xmpp_transport_telegram.core.session_manager import SessionCipher
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.storage.repository import Repository
from xmpp_transport_telegram.telegram.backend import TelegramBackend
from xmpp_transport_telegram.xmpp.component import XmppComponent


log = logging.getLogger(__name__)


class TelegramTransport:
    TELEGRAM_CONTACTS_CIRCLE = "Telegram"

    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.telegram = TelegramBackend(settings)
        self.session_cipher = SessionCipher(settings.session_encryption_key)
        self.qr_store = QrCodeStore(settings.qr_storage_dir, "%s/qr" % settings.qr_base_url)
        self.commands = CommandService(
            repository,
            self.telegram,
            self.session_cipher,
            self.qr_store,
            self._ensure_telegram_contact,
            self._replace_telegram_listener,
        )
        self.xmpp = XmppComponent(settings, self.commands.handle, self.send_direct_message)
        self._telegram_clients: Dict[str, object] = {}
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        await self.xmpp.start()
        await self._sync_connected_contacts_after_restart()
        log.info("Telegram transport backend started")
        await self._stopped.wait()

    async def stop(self) -> None:
        self._stopped.set()
        await self._stop_all_telegram_listeners()
        await self.xmpp.stop()

    async def send_direct_message(self, xmpp_jid: str, contact_jid: str, body: str) -> None:
        peer_id = self._peer_id_from_contact_jid(contact_jid)
        session_data = await self._load_connected_session_data(xmpp_jid)
        await self._ensure_telegram_listener(xmpp_jid, session_data)
        client = self._telegram_clients.get(xmpp_jid)
        if client is None:
            raise RuntimeError("Telegram session expired. Send /login again.")
        log.debug(
            "Sending XMPP direct message to Telegram peer xmpp_jid=%s contact_jid=%s peer_id=%s body_length=%s",
            xmpp_jid,
            contact_jid,
            peer_id,
            len(body),
        )
        await self.telegram.send_direct_message(client, peer_id, body)
        log.debug(
            "Sent XMPP direct message to Telegram peer xmpp_jid=%s peer_id=%s",
            xmpp_jid,
            peer_id,
        )

    async def _ensure_telegram_contact(self, xmpp_jid: str, contact) -> None:
        contact_jid = CommandService.contact_jid(self.settings.xmpp_component_jid, contact)
        sync_signature = CommandService.contact_sync_signature(contact)
        stored_signature = await self.repository.get_synced_roster_item_signature(
            xmpp_jid,
            contact_jid,
        )
        if stored_signature == sync_signature:
            return

        # Xabber represents contact circles as roster groups.  Keeping all
        # synced Telegram direct chats in one group makes the import
        # visible without inventing a separate server-side abstraction.
        await self.xmpp.client.send_transport_operation(
            "add-roster-contact",
            {
                "owner_jid": xmpp_jid,
                "contact_jid": contact_jid,
                "name": contact.title,
                "create_chat": "true",
            },
            groups=(self.TELEGRAM_CONTACTS_CIRCLE,),
        )
        await self.repository.set_synced_roster_item_signature(
            xmpp_jid,
            contact_jid,
            "contact",
            sync_signature,
        )

    async def _sync_connected_contacts_after_restart(self) -> None:
        sessions = await self.repository.list_connected_telegram_sessions()
        if not sessions:
            return

        log.info("Synchronizing Telegram direct chats after restart for %s account(s)", len(sessions))
        for session in sessions:
            xmpp_jid = session["xmpp_jid"]
            try:
                synced_count = await self._sync_contacts_for_stored_session(
                    xmpp_jid,
                    session["encrypted_session"],
                )
            except Exception:
                log.exception("Telegram restart contact sync failed for %s", xmpp_jid)
            else:
                log.info(
                    "Synchronized %s Telegram direct chat(s) after restart for %s",
                    synced_count,
                    xmpp_jid,
                )

    async def _sync_contacts_for_stored_session(self, xmpp_jid: str, encrypted_session: str) -> int:
        session_data = self.session_cipher.decrypt(encrypted_session)
        client = self.telegram.client_for_session(session_data)
        await client.connect()
        synced_count = 0
        try:
            if not await client.is_user_authorized():
                log.warning("Skipping restart contact sync for expired Telegram session %s", xmpp_jid)
                return 0
            contacts = await self.telegram.list_contacts(client)
            # Reuse the same idempotent roster write path used by /add, /sync-contacts,
            # and first login so restart recovery cannot create duplicate roster churn.
            for contact in contacts:
                try:
                    await self._ensure_telegram_contact(xmpp_jid, contact)
                except Exception:
                    log.exception(
                        "Telegram restart contact sync failed for %s contact_id=%s",
                        xmpp_jid,
                        getattr(contact, "peer_id", "unknown"),
                    )
                else:
                    synced_count += 1
        finally:
            await client.disconnect()
        await self._start_telegram_listener(xmpp_jid, session_data)
        return synced_count

    async def _authorized_client(self, xmpp_jid: str):
        session_data = await self._load_connected_session_data(xmpp_jid)
        return await self._authorized_client_for_session(session_data)

    async def _load_connected_session_data(self, xmpp_jid: str) -> str:
        account_id = await self.repository.ensure_xmpp_account(xmpp_jid)
        row = await self.repository.get_telegram_session(account_id)
        if row is None or not row["connected"] or not row["encrypted_session"]:
            raise RuntimeError("Telegram is not connected. Send /login first.")
        return self.session_cipher.decrypt(row["encrypted_session"])

    async def _authorized_client_for_session(self, session_data: str):
        client = self.telegram.client_for_session(session_data)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("Telegram session expired. Send /login again.")
        return client

    async def _ensure_telegram_listener(self, xmpp_jid: str, session_data: str) -> None:
        client = self._telegram_clients.get(xmpp_jid)
        if client is not None and getattr(client, "is_connected", lambda: True)():
            log.debug("Telegram listener already active for %s", xmpp_jid)
            return
        log.debug("Telegram listener missing or disconnected for %s; starting it", xmpp_jid)
        await self._start_telegram_listener(xmpp_jid, session_data)

    async def _replace_telegram_listener(
        self,
        xmpp_jid: str,
        session_data: str,
        previous_owner: Optional[str],
    ) -> None:
        if previous_owner and previous_owner != xmpp_jid:
            await self._stop_telegram_listener(previous_owner)
        await self._start_telegram_listener(xmpp_jid, session_data)

    async def _start_telegram_listener(self, xmpp_jid: str, session_data: str) -> None:
        log.debug("Starting Telegram direct-message listener for %s", xmpp_jid)
        await self._stop_telegram_listener(xmpp_jid)
        client = self.telegram.client_for_session(session_data)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            log.warning("Skipping Telegram listener for expired session %s", xmpp_jid)
            return

        async def handle_event(event) -> None:
            try:
                await self._handle_incoming_telegram_message(xmpp_jid, event)
            except Exception:
                log.exception("Telegram incoming message handler failed for %s", xmpp_jid)

        async def handle_raw_update(update) -> None:
            log.debug(
                "Received Telegram raw update xmpp_jid=%s update_type=%s",
                xmpp_jid,
                type(update).__name__,
            )

        client.add_event_handler(handle_raw_update, events.Raw())
        client.add_event_handler(handle_event, events.NewMessage())
        self._telegram_clients[xmpp_jid] = client
        log.info("Started Telegram direct-message listener for %s", xmpp_jid)

    async def _stop_telegram_listener(self, xmpp_jid: str) -> None:
        client = self._telegram_clients.pop(xmpp_jid, None)
        if client is not None:
            log.debug("Stopping Telegram direct-message listener for %s", xmpp_jid)
            await client.disconnect()

    async def _stop_all_telegram_listeners(self) -> None:
        xmpp_jids = list(self._telegram_clients)
        for xmpp_jid in xmpp_jids:
            await self._stop_telegram_listener(xmpp_jid)

    async def _handle_incoming_telegram_message(self, xmpp_jid: str, event) -> None:
        log.debug(
            "Received Telegram NewMessage event xmpp_jid=%s out=%s is_private=%s chat_id=%s sender_id=%s raw_text_length=%s",
            xmpp_jid,
            getattr(event, "out", None),
            getattr(event, "is_private", None),
            getattr(event, "chat_id", None),
            getattr(event, "sender_id", None),
            len(str(getattr(event, "raw_text", "") or "")),
        )
        if getattr(event, "out", False):
            log.debug("Ignoring outgoing Telegram event for %s", xmpp_jid)
            return
        if getattr(event, "is_private", True) is False:
            log.debug("Ignoring non-private Telegram event for %s", xmpp_jid)
            return
        body = str(getattr(event, "raw_text", "") or "").strip()
        if not body:
            log.debug("Ignoring Telegram event without text body for %s", xmpp_jid)
            return
        peer_id = self._peer_id_from_incoming_event(event)
        if peer_id is None:
            log.debug("Ignoring Telegram message without peer id for %s", xmpp_jid)
            return
        log.debug(
            "Delivering incoming Telegram message to XMPP xmpp_jid=%s peer_id=%s body_length=%s",
            xmpp_jid,
            peer_id,
            len(body),
        )
        self.xmpp.client.send_direct_message(xmpp_jid, int(peer_id), body)
        log.debug(
            "Delivered incoming Telegram message to XMPP xmpp_jid=%s peer_id=%s",
            xmpp_jid,
            peer_id,
        )

    @staticmethod
    def _peer_id_from_incoming_event(event):
        peer_id = getattr(event, "chat_id", None)
        if peer_id is not None:
            return peer_id
        return getattr(event, "sender_id", None)

    def _peer_id_from_contact_jid(self, contact_jid: str) -> int:
        prefix = "chat-"
        suffix = "@%s" % self.settings.xmpp_component_jid
        if not contact_jid.startswith(prefix) or not contact_jid.endswith(suffix):
            raise ValueError("Unsupported Telegram contact JID.")
        value = contact_jid[len(prefix) : -len(suffix)]
        try:
            return int(value)
        except ValueError:
            raise ValueError("Unsupported Telegram contact JID.")
