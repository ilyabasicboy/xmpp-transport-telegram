import logging
from typing import List, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramContact, TelegramDialog


log = logging.getLogger(__name__)


class TelegramBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def client_for_session(self, session_data: Optional[str] = None) -> TelegramClient:
        return TelegramClient(
            StringSession(session_data or ""),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )

    async def list_dialogs(self, client: TelegramClient, limit: int = 100) -> List[TelegramDialog]:
        dialogs = []
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            peer_id = int(dialog.id)
            title = dialog.name or str(peer_id)
            dialogs.append(
                TelegramDialog(
                    peer_id=peer_id,
                    title=title,
                    username=getattr(entity, "username", None),
                    phone=getattr(entity, "phone", None),
                    is_group=bool(getattr(dialog, "is_group", False)),
                    is_channel=bool(getattr(dialog, "is_channel", False)),
                )
            )
        return dialogs

    async def list_contacts(self, client: TelegramClient) -> List[TelegramContact]:
        contacts_by_peer_id = {}
        result = await client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", []):
            peer_id = int(user.id)
            contacts_by_peer_id[peer_id] = TelegramContact(
                peer_id=peer_id,
                title=self._user_title(user),
                username=getattr(user, "username", None),
                phone=getattr(user, "phone", None),
            )

        async for dialog in client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(getattr(dialog, "is_channel", False)):
                continue
            entity = dialog.entity
            peer_id = int(dialog.id)
            if peer_id in contacts_by_peer_id:
                continue
            title = dialog.name or self._user_title(entity)
            contacts_by_peer_id[peer_id] = TelegramContact(
                peer_id=peer_id,
                title=title,
                username=getattr(entity, "username", None),
                phone=getattr(entity, "phone", None),
            )
        return sorted(contacts_by_peer_id.values(), key=lambda contact: contact.title.lower())

    async def send_direct_message(self, client: TelegramClient, peer_id: int, body: str) -> None:
        entity = await self._resolve_direct_entity(client, peer_id)
        log.debug(
            "Resolved Telegram direct message entity peer_id=%s entity_type=%s body_length=%s",
            peer_id,
            type(entity).__name__,
            len(body),
        )
        await client.send_message(entity, body)

    async def _resolve_direct_entity(self, client: TelegramClient, peer_id: int):
        result = await client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", []):
            if int(user.id) == peer_id:
                log.debug("Resolved Telegram peer_id=%s from address-book contacts", peer_id)
                return user

        async for dialog in client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(getattr(dialog, "is_channel", False)):
                continue
            if int(dialog.id) == peer_id:
                log.debug("Resolved Telegram peer_id=%s from private dialogs", peer_id)
                return dialog.entity

        log.debug("Could not resolve Telegram direct peer_id=%s from contacts or private dialogs", peer_id)
        raise ValueError("Telegram direct chat is not available. Send /sync-contacts and try again.")

    @staticmethod
    def _user_title(user) -> str:
        first_name = getattr(user, "first_name", None)
        last_name = getattr(user, "last_name", None)
        full_name = " ".join(part for part in (first_name, last_name) if part)
        username = getattr(user, "username", None)
        phone = getattr(user, "phone", None)
        if full_name:
            return full_name
        if username:
            return "@%s" % username
        if phone:
            return "+%s" % phone
        return str(getattr(user, "id", "unknown"))
