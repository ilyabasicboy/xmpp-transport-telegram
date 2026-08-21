import os
from typing import List

from telethon import TelegramClient

from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.telegram.models import TelegramDialog


class TelegramBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def client_for_account(self, account_key: str) -> TelegramClient:
        os.makedirs(self.settings.telegram_session_storage_dir, exist_ok=True)
        session_path = os.path.join(self.settings.telegram_session_storage_dir, account_key)
        return TelegramClient(
            session_path,
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
