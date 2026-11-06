"""Use explicit Unicode output when a Windows pipe has a legacy code page."""
import sys


def utf8_console():
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if configure is not None:
            configure(encoding="utf-8", errors="replace")
