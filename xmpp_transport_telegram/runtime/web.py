from pathlib import Path

from aiohttp import web


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "xmpp-transport-telegram"})


def create_app(qr_storage_dir: str, avatar_storage_dir: str) -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    qr_dir = Path(qr_storage_dir)
    qr_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/qr/", qr_dir, name="login_qr", show_index=False)
    avatar_dir = Path(avatar_storage_dir)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/avatar/", avatar_dir, name="avatars", show_index=False)
    return app
