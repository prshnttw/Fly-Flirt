"""A tiny, deliberately unadvertised admin page for reviewing reports and bans.

Disabled unless BOTH ``ADMIN_USERNAME`` and ``ADMIN_PASSWORD`` are set (Config.ADMIN_USERNAME /
Config.ADMIN_PASSWORD): with either unset, every route under here 404s, indistinguishable from the
blueprint not existing at all — nobody scanning the site finds a login prompt to attack. When it *is*
configured, it's gated by HTTP Basic Auth (only sensible over HTTPS, which Railway/Fly provide by
default) with a timing-safe comparison, and every POST here also checks a per-session CSRF token, since
a browser attaches cached Basic Auth credentials to same-origin requests automatically and a login
prompt alone wouldn't stop a forged form submission from another tab.

There is deliberately no link to this from anywhere else in the app.
"""
from __future__ import annotations

import secrets
import time

from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, session, url_for

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def services():
    return current_app.extensions["flyflirt"]


def _configured() -> bool:
    cfg = services().config
    return bool(getattr(cfg, "ADMIN_USERNAME", None)) and bool(getattr(cfg, "ADMIN_PASSWORD", None))


def _authorised() -> bool:
    cfg = services().config
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return False
    return secrets.compare_digest(auth.username, cfg.ADMIN_USERNAME) and secrets.compare_digest(auth.password, cfg.ADMIN_PASSWORD)


@admin_bp.before_request
def _guard():
    if not _configured():
        abort(404)
    if not _authorised():
        return Response("Authentication required.", 401, {"WWW-Authenticate": 'Basic realm="Fly//Flirt admin"'})


def _csrf_token() -> str:
    token = session.get("admin_csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["admin_csrf"] = token
    return token


def _check_csrf() -> bool:
    return secrets.compare_digest(request.form.get("csrf", ""), session.get("admin_csrf", "\0"))


@admin_bp.get("/")
def dashboard():
    svc = services()
    reports = svc.storage.list_reports(resolved=False, limit=200)
    for r in reports:
        r["reported_short"] = (r["reported"] or "")[:10]
        r["reporter_short"] = (r["reporter"] or "")[:10]
        r["when"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
    bans = svc.safety.list_bans()
    stats = svc.stats()
    return render_template("admin.html", reports=reports, bans=bans, stats=stats, csrf=_csrf_token())


@admin_bp.post("/reports/<int:report_id>/resolve")
def resolve(report_id: int):
    if not _check_csrf():
        abort(400)
    services().storage.resolve_report(report_id, True)
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/bans/<path:client_id>/clear")
def unban(client_id: str):
    if not _check_csrf():
        abort(400)
    services().safety.unban(client_id)
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/bans/<path:client_id>/extend")
def extend_ban(client_id: str):
    """A quick manual ban, e.g. for someone reported outside the automatic threshold."""
    if not _check_csrf():
        abort(400)
    services().safety.ban(client_id, 24 * 3600, "manually banned by admin")
    return redirect(url_for("admin.dashboard"))
