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
from xmpp_transport_telegram.telegram.models import TelegramDialog
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
        self._group_ensure_signatures: Dict[tuple, str] = {}
        self._group_protocol_members: set = set()
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

    async def send_direct_message(
        self,
        xmpp_jid: str,
        contact_jid: str,
        body: str,
        group_sender_jid: Optional[str] = None,
    ) -> None:
        group_route = self._bot_group_fanout_route(xmpp_jid, contact_jid, group_sender_jid)
        if group_route is not None:
            owner_jid, chat_id = group_route
            await self._send_xabber_group_message_to_telegram(owner_jid, chat_id, body)
            return
        if contact_jid == self.xmpp.client.bot_jid and group_sender_jid is not None:
            return

        localpart = self.xmpp.client.parse_component_localpart(contact_jid)
        if localpart is not None and localpart.startswith("group-") and localpart.removeprefix("group-"):
            await self._send_xabber_group_message_to_telegram(
                xmpp_jid,
                localpart.removeprefix("group-"),
                body,
            )
            return

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

    async def _ensure_telegram_group_chat(self, xmpp_jid: str, chat: TelegramDialog) -> None:
        group_jid = self._group_jid(str(chat.peer_id), xmpp_jid)
        sync_signature = self._group_sync_signature(chat)
        group_key = (xmpp_jid, group_jid)
        if self._group_ensure_signatures.get(group_key) == sync_signature:
            await self._ensure_group_protocol_owner_member(
                owner_jid=xmpp_jid,
                group_jid=group_jid,
            )
            return
        stored_signature = await self.repository.get_synced_roster_item_signature(xmpp_jid, group_jid)
        if stored_signature == sync_signature:
            await self._ensure_group_protocol_owner_member(
                owner_jid=xmpp_jid,
                group_jid=group_jid,
            )
            self._group_ensure_signatures[group_key] = sync_signature
            return

        transport_member_jid = self._group_transport_member_jid()
        created_group_jid = await self.xmpp.client.create_xabber_group(
            owner_jid=xmpp_jid,
            actor_jid=transport_member_jid,
            localpart=self._group_localpart(xmpp_jid, str(chat.peer_id)),
            title=chat.title,
            description="Telegram group %s" % chat.peer_id,
        )
        if created_group_jid != group_jid:
            log.warning(
                "XEP-GROUPS create returned unexpected Telegram group_jid=%s expected=%s",
                created_group_jid,
                group_jid,
            )
        await self._ensure_group_protocol_owner_member(
            owner_jid=xmpp_jid,
            group_jid=group_jid,
        )
        await self.repository.set_synced_roster_item_signature(
            xmpp_jid,
            group_jid,
            "group",
            sync_signature,
        )
        self._group_ensure_signatures[group_key] = sync_signature

    async def _ensure_group_protocol_owner_member(self, owner_jid: str, group_jid: str) -> None:
        invited = await self._ensure_group_protocol_member(
            owner_jid=owner_jid,
            group_jid=group_jid,
            member_jid=owner_jid,
        )
        if not invited:
            return
        self.xmpp.client.send_xabber_group_invite(
            from_jid=self._group_transport_member_jid(),
            to_jid=owner_jid,
            group_jid=group_jid,
            reason="Telegram group member",
        )

    async def _ensure_group_protocol_member(
        self,
        owner_jid: str,
        group_jid: str,
        member_jid: str,
    ) -> bool:
        if member_jid == self._group_transport_member_jid():
            return False
        member_key = (owner_jid, group_jid, member_jid)
        if member_key in self._group_protocol_members:
            return False
        try:
            await self.xmpp.client.invite_xabber_group_member(
                owner_jid=owner_jid,
                actor_jid=self._group_transport_member_jid(),
                group_jid=group_jid,
                member_jid=member_jid,
                send=False,
                reason="Telegram group member",
            )
        except Exception as exc:
            if self._is_group_member_already_invited_error(exc):
                log.debug(
                    "XEP-GROUPS member already invited owner=%s group=%s member=%s",
                    owner_jid,
                    group_jid,
                    member_jid,
                )
                self._group_protocol_members.add(member_key)
                return True
            log.debug(
                "XEP-GROUPS invite returned non-fatal result owner=%s group=%s member=%s error=%s",
                owner_jid,
                group_jid,
                member_jid,
                exc,
            )
            return False
        self._group_protocol_members.add(member_key)
        return True

    @staticmethod
    def _is_group_member_already_invited_error(exc: Exception) -> bool:
        iq = getattr(exc, "iq", None)
        xml = getattr(iq, "xml", None)
        if xml is None:
            return False
        conflict = xml.find(".//{urn:ietf:params:xml:ns:xmpp-stanzas}conflict")
        if conflict is None:
            return False
        text = xml.find(".//{urn:ietf:params:xml:ns:xmpp-stanzas}text")
        return text is not None and "already invited" in (text.text or "").lower()

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
        log.info("Started Telegram message listener for %s", xmpp_jid)

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
        is_outgoing = getattr(event, "out", False)
        is_group_chat = self._is_telegram_group_chat_event(event)
        if is_outgoing and not is_group_chat:
            log.debug("Ignoring outgoing private Telegram event for %s", xmpp_jid)
            return
        body = str(getattr(event, "raw_text", "") or "").strip()
        if not body:
            log.debug("Ignoring Telegram event without text body for %s", xmpp_jid)
            return
        peer_id = self._peer_id_from_incoming_event(event)
        if peer_id is None:
            log.debug("Ignoring Telegram message without peer id for %s", xmpp_jid)
            return
        if is_group_chat:
            await self._handle_incoming_telegram_group_message(xmpp_jid, event, int(peer_id), body)
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

    @staticmethod
    def _is_telegram_group_chat_event(event) -> bool:
        if getattr(event, "is_group", False) is True:
            return True
        if getattr(event, "is_channel", False) is True:
            return True
        if getattr(event, "is_private", None) is False:
            return True
        chat_id = getattr(event, "chat_id", None)
        return isinstance(chat_id, int) and chat_id < 0

    async def _handle_incoming_telegram_group_message(
        self,
        xmpp_jid: str,
        event,
        peer_id: int,
        body: str,
    ) -> None:
        chat = await self._telegram_group_dialog_for_event(event, peer_id)
        await self._ensure_telegram_group_chat(xmpp_jid, chat)
        if getattr(event, "out", False):
            sender = self._group_transport_member_jid()
        else:
            sender_id = getattr(event, "sender_id", None)
            if sender_id is not None:
                sender = self._telegram_user_jid(int(sender_id))
            else:
                sender = self._group_transport_member_jid()
        group_jid = self._group_jid(str(peer_id), xmpp_jid)
        await self._ensure_group_protocol_member(
            owner_jid=xmpp_jid,
            group_jid=group_jid,
            member_jid=sender,
        )
        message_id = self._incoming_telegram_message_id(event)
        log.debug(
            "Delivering incoming Telegram group message to Xabber xmpp_jid=%s group_jid=%s sender=%s body_length=%s",
            xmpp_jid,
            group_jid,
            sender,
            len(body),
        )
        self.xmpp.client.send_xabber_group_message(
            sender=sender,
            group_jid=group_jid,
            body=body,
            message_id=message_id,
            fake_outgoing=True,
        )

    async def _send_xabber_group_message_to_telegram(
        self,
        xmpp_jid: str,
        chat_id: str,
        body: str,
    ) -> None:
        session_data = await self._load_connected_session_data(xmpp_jid)
        await self._ensure_telegram_listener(xmpp_jid, session_data)
        client = self._telegram_clients.get(xmpp_jid)
        if client is None:
            raise RuntimeError("Telegram session expired. Send /login again.")
        log.debug(
            "Sending Xabber group message to Telegram xmpp_jid=%s chat_id=%s body_length=%s",
            xmpp_jid,
            chat_id,
            len(body),
        )
        await self.telegram.send_group_message(client, int(chat_id), body)

    async def _telegram_group_dialog_for_event(self, event, peer_id: int) -> TelegramDialog:
        title = None
        get_chat = getattr(event, "get_chat", None)
        if get_chat is not None:
            chat_entity = await get_chat()
            title = getattr(chat_entity, "title", None) or getattr(chat_entity, "username", None)
        return TelegramDialog(
            peer_id=peer_id,
            title=title or "Telegram group %s" % peer_id,
            is_group=True,
        )

    def _bot_group_fanout_route(
        self,
        sender_jid: str,
        recipient_jid: str,
        group_sender_jid: Optional[str],
    ) -> Optional[tuple]:
        if recipient_jid != self.xmpp.client.bot_jid:
            return None
        sender_suffix = "@%s" % self.settings.transport_server_domain
        if not sender_jid.endswith(sender_suffix):
            return None
        sender_localpart = sender_jid[: -len(sender_suffix)]
        if not sender_localpart.startswith("telegramg-"):
            return None
        if group_sender_jid is None:
            return None
        if group_sender_jid == self._group_transport_member_jid():
            return None
        if group_sender_jid.endswith("@%s" % self.settings.xmpp_component_jid):
            return None
        route = self._parse_group_jid_localpart(sender_localpart)
        if route is None:
            return None
        owner_jid, _chat_id = route
        if group_sender_jid != owner_jid:
            return None
        return route

    @staticmethod
    def _incoming_telegram_message_id(event) -> str:
        message = getattr(event, "message", None)
        message_id = getattr(message, "id", None) if message is not None else None
        if message_id is None:
            message_id = getattr(event, "id", None)
        return str(message_id) if message_id is not None else "telegram-message"

    def _group_jid(self, chat_id: str, owner_jid: str) -> str:
        return "%s@%s" % (
            self._group_localpart(owner_jid, chat_id),
            self.settings.transport_server_domain,
        )

    @staticmethod
    def _group_localpart(owner_jid: str, chat_id: str) -> str:
        return "telegramg-%s-%s" % (
            owner_jid.encode("utf-8").hex(),
            TelegramTransport._safe_group_token(chat_id),
        )

    @staticmethod
    def _parse_group_jid_localpart(localpart: str) -> Optional[tuple]:
        payload = localpart.removeprefix("telegramg-")
        if "-" not in payload:
            return None
        owner_hex, chat_id = payload.split("-", 1)
        if not owner_hex or not chat_id:
            return None
        try:
            owner_jid = bytes.fromhex(owner_hex).decode("utf-8")
        except ValueError:
            return None
        return owner_jid, chat_id

    @staticmethod
    def _safe_group_token(value: str) -> str:
        return "".join(char for char in str(value) if char.isalnum() or char in "-_") or "unknown"

    @staticmethod
    def _group_sync_signature(chat: TelegramDialog) -> str:
        return "%s\n%s\n%s" % (chat.title, chat.is_group, chat.is_channel)

    def _group_transport_member_jid(self) -> str:
        return "bot@%s" % self.settings.xmpp_component_jid

    def _telegram_user_jid(self, user_id: int) -> str:
        return "chat-%s@%s" % (user_id, self.settings.xmpp_component_jid)

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
