"""Push notification layer for async-friends mode.

A small channel abstraction so "it's your turn" can reach a player while the app
is closed. APNs (iOS) is the only channel implemented now; Android (FCM), web
push, and email slot in as additional ``NotificationChannel`` subclasses behind
the same dispatcher.
"""

from .channel import Notification, NotificationChannel

__all__ = ['Notification', 'NotificationChannel']
