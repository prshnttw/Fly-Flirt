"""Fly//Flirt: a live chat where a real fruit-fly connectome reads the conversation."""
from __future__ import annotations

import logging
import os
import secrets
import time

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import ROOT_DIR, Config
from .extensions import socketio
from .services import build_services

__all__ = ["create_app", "socketio"]

log = logging.getLogger("flyflirt")

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)


def _configure_logging(config) -> None:
    level = logging.DEBUG if getattr(config, "DEBUG", False) else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("werkzeug", "engineio", "socketio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(config=None) -> Flask:
    config = config or Config
    _configure_logging(config)

    app = Flask(
        __name__,
        template_folder=os.path.join(ROOT_DIR, "templates"),
        static_folder=os.path.join(ROOT_DIR, "static"),
    )
    app.config.from_object(config)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    if not app.config.get("SECRET_KEY"):
        if config.ENV == "production":
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("SECRET_KEY not set: using an ephemeral key (sessions reset on restart)")

    if config.TRUST_PROXY_HOPS:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.TRUST_PROXY_HOPS, x_proto=config.TRUST_PROXY_HOPS,
                                x_host=config.TRUST_PROXY_HOPS)

    services = build_services(config)
    app.extensions["flyflirt"] = services
    app.jinja_env.globals.update(
        asset_v=str(int(time.time())),
        min_for_verdict=config.MIN_MESSAGES_FOR_VERDICT,
        match_timeout=config.MATCH_TIMEOUT_S,
        retention_days=config.RETENTION_DAYS,
    )

    origins = [o.strip() for o in config.ALLOWED_ORIGINS.split(",")] if config.ALLOWED_ORIGINS else None
    # `socketio` is a module-level singleton; drop handlers bound to any earlier app/service
    # container so calling create_app() repeatedly (tests, reloaders) never mixes state.
    socketio.handlers = []
    socketio.namespace_handlers = []
    socketio.init_app(
        app,
        async_mode="threading",
        cors_allowed_origins=origins,
        ping_interval=20,
        ping_timeout=30,
        max_http_buffer_size=16 * 1024,
        logger=False,
        engineio_logger=False,
    )

    from .sockets import register_socket_handlers, start_background_loops
    from .web import admin_bp, api_bp, pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(admin_bp)
    register_socket_handlers(socketio, services)
    if not app.config.get("TESTING"):
        start_background_loops(socketio, services)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response

    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    def _error(status: int, title: str, message: str):
        if _wants_json():
            return jsonify(error=title.lower().replace(" ", "_"), message=message), status
        return render_template("error.html", status=status, title=title, message=message), status

    @app.errorhandler(404)
    def not_found(_):
        return _error(404, "Not found", "That page or chat does not exist (or has expired).")

    @app.errorhandler(403)
    def forbidden(_):
        return _error(403, "Private chat", "This conversation belongs to someone else.")

    @app.errorhandler(413)
    def too_large(_):
        return _error(413, "Too large", "That request was too large.")

    @app.errorhandler(429)
    def too_many(_):
        return _error(429, "Slow down", "Too many requests. Please wait a moment and try again.")

    @app.errorhandler(500)
    def server_error(exc):
        log.exception("unhandled error: %s", exc)
        return _error(500, "Something broke", "We hit an unexpected error. Please try again.")

    return app
