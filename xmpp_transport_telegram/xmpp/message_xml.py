from xml.etree import ElementTree as ET
from typing import Optional

from xmpp_transport_telegram.xmpp.namespaces import (
    FILES_NS,
    GROUPS_NS,
    PUBSUB_AVATAR_METADATA_THUMBNAIL_NS,
    XABBER_REFERENCES_NS,
)


class XmppMessageXml:
    @classmethod
    def body_with_media_references(cls, body: str, media: tuple) -> tuple:
        references = []
        if not media:
            return body, references
        result = body
        if result and not result.endswith("\n"):
            result += "\n"
        media_items = [item for item in media if str(getattr(item, "url", "") or "").strip()]
        for index, item in enumerate(media_items):
            url = str(getattr(item, "url", "") or "").strip()
            begin = cls.escaped_text_len(result)
            result += url
            end = cls.escaped_text_len(result)
            references.append(cls.media_reference_element(item, begin, end))
            if index != len(media_items) - 1:
                result += "\n"
        return result, references

    @classmethod
    def media_reference_element(cls, media: object, begin: int, end: int) -> ET.Element:
        reference = ET.Element(
            "{%s}reference" % XABBER_REFERENCES_NS,
            {
                "type": "mutable",
                "begin": str(begin),
                "end": str(end),
            },
        )
        file_sharing = ET.SubElement(reference, "{%s}file-sharing" % FILES_NS)
        file_el = ET.SubElement(file_sharing, "file")
        cls.append_media_field(file_el, "media-type", getattr(media, "mime_type", None))
        thumbnail_url = getattr(media, "thumbnail_url", None)
        if thumbnail_url:
            ET.SubElement(
                file_el,
                "{%s}thumbnail" % PUBSUB_AVATAR_METADATA_THUMBNAIL_NS,
                {"uri": str(thumbnail_url)},
            )
        cls.append_media_field(file_el, "name", getattr(media, "name", None))
        cls.append_media_field(file_el, "size", getattr(media, "size", None))
        cls.append_media_field(file_el, "height", getattr(media, "height", None))
        cls.append_media_field(file_el, "width", getattr(media, "width", None))
        sources = ET.SubElement(file_sharing, "sources")
        uri = ET.SubElement(sources, "uri")
        uri.text = str(getattr(media, "url", ""))
        return reference

    @staticmethod
    def append_media_field(parent: ET.Element, tag: str, value: object) -> None:
        if value is None or value == "":
            return
        if isinstance(value, int) and value <= 0:
            return
        child = ET.SubElement(parent, tag)
        child.text = str(value)

    @staticmethod
    def xml_escaped_text(value: str) -> str:
        return value.replace("&", "&amp;").replace(">", "&gt;").replace("<", "&lt;")

    @staticmethod
    def utf16_len(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    @classmethod
    def escaped_text_len(cls, value: str) -> int:
        return cls.utf16_len(cls.xml_escaped_text(value))

    @classmethod
    def group_sender_jid(cls, msg) -> Optional[str]:
        groups_x = msg.xml.find("{%s}x" % GROUPS_NS)
        if groups_x is None:
            return None
        user = cls.child_by_local_name(groups_x, "user", namespace=GROUPS_NS)
        if user is None:
            return None
        jid = cls.child_by_local_name(user, "jid")
        if jid is None:
            return None
        value = (jid.text or "").strip()
        if not value:
            return None
        return value.split("/", 1)[0]

    @staticmethod
    def child_by_local_name(
        parent: ET.Element,
        local_name: str,
        namespace: Optional[str] = None,
    ) -> Optional[ET.Element]:
        for child in parent:
            if XmppMessageXml.local_name(child.tag) != local_name:
                continue
            if namespace is not None and not str(child.tag).startswith("{%s}" % namespace):
                continue
            return child
        return None

    @staticmethod
    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
