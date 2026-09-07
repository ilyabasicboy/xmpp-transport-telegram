from xml.etree import ElementTree as ET

from xmpp_transport_telegram.core.qr_store import StoredQrImage
from xmpp_transport_telegram.xmpp.message_xml import XmppMessageXml
from xmpp_transport_telegram.xmpp.models import XmppForwardReference, XmppOutgoingMedia
from xmpp_transport_telegram.xmpp.namespaces import FILES_NS, FORWARDED_NS, VOICE_MESSAGE_NS, XABBER_REFERENCES_NS


class FakeJid:
    def __init__(self, bare):
        self.bare = bare


class FakeMessage:
    def __init__(self, body, to_jid, xml):
        self.xml = xml
        self.values = {
            "body": body,
            "to": FakeJid(to_jid),
        }

    def __getitem__(self, key):
        return self.values.get(key, "")


def test_body_with_media_references_adds_svg_file_sharing():
    image = StoredQrImage(
        url="https://transport.example/qr/telegram-login-qr-test.svg",
        name="telegram-login-qr-test.svg",
        mime_type="image/svg+xml",
        size=123,
    )

    body, references = XmppMessageXml.body_with_media_references("Scan QR", (image,))

    assert "https://transport.example/qr/telegram-login-qr-test.svg" in body
    assert len(references) == 1
    xml = references[0]
    assert xml.attrib["type"] == "mutable"
    assert xml.find(".//file") is not None
    assert xml.find(".//media-type").text == "image/svg+xml"
    assert xml.find(".//name").text == "telegram-login-qr-test.svg"
    assert xml.find(".//uri").text == image.url


def test_extract_media_references_reads_xabber_file_sharing():
    xml = ET.Element("message")
    reference = ET.SubElement(xml, "{%s}reference" % XABBER_REFERENCES_NS)
    file_sharing = ET.SubElement(reference, "{%s}file-sharing" % FILES_NS)
    file_el = ET.SubElement(file_sharing, "file")
    ET.SubElement(file_el, "media-type").text = "image/jpeg"
    ET.SubElement(file_el, "name").text = "photo.jpg"
    ET.SubElement(file_el, "size").text = "1234"
    sources = ET.SubElement(file_sharing, "sources")
    ET.SubElement(sources, "uri").text = "https://xabber.example/gallery/photo.jpg"
    msg = FakeMessage("", "chat-100@telegram.example.com", xml)

    media = XmppMessageXml.extract_media_references(msg)

    assert len(media) == 1
    assert media[0].url == "https://xabber.example/gallery/photo.jpg"
    assert media[0].name == "photo.jpg"
    assert media[0].mime_type == "image/jpeg"
    assert media[0].size == 1234


def test_body_with_media_references_wraps_voice_message():
    voice = XmppOutgoingMedia(
        url="https://transport.example/media/token/telegram-901.webm",
        name="telegram-901.webm",
        mime_type="audio/webm;codecs=opus",
        duration=3,
        voice=True,
    )

    body, references = XmppMessageXml.body_with_media_references("", (voice,))

    assert body == voice.url
    assert len(references) == 1
    voice_message = references[0].find("{%s}voice-message" % VOICE_MESSAGE_NS)
    assert voice_message is not None
    assert voice_message.find("{%s}file-sharing" % FILES_NS) is not None
    assert references[0].find("{%s}file-sharing" % FILES_NS) is None
    assert references[0].find(".//media-type").text == "audio/webm;codecs=opus"
    assert references[0].find(".//duration").text == "3"


def test_extract_media_references_reads_xabber_voice_message():
    xml = ET.Element("message")
    reference = ET.SubElement(xml, "{%s}reference" % XABBER_REFERENCES_NS)
    voice_message = ET.SubElement(reference, "{%s}voice-message" % VOICE_MESSAGE_NS)
    file_sharing = ET.SubElement(voice_message, "{%s}file-sharing" % FILES_NS)
    file_el = ET.SubElement(file_sharing, "file")
    ET.SubElement(file_el, "media-type").text = "audio/ogg"
    ET.SubElement(file_el, "name").text = "voice.ogg"
    ET.SubElement(file_el, "duration").text = "4"
    sources = ET.SubElement(file_sharing, "sources")
    ET.SubElement(sources, "uri").text = "https://xabber.example/gallery/voice.ogg"
    msg = FakeMessage("", "chat-100@telegram.example.com", xml)

    media = XmppMessageXml.extract_media_references(msg)

    assert len(media) == 1
    assert media[0].url == "https://xabber.example/gallery/voice.ogg"
    assert media[0].mime_type == "audio/ogg"
    assert media[0].duration == 4
    assert media[0].voice


def test_extract_body_media_urls_reads_gallery_fallback_and_strips_body():
    body = "caption\nhttps://xabber.example/gallery/photo.jpg"

    media = XmppMessageXml.extract_body_media_urls(body, ())
    normalized = XmppMessageXml.strip_media_fallback_body(body, media)

    assert len(media) == 1
    assert media[0].name == "photo.jpg"
    assert media[0].mime_type == "image/jpeg"
    assert normalized == "caption"


