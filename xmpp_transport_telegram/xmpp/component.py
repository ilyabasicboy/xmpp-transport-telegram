import asyncio
import logging
from typing import Awaitable, Callable, Optional
from xml.etree import ElementTree as ET

from slixmpp import ComponentXMPP
from slixmpp.jid import JID

from xmpp_transport_telegram.core.commands import ControlResponse
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.xmpp.message_xml import XmppMessageXml
from xmpp_transport_telegram.xmpp.namespaces import TRANSPORT_TELEGRAM_NS


log = logging.getLogger(__name__)


CommandHandler = Callable[
    [str, str, Callable[[str], Awaitable[None]]],
    Awaitable[ControlResponse],
]
DirectMessageHandler = Callable[[str, str, str], Awaitable[None]]


class TelegramCommandComponent(ComponentXMPP):
    def __init__(
        self,
        settings: Settings,
        command_handler: CommandHandler,
        direct_message_handler: Optional[DirectMessageHandler] = None,
    ) -> None:
        super().__init__(
            settings.xmpp_component_jid,
            settings.xmpp_component_secret,
            settings.xmpp_component_host,
            settings.xmpp_component_port,
        )
        self.settings = settings
        self.command_handler = command_handler
        self.direct_message_handler = direct_message_handler
        self.bot_jid = "bot@%s" % settings.xmpp_component_jid
        self.transport_server_domain = settings.transport_server_domain
        self.component_domain = settings.xmpp_component_jid
        self._session_ready = asyncio.Event()
        self.add_event_handler("session_start", self._handle_session_start)
        self.add_event_handler("disconnected", self._handle_disconnected)
        self.add_event_handler("message", self._handle_message)

    async def _handle_session_start(self, _event) -> None:
        log.info("XMPP component session started for %s", self.boundjid.bare)
        self._session_ready.set()

    def _handle_disconnected(self, _event) -> None:
        self._session_ready.clear()

    async def wait_until_ready(self, timeout: int) -> None:
        await asyncio.wait_for(self._session_ready.wait(), timeout=timeout)

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
        from_jid = str(JID(message["from"]).bare)
        if to_jid.bare != self.bot_jid:
            await self._handle_direct_message(from_jid, to_jid, body)
            return

        async def notify(reply_body: str) -> None:
            self._send_reply(message["from"], reply_body)

        try:
            response = await self.command_handler(from_jid, body, notify)
        except (RuntimeError, ValueError) as exc:
            self._send_reply(message["from"], str(exc))
            return
        except Exception:
            log.exception("Telegram command failed for %s", from_jid)
            self._send_reply(message["from"], "Telegram command failed. Try again later.")
            return
        self._send_reply(message["from"], response.body, media=response.media)

    async def _handle_direct_message(self, from_jid: str, to_jid: JID, body: str) -> None:
        if self.direct_message_handler is None:
            log.debug("Ignoring direct message addressed to %s without handler", to_jid.bare)
            return
        if to_jid.domain != self.component_domain:
            log.debug("Ignoring direct message addressed outside component domain: %s", to_jid.bare)
            return
        try:
            await self.direct_message_handler(from_jid, to_jid.bare, body)
        except (RuntimeError, ValueError) as exc:
            self._send_chat(str(from_jid), str(to_jid.bare), str(exc))
        except Exception:
            log.exception("Telegram direct message failed from %s to %s", from_jid, to_jid.bare)
            self._send_chat(
                str(from_jid),
                str(to_jid.bare),
                "Telegram message failed. Try again later.",
            )

    def _send_reply(self, to_jid: str, body: str, media: tuple = ()) -> None:
        body, media_references = XmppMessageXml.body_with_media_references(body, media)
        message = self._make_chat_message(to_jid, self.bot_jid, body)
        for reference in media_references:
            message.xml.append(reference)
        message.send()

    def send_direct_message(self, to_jid: str, peer_id: int, body: str) -> None:
        from_jid = "chat-%s@%s" % (peer_id, self.component_domain)
        log.debug(
            "Sending incoming Telegram message as XMPP stanza from=%s to=%s body_length=%s",
            from_jid,
            to_jid,
            len(body),
        )
        self._send_chat(to_jid, from_jid, body)

    def _send_chat(self, to_jid: str, from_jid: str, body: str) -> None:
        self._make_chat_message(to_jid, from_jid, body).send()

    def _make_chat_message(self, to_jid: str, from_jid: str, body: str):
        return self.make_message(
            mto=to_jid,
            mfrom=from_jid,
            mbody=body,
            mtype="chat",
        )

    async def send_transport_operation(
        self,
        operation: str,
        fields: dict,
        groups: tuple = (),
        timeout: int = 10,
    ) -> str:
        query = self._transport_query_element(operation, fields, groups)
        iq = self.make_iq_set(
            sub=query,
            ito=self.transport_server_domain,
            ifrom=self.component_domain,
        )
        # Server-owned roster changes go through the privileged module. The
        # Python transport records only what it asked to sync, never roster rows.
        result = await iq.send(timeout=timeout)
        query_result = result.xml.find("{%s}query" % TRANSPORT_TELEGRAM_NS)
        if query_result is None:
            return "ok"
        return query_result.attrib.get("status", "ok")

    def _transport_query_element(self, operation: str, fields: dict, groups: tuple) -> ET.Element:
        query = ET.Element(
            "{%s}query" % TRANSPORT_TELEGRAM_NS,
            {"op": operation},
        )
        for name, value in fields.items():
            field = ET.SubElement(query, "field", {"name": str(name)})
            field.text = str(value)
        for group in groups:
            group_el = ET.SubElement(query, "group")
            group_el.text = str(group)
        return query


class XmppComponent:
    def __init__(
        self,
        settings: Settings,
        command_handler: CommandHandler,
        direct_message_handler: Optional[DirectMessageHandler] = None,
    ) -> None:
        self.settings = settings
        self.client = TelegramCommandComponent(settings, command_handler, direct_message_handler)
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        await self.client.connect(
            self.settings.xmpp_component_host,
            self.settings.xmpp_component_port,
        )
        await self.client.wait_until_ready(self.settings.xmpp_component_connect_timeout)
        log.info("XMPP component connected as %s", self.settings.xmpp_component_jid)

    async def stop(self) -> None:
        self._stopped.set()
        if self.client.is_connected():
            await self.client.disconnect()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()
