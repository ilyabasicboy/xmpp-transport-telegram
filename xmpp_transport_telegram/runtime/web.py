from pathlib import Path

from aiohttp import web


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "xmpp-transport-telegram"})


async def avatar(request: web.Request) -> web.StreamResponse:
    filename = request.match_info["filename"]
    if not filename.endswith(".jpg"):
        raise web.HTTPNotFound()
    content_hash = filename[:-4]
    if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
        raise web.HTTPNotFound()
    repository = request.app["repository"]
    row = await repository.get_avatar_file(content_hash)
    if row is None:
        raise web.HTTPNotFound()
    avatar_dir = request.app["avatar_storage_dir"]
    path = avatar_dir / row["relative_path"]
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(
        path,
        headers={
            "Content-Type": row["mime_type"],
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


async def media(request: web.Request) -> web.StreamResponse:
    handler = request.app.get("media_handler")
    if handler is None:
        raise web.HTTPNotFound()
    return await handler(request)


def create_app(qr_storage_dir: str, avatar_storage_dir: str, repository, media_handler=None) -> web.Application:
    app = web.Application()
    app["repository"] = repository
    app["avatar_storage_dir"] = Path(avatar_storage_dir)
    app["media_handler"] = media_handler
    app.router.add_get("/health", health)
    qr_dir = Path(qr_storage_dir)
    qr_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/qr/", qr_dir, name="login_qr", show_index=False)
    avatar_dir = Path(avatar_storage_dir)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_get("/avatar/{filename}", avatar)
    app.router.add_get("/media/{token}/{filename}", media)
    return app
