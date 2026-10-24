"""Only synthetic local fixtures."""
import builtins
import io
import asyncio
import ssl
import socket
import subprocess

# Load SSL's socket subclass definitions before replacing the socket constructor.
# The denial guards remain installed during every test body, including async tests.
del asyncio, ssl


def deny_external(*args, **kwargs):
    raise AssertionError("real process/socket forbidden in synthetic suite")


for name in ("Popen", "run", "call", "check_call", "check_output"):
    setattr(subprocess, name, deny_external)
socket.socket = deny_external
socket.create_connection = deny_external
_open, _io_open = builtins.open, io.open


def restricted_open(original):
    def checked(file, *args, **kwargs):
        text = str(file).replace("\\", "/").lower()
        if "/srv/clothinkai" in text or "/run/production" in text or "preserved-config" in text:
            raise AssertionError("production paths forbidden in synthetic suite")
        return original(file, *args, **kwargs)
    return checked


builtins.open = restricted_open(_open)
io.open = restricted_open(_io_open)
