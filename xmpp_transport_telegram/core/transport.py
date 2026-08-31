import asyncio
import logging

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
        )
        self.xmpp = XmppComponent(settings, self.commands.handle)
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        await self.xmpp.start()
        await self._sync_connected_contacts_after_restart()
        log.info("Telegram transport backend started")
        await self._stopped.wait()

    async def stop(self) -> None:
        self._stopped.set()
        await self.xmpp.stop()

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
        # synced Telegram address-book contacts in one group makes the import
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

        log.info("Synchronizing Telegram contacts after restart for %s account(s)", len(sessions))
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
                    "Synchronized %s Telegram contact(s) after restart for %s",
                    synced_count,
                    xmpp_jid,
                )

    async def _sync_contacts_for_stored_session(self, xmpp_jid: str, encrypted_session: str) -> int:
        session_data = self.session_cipher.decrypt(encrypted_session)
        client = self.telegram.client_for_session(session_data)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                log.warning("Skipping restart contact sync for expired Telegram session %s", xmpp_jid)
                return 0
            contacts = await self.telegram.list_contacts(client)
            # Reuse the same idempotent roster write path used by /add, /sync-contacts,
            # and first login so restart recovery cannot create duplicate roster churn.
            synced_count = 0
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
            return synced_count
        finally:
            await client.disconnect()
