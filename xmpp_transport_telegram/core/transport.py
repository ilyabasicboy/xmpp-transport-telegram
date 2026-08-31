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
        )
        self.xmpp = XmppComponent(settings, self.commands.handle)
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        await self.xmpp.start()
        log.info("Telegram transport backend started")
        await self._stopped.wait()

    async def stop(self) -> None:
        self._stopped.set()
        await self.xmpp.stop()
