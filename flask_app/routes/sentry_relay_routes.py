"""Sentry tunnel relay — ad-blocker bypass for browser telemetry.

The browser Sentry SDK is configured with ``tunnel: '<api>/api/event-relay'``,
so envelopes (errors, session replays, user-feedback reports) are POSTed to this
same-origin route instead of directly to ``*.ingest.sentry.io``. Ad/tracker
blockers (uBlock, Brave shields, etc.) routinely block the Sentry domain, which
silently drops exactly the early-user feedback + replays we want — routing
through our own origin avoids that.

We forward the raw envelope to Sentry ingest server-side. To avoid running an
open relay, we only forward to the org ingest host configured via ``SENTRY_DSN``
and only to numeric project ids. See
https://docs.sentry.io/platforms/javascript/troubleshooting/#dealing-with-ad-blockers
"""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request

from core import feature_flags

from ..extensions import limiter

logger = logging.getLogger(__name__)

sentry_relay_bp = Blueprint('sentry_relay', __name__)

# Sentry SaaS ingest hosts look like ``o<org-id>.ingest.<region>.sentry.io``.
_SENTRY_SAAS_HOST = re.compile(r'^o\d+\.ingest\.[a-z0-9-]+\.sentry\.io$')


def _is_allowed_ingest_host(host: str | None) -> bool:
    """Open-relay guard: which ingest hosts we'll forward to.

    Prefer the exact host from our own ``SENTRY_DSN`` (tightest). If the backend
    DSN isn't configured (e.g. a frontend-only Sentry deploy), still accept
    Sentry SaaS ingest hosts so the browser tunnel's telemetry isn't silently
    dropped. (Self-hosted Sentry: set ``SENTRY_DSN`` so its host is allowed.)
    """
    if not host:
        return False
    configured = urlparse(os.environ.get('SENTRY_DSN', '')).hostname
    if configured:
        return host == configured
    return bool(_SENTRY_SAAS_HOST.match(host))


@sentry_relay_bp.route('/api/event-relay', methods=['POST'])
@limiter.exempt  # replay can emit many envelopes/min — must not hit the default cap
def sentry_event_relay():
    """Forward a Sentry envelope from the browser SDK to Sentry ingest."""
    body = request.get_data()
    if not body:
        return ('', 400)

    # The envelope is newline-delimited; its first line is the header JSON, which
    # carries the originating DSN (public key + ingest host + project id).
    try:
        header = json.loads(body.split(b'\n', 1)[0])
        parsed = urlparse(header['dsn'])
        host = parsed.hostname
        project_id = parsed.path.strip('/')
    except Exception:
        logger.warning('[sentry-relay] malformed envelope header')
        return ('', 400)

    # Only forward to an allowed Sentry ingest host + a numeric project id.
    if not _is_allowed_ingest_host(host) or not project_id.isdigit():
        logger.warning('[sentry-relay] rejected dsn host=%s project=%s', host, project_id)
        return ('', 403)

    try:
        resp = requests.post(
            f'https://{host}/api/{project_id}/envelope/',
            data=body,
            headers={'Content-Type': 'application/x-sentry-envelope'},
            timeout=5,
        )
        return (resp.content, resp.status_code, {'Content-Type': 'application/json'})
    except requests.RequestException as e:
        logger.warning('[sentry-relay] forward to ingest failed: %s', e)
        return ('', 502)


@sentry_relay_bp.route('/api/feature-flags', methods=['GET'])
@limiter.exempt  # cheap static-ish read, fetched once per app load (incl. guests)
def client_feature_flags():
    """Resolved values of the ``client_exposed`` flags for the browser.

    The frontend attaches these to the Sentry scope so errors / replays / bug
    reports show which player-facing flags were active. Only flags explicitly
    marked ``client_exposed`` in the registry are published here.
    """
    return jsonify(feature_flags.client_snapshot())
