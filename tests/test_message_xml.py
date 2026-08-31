from xmpp_transport_telegram.core.qr_store import StoredQrImage
from xmpp_transport_telegram.xmpp.message_xml import XmppMessageXml


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
