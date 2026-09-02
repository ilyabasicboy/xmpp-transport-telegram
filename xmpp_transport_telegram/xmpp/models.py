from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class XmppAddress:
    bare: str


@dataclass(frozen=True)
class TelegramContactJid:
    peer_id: int
    component_domain: str

    @property
    def jid(self) -> str:
        return "chat-%s@%s" % (self.peer_id, self.component_domain)


@dataclass(frozen=True)
class XmppReplyReference:
    message_id: str
    body: str
    sender: str
    recipient: str
    fake_outgoing: bool = False


@dataclass(frozen=True)
class XmppForwardReference:
    message_id: str
    body: str
    sender: str
    recipient: str
    media: tuple = ()
    fake_outgoing: bool = False


@dataclass(frozen=True)
class XmppOutgoingMedia:
    url: str
    name: str = ""
    mime_type: str = "application/octet-stream"
    size: int = 0
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    voice: bool = False


@dataclass(frozen=True)
class XmppIncomingMessage:
    sender: str
    recipient: str
    body: str
    media: tuple = ()
    forward_references: tuple = ()
    group_sender_jid: Optional[str] = None
    message_id: Optional[str] = None
    message_ids: tuple = ()
    reply_to_message_id: Optional[str] = None
    reply_to_message_ids: tuple = ()
