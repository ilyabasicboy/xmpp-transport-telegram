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
class XmppIncomingMessage:
    sender: str
    recipient: str
    body: str
    group_sender_jid: Optional[str] = None
    message_id: Optional[str] = None
    message_ids: tuple = ()
    reply_to_message_id: Optional[str] = None
    reply_to_message_ids: tuple = ()
