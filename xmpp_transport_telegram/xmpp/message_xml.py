from xml.etree import ElementTree as ET

from xmpp_transport_telegram.xmpp.namespaces import (
    FILES_NS,
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