def test_body_with_forward_references_adds_xabber_forward_payload():
    body, references = XmppMessageXml.body_with_forward_references(
        "comment",
        (
            XmppForwardReference(
                message_id="777",
                body="Forwarded text",
                sender="chat-200@telegram.example.com",
                recipient="user@example.com",
            ),
        ),
    )

    assert body == "> Forwarded text\ncomment"
    assert len(references) == 1
    reference = references[0]
    assert reference.attrib["type"] == "mutable"
    forwarded = reference.find("{%s}forwarded" % FORWARDED_NS)
    assert forwarded is not None
    message = forwarded.find("{jabber:client}message")
    assert message.attrib["from"] == "chat-200@telegram.example.com"
    assert message.attrib["id"] == "777"
    assert message.findtext("{jabber:client}body") == "Forwarded text"


def test_forward_fallback_and_media_ranges_use_body_offsets():
    forward = XmppForwardReference(
        message_id="777",
        body="⚡️Forwarded > text\nsecond line",
        sender="chat-200@telegram.example.com",
        recipient="user@example.com",
    )
    image = XmppOutgoingMedia(
        url="https://transport.example/media/token/photo.jpg",
        name="photo.jpg",
        mime_type="image/jpeg",
    )

    body, forward_references = XmppMessageXml.body_with_forward_references("", (forward,))
    body, media_references = XmppMessageXml.body_with_media_references(body, (image,))

    assert body.endswith(image.url)
    assert forward_references[0].attrib["begin"] == "0"
    assert forward_references[0].attrib["end"] == str(XmppMessageXml.body_range_len("> ⚡️Forwarded > text\n> second line\n"))
    assert media_references[0].attrib["begin"] == str(XmppMessageXml.body_range_len(body[: body.index(image.url)]))
    assert media_references[0].attrib["end"] == str(XmppMessageXml.body_range_len(body))
    assert XmppMessageXml.strip_escaped_ranges(
        body,
        [
            (
                int(forward_references[0].attrib["begin"]),
                int(forward_references[0].attrib["end"]),
            ),
            (
                int(media_references[0].attrib["begin"]),
                int(media_references[0].attrib["end"]),
            ),
        ],
    ) == ""


def test_extract_forwarded_body_and_references_removes_forward_fallback():
    body, references = XmppMessageXml.body_with_forward_references(
        "comment",
        (
            XmppForwardReference(
                message_id="777",
                body="Forwarded text",
                sender="chat-200@telegram.example.com",
                recipient="user@example.com",
            ),
        ),
    )
    xml = ET.Element("message")
    for reference in references:
        xml.append(reference)
    msg = FakeMessage(body, "chat-100@telegram.example.com", xml)

    normalized_body, forward_references = XmppMessageXml.extract_forwarded_body_and_references(msg, body)

    assert normalized_body == "comment"
    assert len(forward_references) == 1
    assert forward_references[0].message_id == "777"
    assert forward_references[0].body == "Forwarded text"
    assert forward_references[0].sender == "chat-200@telegram.example.com"


def test_extract_forwarded_reference_without_fallback_range_from_same_chat():
    xml = ET.Element("message")
    reference = ET.SubElement(xml, "{%s}reference" % XABBER_REFERENCES_NS, {"type": "mutable"})
    forwarded = ET.SubElement(reference, "{%s}forwarded" % FORWARDED_NS)
    forwarded_message = ET.SubElement(
        forwarded,
        "{jabber:client}message",
        {
            "from": "chat-100@telegram.example.com",
            "to": "user@example.com",
            "id": "777",
        },
    )
    ET.SubElement(forwarded_message, "{jabber:client}body").text = "?"
    msg = FakeMessage("?", "chat-100@telegram.example.com", xml)

    normalized_body, forward_references = XmppMessageXml.extract_forwarded_body_and_references(msg, "?")

    assert normalized_body == ""
    assert len(forward_references) == 1
    assert forward_references[0].message_id == "777"
    assert forward_references[0].body == "?"


def test_extract_forwarded_reference_strips_sender_jid_fallback_body():
    xml = ET.Element("message")
    reference = ET.SubElement(xml, "{%s}reference" % XABBER_REFERENCES_NS, {"type": "mutable"})
    forwarded = ET.SubElement(reference, "{%s}forwarded" % FORWARDED_NS)
    forwarded_message = ET.SubElement(
        forwarded,
        "{jabber:client}message",
        {
            "from": "admin@example.com",
            "to": "telegramg-75736572406578616d706c652e636f6d--100500@example.com",
            "id": "xabber-group-1",
        },
    )
    ET.SubElement(forwarded_message, "{jabber:client}body").text = "еуые1"
    body = "admin@example.com:\nеуые1"
    msg = FakeMessage(body, "chat-100@telegram.example.com", xml)

    normalized_body, forward_references = XmppMessageXml.extract_forwarded_body_and_references(msg, body)

    assert normalized_body == ""
    assert len(forward_references) == 1
    assert forward_references[0].message_id == "xabber-group-1"
    assert forward_references[0].body == "еуые1"
