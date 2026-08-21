# module-transport-telegram

Minimal Xabber Server module package for `xmpp-transport-telegram`.

The module exists only because a plain XEP-0114 external component cannot manage
a user's roster directly. It should accept a narrow trusted IQ API from the
configured transport component and perform roster push through Xabber Server
roster APIs.

Group, member, message, avatar, archive, carbons, media, and fanout logic belongs
in the Python `xmpp-transport-telegram` component or existing Xabber protocol
paths.

## Build

```bash
cp .env.example .env
make
```

The installable archive should be written to:

```text
build/module_mod_transport_telegram_00.01.tar.gz
```

## IQ API

Namespace:

```text
urn:xabber:transport:telegram:1
```

Supported operations:

- `add-roster-contact`
- `rename-roster-contact`
- `remove-roster-contact`

Unknown operations must be rejected. No group, message, archive, avatar, media,
or fanout operations belong in this module by default.
