"""Hop onto the AppKit main thread from any thread."""

from __future__ import annotations

from collections.abc import Callable


def main_thread(fn: Callable, *args) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn, *args)
