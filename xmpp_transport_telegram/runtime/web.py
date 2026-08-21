from aiohttp import web


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "xmpp-transport-telegram"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    return app
