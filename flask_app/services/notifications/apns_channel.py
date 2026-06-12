"""APNs (Apple Push Notification service) channel — token-based (.p8) auth.

Sends "it's your turn" alerts to iOS devices over APNs' HTTP/2 API using a
provider JWT signed with an Apple Auth Key (.p8). Token auth (not certificates)
is used so there's nothing to rotate on a schedule.

Configuration (all via env; see OPS_RUNBOOK):
    APNS_KEY_PATH    path to the .p8 Auth Key
    APNS_KEY_ID      the Key ID
    APNS_TEAM_ID     the Apple Developer Team ID
    APNS_BUNDLE_ID   the app bundle id (the APNs topic)
    APNS_USE_SANDBOX '1' for the sandbox host (dev builds), else production

When unconfigured the channel is a safe no-op, so dev/CI never needs Apple
credentials. Actual delivery requires the optional ``h2`` package (HTTP/2);
without it the channel degrades to a logged no-op rather than failing play.
APNs cannot be exercised from CI or the simulator — it needs a real device, so
this implementation is verified manually (see docs/plans/ASYNC_FRIENDS_POC.md).
"""

from __future__ import annotations

import logging
import os
import time

from .channel import Notification, NotificationChannel

logger = logging.getLogger(__name__)

_PROD_HOST = 'https://api.push.apple.com'
_SANDBOX_HOST = 'https://api.sandbox.push.apple.com'
_TOKEN_TTL_SECONDS = 50 * 60  # APNs accepts a provider token for up to 1h; refresh early.


class APNsChannel(NotificationChannel):
    platform = 'ios'

    def __init__(
        self,
        *,
        key_path: str | None = None,
        key_id: str | None = None,
        team_id: str | None = None,
        bundle_id: str | None = None,
        use_sandbox: bool | None = None,
    ):
        self.key_path = key_path if key_path is not None else os.environ.get('APNS_KEY_PATH')
        self.key_id = key_id if key_id is not None else os.environ.get('APNS_KEY_ID')
        self.team_id = team_id if team_id is not None else os.environ.get('APNS_TEAM_ID')
        self.bundle_id = bundle_id if bundle_id is not None else os.environ.get('APNS_BUNDLE_ID')
        if use_sandbox is None:
            use_sandbox = os.environ.get('APNS_USE_SANDBOX', '0') == '1'
        self.host = _SANDBOX_HOST if use_sandbox else _PROD_HOST
        self._jwt_cache: tuple[str, float] | None = None  # (token, issued_at)

    @property
    def configured(self) -> bool:
        return bool(self.key_path and self.key_id and self.team_id and self.bundle_id)

    def send(self, token: str, notification: Notification) -> bool:
        if not self.configured:
            logger.debug("[APNS] not configured; skipping push to %s…", token[:8])
            return True
        try:
            return self._send(token, notification)
        except Exception as e:  # pragma: no cover - network/transport, never raise
            logger.warning("[APNS] send failed for %s…: %s", token[:8], e)
            return True  # transient — don't prune the token

    # --- internals ---

    def _provider_jwt(self) -> str:
        """A cached, periodically-refreshed APNs provider JWT (ES256 over the .p8)."""
        now = time.time()
        if self._jwt_cache and (now - self._jwt_cache[1]) < _TOKEN_TTL_SECONDS:
            return self._jwt_cache[0]
        import jwt  # PyJWT (already a project dep)

        with open(self.key_path, 'r') as f:  # type: ignore[arg-type]
            signing_key = f.read()
        token = jwt.encode(
            {'iss': self.team_id, 'iat': int(now)},
            signing_key,
            algorithm='ES256',
            headers={'kid': self.key_id},
        )
        self._jwt_cache = (token, now)
        return token

    def _send(self, device_token: str, notification: Notification) -> bool:
        import httpx  # HTTP/2 requires the optional `h2` package

        payload = {
            'aps': {
                'alert': {'title': notification.title, 'body': notification.body},
                'sound': 'default',
            },
            **(notification.data or {}),
        }
        headers = {
            'authorization': f'bearer {self._provider_jwt()}',
            'apns-topic': self.bundle_id,
            'apns-push-type': 'alert',
        }
        url = f"{self.host}/3/device/{device_token}"
        try:
            with httpx.Client(http2=True, timeout=10.0) as client:
                resp = client.post(url, json=payload, headers=headers)
        except ImportError:  # pragma: no cover - missing h2
            logger.warning("[APNS] HTTP/2 support (h2) unavailable; push skipped")
            return True
        if resp.status_code == 410 or (resp.status_code == 400 and 'BadDeviceToken' in resp.text):
            logger.info("[APNS] token unregistered (%s); pruning", resp.status_code)
            return False  # prune
        if resp.status_code >= 400:
            logger.warning("[APNS] push rejected %s: %s", resp.status_code, resp.text[:200])
        return True
