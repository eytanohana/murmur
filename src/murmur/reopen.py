"""Getting back to Settings: a second launch asks the running copy to open its window.

Launching murmur again through LaunchServices (Spotlight, Finder, `open -a murmur`) reaches
the running copy as a reopen event. Executing the binary directly starts a second process
instead; that one posts the notification below and exits. The notification's object is the
instance lock path, so only the copy that owns that lock reacts (a scratch copy with its own
HOME never wakes up the copy you actually use).
"""

from __future__ import annotations

import time

OPEN_SETTINGS_NOTIFICATION = "com.eytanohana.murmur.open-settings"


def route_open_request(busy: bool) -> bool:
    """Open Settings for a request from a second launch, unless a dictation is running."""
    return not busy


def should_welcome(first_launch: bool, selftest: bool) -> bool:
    """Open Settings once, on the very first launch (there the user grants permissions)."""
    return first_launch and not selftest


def post_open_request(scope: str, settle_s: float = 0.3) -> None:  # pragma: no cover - system call
    """Tell the running copy (identified by `scope`) to open Settings."""
    from Foundation import NSDistributedNotificationCenter

    NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
        OPEN_SETTINGS_NOTIFICATION, scope, None, True
    )
    time.sleep(settle_s)  # the post is asynchronous; exiting at once could drop it
