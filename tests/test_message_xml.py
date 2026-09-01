from xml.etree import ElementTree as ET

from xmpp_transport_telegram.core.qr_store import StoredQrImage
from xmpp_transport_telegram.xmpp.message_xml import XmppMessageXml
from xmpp_transport_telegram.xmpp.models import XmppForwardReference
from xmpp_transport_telegram.xmpp.namespaces import FORWARDED_NS


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

    assert body == "> chat-200@telegram.example.com:\n> Forwarded text\ncomment"
    assert len(references) == 1
    reference = references[0]
    assert reference.attrib["type"] == "mutable"
    forwarded = reference.find("{%s}forwarded" % FORWARDED_NS)
    assert forwarded is not None
    message = forwarded.find("{jabber:client}message")
    assert message.attrib["from"] == "chat-200@telegram.example.com"
    assert message.attrib["id"] == "777"
    assert message.findtext("{jabber:client}body") == "Forwarded text"


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
