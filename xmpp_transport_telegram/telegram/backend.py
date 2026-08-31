from typing import List, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramDialog


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
