import asyncio
from xml.etree import ElementTree as ET

from xmpp_transport_telegram.core.commands import ControlResponse
from xmpp_transport_telegram.runtime.config import Settings
from xmpp_transport_telegram.xmpp import component as component_module
from xmpp_transport_telegram.xmpp.component import TelegramCommandComponent, XmppComponent
from xmpp_transport_telegram.xmpp.namespaces import GROUPS_NS


def make_settings():
    return Settings(
        database_url="postgresql://example",
        session_encryption_key="key",
        xmpp_component_jid="telegram.example.com",
        xmpp_component_secret="secret",
        xmpp_component_host="127.0.0.1",
        xmpp_component_port=5238,
        xmpp_component_connect_timeout=1,
        xmpp_component_retry_interval=0,
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_storage_dir="data/telegram_sessions",
        transport_server_domain="example.com",
        transport_pid_file="run/transport.pid",
        health_host="127.0.0.1",
        health_port=8089,
        qr_storage_dir="data/login_qr",
        qr_base_url="http://127.0.0.1:8089",
        avatar_storage_dir="data/avatars",
        avatar_base_url="http://127.0.0.1:8089",
        log_level="INFO",
        log_file="",
        log_max_bytes=10485760,
        log_backup_count=5,
    )


class FakeComponentClient:
    def __init__(self, _settings, _command_handler, _direct_message_handler):
        self.connect_count = 0
        self.disconnect_count = 0
        self.connected = False

    async def connect(self, _host, _port):
        self.connect_count += 1
        self.connected = True

    async def wait_until_ready(self, _timeout):
        if self.connect_count == 1:
            raise asyncio.TimeoutError()

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.disconnect_count += 1
        self.connected = False


class FakeMessage:
    def __init__(self, from_jid, to_jid, body, message_type="chat", xml=None, message_id="xmpp-1"):
        self.values = {
            "from": from_jid,
            "to": to_jid,
            "body": body,
            "type": message_type,
            "id": message_id,
        }
        self.xml = xml if xml is not None else ET.Element("message")

    def __getitem__(self, key):
        return self.values[key]


def test_xmpp_component_start_retries_after_ready_timeout(monkeypatch):
    async def run_test():
        monkeypatch.setattr(component_module, "TelegramCommandComponent", FakeComponentClient)

        async def command_handler(_from_jid, _body, _notify):
            raise AssertionError("not used")

        xmpp = XmppComponent(make_settings(), command_handler)

        await xmpp.start()

        assert xmpp.client.connect_count == 2
        assert xmpp.client.disconnect_count == 1
        assert xmpp.client.connected is True

    asyncio.run(run_test())


def test_xabber_group_service_message_to_bot_does_not_run_command_handler():
    async def run_test():
        command_calls = []

        async def command_handler(_from_jid, _body, _notify):
            command_calls.append((_from_jid, _body))
            return ControlResponse("unexpected")

        async def direct_message_handler(_message):
            raise AssertionError("direct handler should not be called")

        client = TelegramCommandComponent(
            make_settings(),
            command_handler,
            direct_message_handler,
        )
        await client._handle_message_async(
            FakeMessage(
                from_jid="telegramg-74657374406578616d706c652e636f6d--5386493808@example.com/Group",
                to_jid="bot@telegram.example.com",
                body="test@example.com joined the group.",
            )
        )

        assert command_calls == []

    asyncio.run(run_test())


def test_xabber_group_user_message_to_bot_runs_direct_handler():
    async def run_test():
        direct_calls = []

        async def command_handler(_from_jid, _body, _notify):
            raise AssertionError("command handler should not be called")

        async def direct_message_handler(message):
            direct_calls.append(message)

        x = ET.Element("{%s}x" % GROUPS_NS)
        user = ET.SubElement(x, "{%s}user" % GROUPS_NS)
        jid = ET.SubElement(user, "jid")
        jid.text = "test@example.com"
        xml = ET.Element("message")
        xml.append(x)

        client = TelegramCommandComponent(
            make_settings(),
            command_handler,
            direct_message_handler,
        )
        await client._handle_message_async(
            FakeMessage(
                from_jid="telegramg-74657374406578616d706c652e636f6d--5386493808@example.com/Group",
                to_jid="bot@telegram.example.com",
                body="hello tg",
                xml=xml,
            )
        )

        assert len(direct_calls) == 1
        assert direct_calls[0].sender == "telegramg-74657374406578616d706c652e636f6d--5386493808@example.com"
        assert direct_calls[0].recipient == "bot@telegram.example.com"
        assert direct_calls[0].body == "hello tg"
        assert direct_calls[0].group_sender_jid == "test@example.com"

    asyncio.run(run_test())
