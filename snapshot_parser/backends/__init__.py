from .base import (
    CUSTOM_BACKENDS,
    ActionResult,
    Backend,
    BackendUnavailable,
    WindowInfo,
    list_backends,
    register_backend,
    resolve_backend,
)
from .fixture import FixtureBackend

__all__ = [
    "ActionResult",
    "Backend",
    "BackendUnavailable",
    "WindowInfo",
    "FixtureBackend",
    "list_backends",
    "register_backend",
    "CUSTOM_BACKENDS",
    "resolve_backend",
    "WindowsUiaBackend",
    "LinuxX11Backend",
]


def __getattr__(name):
    # imported lazily so a missing pywinauto / libX11 never breaks `import`
    if name == "WindowsUiaBackend":
        from .windows_uia import WindowsUiaBackend

        return WindowsUiaBackend
    if name == "LinuxX11Backend":
        from .linux_x11 import LinuxX11Backend

        return LinuxX11Backend
    raise AttributeError(name)
