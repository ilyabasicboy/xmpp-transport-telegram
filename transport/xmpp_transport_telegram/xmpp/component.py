import asyncio
import logging

from xmpp_transport_telegram.runtime.config import Settings


log = logging.getLogger(__name__)


class XmppComponent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        log.info(
            "XMPP component placeholder configured for %s at %s:%s",
            self.settings.xmpp_component_jid,
            self.settings.xmpp_component_host,
            self.settings.xmpp_component_port,
        )

    async def stop(self) -> None:
        self._stopped.set()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()
