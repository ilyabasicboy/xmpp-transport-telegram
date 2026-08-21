import asyncio
import logging

from slixmpp import ComponentXMPP
from slixmpp.jid import JID

from xmpp_transport_telegram.core.commands import command_response
from xmpp_transport_telegram.runtime.config import Settings


log = logging.getLogger(__name__)


class TelegramCommandComponent(ComponentXMPP):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings.xmpp_component_jid,
            settings.xmpp_component_secret,
            settings.xmpp_component_host,
            settings.xmpp_component_port,
        )
        self.settings = settings
        self.bot_jid = "bot@%s" % settings.xmpp_component_jid
        self.add_event_handler("session_start", self._handle_session_start)
        self.add_event_handler("message", self._handle_message)

    async def _handle_session_start(self, _event) -> None:
        log.info("XMPP component session started for %s", self.boundjid.bare)

    def _handle_message(self, message) -> None:
        message_type = message["type"]
        if message_type not in ("chat", "normal", ""):
            return

        body = str(message["body"] or "").strip()
        if not body:
            return

        to_jid = JID(message["to"])
        if to_jid.bare != self.bot_jid:
            log.debug("Ignoring message addressed to %s", to_jid.bare)
            return

        reply = command_response(body)
        self.send_message(
            mto=message["from"],
            mfrom=self.bot_jid,
            mbody=reply,
            mtype="chat",
        )


class XmppComponent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TelegramCommandComponent(settings)
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        await self.client.connect(
            self.settings.xmpp_component_host,
            self.settings.xmpp_component_port,
        )
        log.info("XMPP component connected as %s", self.settings.xmpp_component_jid)

    async def stop(self) -> None:
        self._stopped.set()
        if self.client.is_connected():
            await self.client.disconnect()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()
