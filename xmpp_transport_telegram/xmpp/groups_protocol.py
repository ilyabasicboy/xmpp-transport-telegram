from typing import Optional
from xml.etree import ElementTree as ET

from xmpp_transport_telegram.xmpp.namespaces import (
    CHAT_MARKERS_NS,
    GROUPS_NS,
    NICK_NS,
    PUBSUB_AVATAR_METADATA_NS,
    SID_NS,
)


class XabberGroupsXml:
    """Builders for the Xabber Groups protocol payloads used by the transport."""

    @staticmethod
    def create_group(
        localpart: str,
        title: str,
        description: str,
        privacy: str = "public",
        membership: str = "private",
        index: str = "none",
    ) -> ET.Element:
        create = ET.Element("{%s}create" % GROUPS_NS)
        group = ET.SubElement(create, "group", {"privacy": privacy})
        localpart_el = ET.SubElement(group, "localpart")
        localpart_el.text = localpart
        info = ET.SubElement(group, "info")
        name = ET.SubElement(info, "name")
        name.text = title
        if description:
            description_el = ET.SubElement(info, "description")
            description_el.text = description
        settings = ET.SubElement(group, "settings")
        index_el = ET.SubElement(settings, "index")
        index_el.text = index
        membership_el = ET.SubElement(settings, "membership")
        membership_el.text = membership
        return create

    @staticmethod
    def update_info(title: Optional[str] = None, avatar: Optional[dict] = None) -> ET.Element:
        info = ET.Element("{%s}info" % GROUPS_NS)
        if title is not None:
            name = ET.SubElement(info, "name")
            name.text = title
        if avatar is not None:
            avatar_el = ET.SubElement(info, "avatar")
            ET.SubElement(
                avatar_el,
                "{%s}info" % PUBSUB_AVATAR_METADATA_NS,
                {
                    "bytes": str(avatar["bytes"]),
                    "id": str(avatar["id"]),
                    "type": str(avatar["type"]),
                    "url": str(avatar["url"]),
                },
            )
        return info

    @staticmethod
    def invite(jid: str, send: bool = False, reason: Optional[str] = None) -> ET.Element:
        invite = ET.Element("{%s}invite" % GROUPS_NS)
        jid_el = ET.SubElement(invite, "jid")
        jid_el.text = jid
        send_el = ET.SubElement(invite, "send")
        send_el.text = "true" if send else "false"
        if reason:
            reason_el = ET.SubElement(invite, "reason")
            reason_el.text = reason
        return invite

    @staticmethod
    def direct_invite(group_jid: str, reason: Optional[str] = None) -> ET.Element:
        invite = ET.Element("{%s}invite" % GROUPS_NS, {"jid": group_jid})
        if reason:
            reason_el = ET.SubElement(invite, "reason")
            reason_el.text = reason
        return invite

    @staticmethod
    def nick(name: str) -> ET.Element:
        nick = ET.Element("{%s}nick" % NICK_NS)
        nick.text = name
        return nick

    @staticmethod
    def message_markers(message_id: str) -> list:
        return [
            ET.Element("{%s}markable" % CHAT_MARKERS_NS),
            ET.Element("{%s}origin-id" % SID_NS, {"id": message_id}),
        ]
