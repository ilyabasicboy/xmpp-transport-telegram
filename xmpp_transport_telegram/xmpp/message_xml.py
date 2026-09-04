import mimetypes
from xml.etree import ElementTree as ET
from typing import Optional
from urllib.parse import unquote, urlsplit

from xmpp_transport_telegram.xmpp.namespaces import (
    CHAT_MARKERS_NS,
    FILES_NS,
    FORWARDED_NS,
    GROUPS_NS,
    PUBSUB_AVATAR_METADATA_THUMBNAIL_NS,
    SID_NS,
    TRANSPORT_FAKE_OUTGOING_TAG,
    VOICE_MESSAGE_NS,
    XABBER_REFERENCES_NS,
)
from xmpp_transport_telegram.xmpp.models import XmppForwardReference, XmppOutgoingMedia, XmppReplyReference


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
        parent = reference
        if bool(getattr(media, "voice", False)):
            parent = ET.SubElement(reference, "{%s}voice-message" % VOICE_MESSAGE_NS)
        file_sharing = ET.SubElement(parent, "{%s}file-sharing" % FILES_NS)
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
        cls.append_media_field(file_el, "duration", getattr(media, "duration", None))
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
    def extract_reply_to_message_ids(cls, msg) -> tuple:
        body = str(msg["body"] or "")
        for child in msg.xml:
            if child.tag != "{%s}reference" % XABBER_REFERENCES_NS:
                continue
            if cls.is_forward_reference(msg, child):
                continue
            reply_to_message_ids = cls.reply_target_message_ids(child)
            if not reply_to_message_ids:
                continue
            body = cls.strip_reply_fallback_body(body)
            return reply_to_message_ids, body
        return (), body

    @classmethod
    def extract_forwarded_body_and_references(cls, msg, body: str) -> tuple:
        forward_references = []
        fallback_ranges = []
        for reference in msg.xml:
            if reference.tag != "{%s}reference" % XABBER_REFERENCES_NS:
                continue
            if not cls.is_forward_reference(msg, reference):
                continue
            forwarded_message = cls.forwarded_message(reference)
            if forwarded_message is None:
                continue
            reference_range = cls.reference_body_range(reference)
            if reference_range is not None:
                fallback_ranges.append(reference_range)
            forwarded_body = cls.body_text(forwarded_message).strip()
            forwarded_message_ids = cls.reply_target_message_ids(reference)
            forward_references.append(
                XmppForwardReference(
                    message_id=forwarded_message_ids[0] if forwarded_message_ids else "",
                    body=forwarded_body,
                    sender=str(forwarded_message.attrib.get("from") or "").split("/", 1)[0],
                    recipient=str(forwarded_message.attrib.get("to") or "").split("/", 1)[0],
                    fake_outgoing=forwarded_message.find(TRANSPORT_FAKE_OUTGOING_TAG) is not None,
                )
            )
        if not forward_references:
            return body, ()
        comment_body = cls.strip_escaped_ranges(body, fallback_ranges).strip()
        return comment_body, tuple(forward_references)

    @classmethod
    def extract_forwarded_body_media_and_references(cls, msg, body: str) -> tuple:
        forwarded_media = []
        forward_references = []
        fallback_ranges = []
        seen_urls = set()
        for reference in msg.xml:
            if reference.tag != "{%s}reference" % XABBER_REFERENCES_NS:
                continue
            if not cls.is_forward_reference(msg, reference):
                continue
            forwarded_message = cls.forwarded_message(reference)
            if forwarded_message is None:
                continue
            reference_range = cls.reference_body_range(reference)
            if reference_range is not None:
                fallback_ranges.append(reference_range)
            forwarded_body = cls.body_text(forwarded_message)
            media = cls.extract_media_references_from_xml(forwarded_message)
            media = media + cls.extract_body_media_urls(forwarded_body, media)
            forwarded_body = cls.strip_media_fallback_body(forwarded_body, media).strip()
            forwarded_message_ids = cls.reply_target_message_ids(reference)
            forward_references.append(
                XmppForwardReference(
                    message_id=forwarded_message_ids[0] if forwarded_message_ids else "",
                    body=forwarded_body,
                    sender=str(forwarded_message.attrib.get("from") or "").split("/", 1)[0],
                    recipient=str(forwarded_message.attrib.get("to") or "").split("/", 1)[0],
                    media=media,
                    fake_outgoing=forwarded_message.find(TRANSPORT_FAKE_OUTGOING_TAG) is not None,
                )
            )
            for item in media:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                forwarded_media.append(item)
        if not forward_references:
            return body, (), ()
        comment_body = cls.strip_escaped_ranges(body, fallback_ranges).strip()
        return comment_body, tuple(forwarded_media), tuple(forward_references)

    @classmethod
    def is_forward_reference(cls, msg, reference: ET.Element) -> bool:
        forwarded_message = cls.forwarded_message(reference)
        if forwarded_message is None:
            return False
        outer_to = cls.stanza_bare_jid(msg, "to")
        if not outer_to:
            return False
        inner_from = str(forwarded_message.attrib.get("from") or "").split("/", 1)[0]
        inner_to = str(forwarded_message.attrib.get("to") or "").split("/", 1)[0]
        return outer_to not in {inner_from, inner_to}

    @staticmethod
    def stanza_bare_jid(msg, key: str) -> str:
        value = msg[key]
        bare = getattr(value, "bare", value)
        return str(bare or "").split("/", 1)[0]

    @classmethod
    def reference_body_range(cls, reference: ET.Element) -> Optional[tuple]:
        begin = cls.nonnegative_int(reference.attrib.get("begin"))
        end = cls.nonnegative_int(reference.attrib.get("end"))
        if begin is None or end is None or end < begin:
            return None
        return begin, end

    @classmethod
    def strip_escaped_ranges(cls, body: str, ranges: list) -> str:
        if not body or not ranges:
            return body
        result = []
        escaped_offset = 0
        range_index = 0
        sorted_ranges = sorted(ranges)
        for character in body:
            escaped_character = cls.xml_escaped_text(character)
            next_offset = escaped_offset + cls.utf16_len(escaped_character)
            while range_index < len(sorted_ranges) and escaped_offset >= sorted_ranges[range_index][1]:
                range_index += 1
            in_range = (
                range_index < len(sorted_ranges)
                and escaped_offset >= sorted_ranges[range_index][0]
                and next_offset <= sorted_ranges[range_index][1]
            )
            if not in_range:
                result.append(character)
            escaped_offset = next_offset
        return "".join(result)

    @staticmethod
    def nonnegative_int(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            parsed = int(str(value).strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def reply_target_message_ids(reference: ET.Element) -> tuple:
        message = XmppMessageXml.forwarded_message(reference)
        if message is None:
            return ()
        origin_ids = []
        stanza_ids = []
        for child in message:
            if child.tag == "{%s}origin-id" % SID_NS:
                origin_id = child.attrib.get("id")
                if origin_id:
                    origin_ids.append(origin_id)
            if child.tag == "{%s}stanza-id" % SID_NS:
                stanza_id = child.attrib.get("id")
                if stanza_id:
                    stanza_ids.append(stanza_id)
        message_id = message.attrib.get("id")
        candidates = origin_ids + ([message_id] if message_id else []) + stanza_ids
        return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))

    @staticmethod
    def forwarded_message(reference: ET.Element) -> Optional[ET.Element]:
        forwarded = reference.find("{%s}forwarded" % FORWARDED_NS)
        if forwarded is None:
            return None
        for child in forwarded:
            if child.tag == "{jabber:client}message" or child.tag.rsplit("}", 1)[-1] == "message":
                return child
        return None

    @staticmethod
    def body_text(message: ET.Element) -> str:
        body = message.find("{jabber:client}body")
        if body is None:
            for child in message:
                if child.tag.rsplit("}", 1)[-1] == "body":
                    body = child
                    break
        return body.text or "" if body is not None else ""

    @staticmethod
    def message_candidate_ids(msg) -> tuple:
        candidates = []
        message_id = str(msg["id"] or "").strip()
        if message_id:
            candidates.append(message_id)
        for child in msg.xml:
            if child.tag == "{%s}origin-id" % SID_NS:
                origin_id = child.attrib.get("id")
                if origin_id:
                    candidates.append(origin_id)
            if child.tag == "{%s}stanza-id" % SID_NS:
                stanza_id = child.attrib.get("id")
                if stanza_id:
                    candidates.append(stanza_id)
        return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))

    @classmethod
    def extract_media_references(cls, msg) -> tuple:
        return cls.extract_media_references_from_xml(msg.xml)

    @classmethod
    def extract_media_references_from_xml(cls, xml: ET.Element) -> tuple:
        media = []
        seen_urls = set()
        for reference in xml:
            if reference.tag != "{%s}reference" % XABBER_REFERENCES_NS:
                continue
            voice_message = cls.child_by_local_name(reference, "voice-message", namespace=VOICE_MESSAGE_NS)
            file_sharing_parent = voice_message if voice_message is not None else reference
            file_sharing = cls.child_by_local_name(file_sharing_parent, "file-sharing", namespace=FILES_NS)
            if file_sharing is None:
                continue
            item = cls.media_from_file_sharing(file_sharing)
            if item is None or item.url in seen_urls:
                continue
            if voice_message is not None:
                item = XmppOutgoingMedia(
                    url=item.url,
                    name=item.name,
                    mime_type=item.mime_type,
                    size=item.size,
                    thumbnail_url=item.thumbnail_url,
                    width=item.width,
                    height=item.height,
                    duration=item.duration,
                    voice=True,
                )
            seen_urls.add(item.url)
            media.append(item)
        return tuple(media)

    @classmethod
    def extract_body_media_urls(cls, body: str, existing_media: tuple) -> tuple:
        existing_urls = {item.url for item in existing_media}
        media = []
        for line in body.splitlines():
            url = line.strip()
            if url in existing_urls:
                continue
            item = cls.media_from_gallery_url(url)
            if item is None:
                continue
            existing_urls.add(item.url)
            media.append(item)
        return tuple(media)

    @staticmethod
    def media_from_gallery_url(url: str) -> Optional[XmppOutgoingMedia]:
        if not url.startswith(("http://", "https://")):
            return None
        parsed = urlsplit(url)
        if "/gallery/" not in parsed.path and "/upload/" not in parsed.path:
            return None
        name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
        if not name or "." not in name:
            return None
        mime_type, _encoding = mimetypes.guess_type(name)
        return XmppOutgoingMedia(
            url=url,
            name=name,
            mime_type=mime_type or "application/octet-stream",
        )

    @classmethod
    def media_from_file_sharing(cls, file_sharing: ET.Element) -> Optional[XmppOutgoingMedia]:
        sources = cls.child_by_local_name(file_sharing, "sources")
        url = ""
        if sources is not None:
            for child in sources:
                if cls.local_name(child.tag) != "uri":
                    continue
                candidate = (child.text or "").strip()
                if candidate.startswith(("http://", "https://")):
                    url = candidate
                    break
        if not url:
            return None

        file_el = cls.child_by_local_name(file_sharing, "file")
        fields = {}
        thumbnail_url = None
        if file_el is not None:
            for child in file_el:
                local_name = cls.local_name(child.tag)
                if local_name == "thumbnail":
                    thumbnail_url = (child.attrib.get("uri") or "").strip() or None
                    continue
                fields[local_name] = (child.text or "").strip()

        return XmppOutgoingMedia(
            url=url,
            name=fields.get("name", ""),
            mime_type=fields.get("media-type") or fields.get("mime-type") or "application/octet-stream",
            size=cls.positive_int(fields.get("size")) or 0,
            thumbnail_url=thumbnail_url,
            width=cls.positive_int(fields.get("width")),
            height=cls.positive_int(fields.get("height")),
            duration=cls.positive_int(fields.get("duration")),
        )

    @staticmethod
    def strip_media_fallback_body(body: str, media: tuple) -> str:
        result = body
        for item in media:
            if not item.url:
                continue
            lines = [line for line in result.splitlines() if line.strip() != item.url]
            result = "\n".join(lines)
        return result

    @staticmethod
    def child_by_local_name(parent: ET.Element, local_name: str, namespace: Optional[str] = None) -> Optional[ET.Element]:
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

    @staticmethod
    def positive_int(value: object) -> Optional[int]:
        parsed = XmppMessageXml.nonnegative_int(value)
        return parsed if parsed and parsed > 0 else None

    @classmethod
    def reply_reference_element(cls, reply_reference: XmppReplyReference) -> ET.Element:
        fallback_prefix = cls.reply_fallback_prefix(reply_reference)
        reference = ET.Element(
            "{%s}reference" % XABBER_REFERENCES_NS,
            {
                "type": "mutable",
                "begin": "0",
                "end": str(cls.escaped_text_len(fallback_prefix)),
            },
        )
        forwarded = ET.SubElement(reference, "{%s}forwarded" % FORWARDED_NS)
        message = ET.SubElement(
            forwarded,
            "{jabber:client}message",
            {
                "from": reply_reference.sender,
                "to": reply_reference.recipient,
                "type": "chat",
                "id": reply_reference.message_id,
            },
        )
        ET.SubElement(message, "{%s}markable" % CHAT_MARKERS_NS)
        ET.SubElement(message, "{%s}origin-id" % SID_NS, {"id": reply_reference.message_id})
        if reply_reference.fake_outgoing:
            ET.SubElement(message, TRANSPORT_FAKE_OUTGOING_TAG)
        body = ET.SubElement(message, "{jabber:client}body")
        body.text = reply_reference.body
        return reference

    @classmethod
    def body_with_forward_references(cls, body: str, forward_references: tuple) -> tuple:
        references = []
        if not forward_references:
            return body, references
        result = ""
        for forward_reference in forward_references:
            fallback = cls.forward_fallback_text(forward_reference)
            begin = cls.escaped_text_len(result)
            result += fallback
            end = cls.escaped_text_len(result)
            references.append(cls.forward_reference_element(forward_reference, begin=begin, end=end))
        if body:
            result += body
        return result, references

    @classmethod
    def forward_reference_element(
        cls,
        forward_reference: XmppForwardReference,
        *,
        begin: int,
        end: int,
    ) -> ET.Element:
        reference = ET.Element(
            "{%s}reference" % XABBER_REFERENCES_NS,
            {
                "type": "mutable",
                "begin": str(begin),
                "end": str(end),
            },
        )
        forwarded = ET.SubElement(reference, "{%s}forwarded" % FORWARDED_NS)
        message = ET.SubElement(
            forwarded,
            "{jabber:client}message",
            {
                "from": forward_reference.sender,
                "to": forward_reference.recipient,
                "type": "chat",
            },
        )
        if forward_reference.message_id:
            message.attrib["id"] = forward_reference.message_id
        ET.SubElement(message, "{%s}markable" % CHAT_MARKERS_NS)
        if forward_reference.message_id:
            ET.SubElement(message, "{%s}origin-id" % SID_NS, {"id": forward_reference.message_id})
        if forward_reference.fake_outgoing:
            ET.SubElement(message, TRANSPORT_FAKE_OUTGOING_TAG)
        body = ET.SubElement(message, "{jabber:client}body")
        body.text = forward_reference.body
        return reference

    @staticmethod
    def forward_fallback_text(forward_reference: XmppForwardReference) -> str:
        fallback_body = XmppMessageXml.forward_fallback_body(forward_reference)
        quoted_lines = fallback_body.splitlines() or [fallback_body]
        quoted_text = "\n".join("> %s" % line if line else ">" for line in quoted_lines)
        return "> %s:\n%s\n" % (forward_reference.sender, quoted_text)

    @staticmethod
    def forward_fallback_body(forward_reference: XmppForwardReference) -> str:
        parts = []
        body = forward_reference.body.strip()
        if body:
            parts.append(body)
        for item in forward_reference.media:
            url = str(getattr(item, "url", "") or "").strip()
            if url and url not in body:
                parts.append(url)
        return "\n".join(parts)

    @staticmethod
    def reply_fallback_prefix(reply_reference: XmppReplyReference) -> str:
        quoted_lines = reply_reference.body.splitlines() or [reply_reference.body]
        quoted_text = "\n".join("> %s" % line for line in quoted_lines)
        return "> %s:\n%s\n" % (reply_reference.sender, quoted_text)

    @staticmethod
    def strip_reply_fallback_body(body: str) -> str:
        if not body.startswith("> "):
            return body
        lines = body.splitlines()
        index = 0
        while index < len(lines) and lines[index].startswith("> "):
            index += 1
        if index >= len(lines):
            return body
        return "\n".join(lines[index:]).lstrip("\n")

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
