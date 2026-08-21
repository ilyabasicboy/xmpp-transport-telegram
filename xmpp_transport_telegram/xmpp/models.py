from dataclasses import dataclass


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
