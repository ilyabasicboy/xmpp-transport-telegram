from typing import List, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramContact, TelegramDialog


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
        # hash=0 asks Telegram for the full address book instead of a
        # not-modified shortcut.  The caller decides how much of it to display.
        result = await client(GetContactsRequest(hash=0))
        contacts = []
        for user in getattr(result, "users", []):
            if getattr(user, "bot", False):
                continue
            title = self._user_title(user)
            contacts.append(
                TelegramContact(
                    peer_id=int(user.id),
                    title=title,
                    username=getattr(user, "username", None),
                    phone=getattr(user, "phone", None),
                )
            )
        return sorted(contacts, key=lambda contact: contact.title.lower())

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
