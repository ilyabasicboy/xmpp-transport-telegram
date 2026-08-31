import asyncio
import logging
from typing import Awaitable, Callable

from slixmpp import ComponentXMPP
from slixmpp.jid import JID

from xmpp_transport_telegram.core.commands import ControlResponse
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.xmpp.message_xml import XmppMessageXml


log = logging.getLogger(__name__)


CommandHandler = Callable[
    [str, str, Callable[[str], Awaitable[None]]],
    Awaitable[ControlResponse],
]


class TelegramCommandComponent(ComponentXMPP):
    def __init__(self, settings: Settings, command_handler: CommandHandler) -> None:
        super().__init__(
            settings.xmpp_component_jid,
            settings.xmpp_component_secret,
            settings.xmpp_component_host,
            settings.xmpp_component_port,
        )
        self.settings = settings
        self.command_handler = command_handler
        self.bot_jid = "bot@%s" % settings.xmpp_component_jid
        self.add_event_handler("session_start", self._handle_session_start)
        self.add_event_handler("message", self._handle_message)

    async def _handle_session_start(self, _event) -> None:
        log.info("XMPP component session started for %s", self.boundjid.bare)

    def _handle_message(self, message) -> None:
        asyncio.create_task(self._handle_message_async(message))

    async def _handle_message_async(self, message) -> None:
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

        from_jid = str(JID(message["from"]).bare)

        async def notify(reply_body: str) -> None:
            self._send_reply(message["from"], reply_body)

        response = await self.command_handler(from_jid, body, notify)
        self._send_reply(message["from"], response.body, media=response.media)

    def _send_reply(self, to_jid: str, body: str, media: tuple = ()) -> None:
        body, media_references = XmppMessageXml.body_with_media_references(body, media)
        message = self.make_message(
            mto=to_jid,
            mfrom=self.bot_jid,
            mbody=body,
            mtype="chat",
        )
        for reference in media_references:
            message.xml.append(reference)
        message.send()


class XmppComponent:
    def __init__(self, settings: Settings, command_handler: CommandHandler) -> None:
        self.settings = settings
        self.client = TelegramCommandComponent(settings, command_handler)
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
