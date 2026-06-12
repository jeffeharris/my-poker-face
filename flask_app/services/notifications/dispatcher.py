"""Notification dispatch: resolve a user's devices and deliver to each.

The single entry point is :func:`notify_turn`, which the turn-notify
orchestrator calls once per turn for an offline player. It looks up the user's
registered devices, sends via the channel matching each device's platform, and
prunes any token a channel reports as permanently dead. Best-effort throughout —
a delivery failure never propagates into gameplay.
"""

from __future__ import annotations

import logging
from typing import Dict

from flask_app import extensions

from .apns_channel import APNsChannel
from .channel import Notification, NotificationChannel

logger = logging.getLogger(__name__)

# Lazily-built channel registry keyed by platform. Cached at module level — the
# channels are cheap, stateless apart from a JWT cache, and process-wide.
_channels: Dict[str, NotificationChannel] | None = None


def get_channels() -> Dict[str, NotificationChannel]:
    global _channels
    if _channels is None:
        _channels = {'ios': APNsChannel()}
    return _channels


def set_channels_for_test(channels: Dict[str, NotificationChannel]) -> None:
    """Override the channel registry (tests inject a fake channel)."""
    global _channels
    _channels = channels


def notify_turn(game_id: str, user_id: str) -> int:
    """Push an "it's your turn" alert to every device the user has registered.

    Returns the number of devices delivered to. Dead tokens (a channel returning
    False) are pruned. Never raises.
    """
    try:
        devices = extensions.device_repo.list_devices(user_id) if extensions.device_repo else []
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[NOTIFY] device lookup failed for %s: %s", user_id, e)
        return 0
    if not devices:
        return 0

    notification = Notification(
        title="Your turn",
        body="It's your move at the poker table.",
        data={'game_id': game_id},
    )
    channels = get_channels()
    delivered = 0
    for device in devices:
        channel = channels.get(device.platform)
        if channel is None:
            continue
        try:
            ok = channel.send(device.token, notification)
        except Exception as e:  # pragma: no cover - channels are best-effort
            logger.debug("[NOTIFY] send error for %s: %s", device.platform, e)
            continue
        if ok:
            delivered += 1
        else:
            # Permanently invalid token — prune so we stop trying it.
            try:
                extensions.device_repo.remove(user_id, device.token)
            except Exception:  # pragma: no cover - defensive
                pass
    return delivered
