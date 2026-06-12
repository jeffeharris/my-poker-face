"""Notification channel interface.

A channel delivers a :class:`Notification` to one device token on one platform.
Keeping this abstract lets the dispatcher fan a turn alert out to whatever
channels a user has registered (APNs today; FCM / web / email later) without
knowing their transport details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Notification:
    """A platform-agnostic push payload.

    ``data`` carries the deep-link context (e.g. ``{'game_id': ...}``) the client
    uses to open straight to the relevant screen.
    """

    title: str
    body: str
    data: dict = field(default_factory=dict)


class NotificationChannel(ABC):
    """Delivers notifications to a single platform's devices."""

    #: platform tag matching ``user_devices.platform`` (e.g. ``'ios'``).
    platform: str = ''

    @property
    def configured(self) -> bool:
        """Whether this channel has the credentials it needs to actually send.

        An unconfigured channel is a safe no-op (returns delivered) so missing
        APNs keys in dev never break gameplay.
        """
        return True

    @abstractmethod
    def send(self, token: str, notification: Notification) -> bool:
        """Deliver to ``token``.

        Returns True if delivered (or safely skipped), and False ONLY when the
        token is permanently invalid and should be pruned (e.g. APNs 410
        Unregistered). Transient failures return True — best-effort, never raise.
        """
        raise NotImplementedError
