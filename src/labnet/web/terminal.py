"""A terminal session on a lab node, for the browser.

The browser cannot run ``incus exec`` itself, so the backend does it and
relays the bytes. For the shell on the node to behave like a real terminal
(colours, line editing, full-screen programs such as ``nano`` or ``vtysh``),
the ``incus`` client must believe it is attached to one. A pseudo-terminal
(PTY) provides that: a pair of connected file descriptors where one end looks
like a terminal to the program and the other end is ours to read and write.

    browser (xterm.js) <--WebSocket--> backend <--PTY--> incus exec <--> shell on node

This is the same command ``labnet shell`` runs, so the web terminal and the
CLI console behave identically.
"""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios


class NodeTerminal:
    """One running ``incus exec`` attached to a PTY."""

    def __init__(self, argv: list[str], rows: int = 24, cols: int = 80) -> None:
        # ``fd`` is our end of the PTY; the child gets the other end as its
        # standard input, output and error.
        self.fd, child_end = pty.openpty()
        self._set_size(rows, cols)
        self.process = subprocess.Popen(  # noqa: S603 - argv is built by the engine
            argv,
            stdin=child_end,
            stdout=child_end,
            stderr=child_end,
            start_new_session=True,
            close_fds=True,
        )
        os.close(child_end)
        self._closed = False

    def read(self, size: int = 65536) -> bytes:
        """Read what the node printed. Returns ``b""`` when the session has ended."""
        try:
            return os.read(self.fd, size)
        except OSError:
            # Reading a PTY whose other end has closed raises EIO on Linux.
            return b""

    def write(self, data: bytes) -> None:
        """Send keystrokes to the node."""
        while data and not self._closed:
            try:
                written = os.write(self.fd, data)
            except OSError:
                return
            data = data[written:]

    def resize(self, rows: int, cols: int) -> None:
        """Tell the node the browser terminal's size changed."""
        if self._closed:
            return
        self._set_size(rows, cols)
        # The PTY is not the child's controlling terminal, so the kernel does
        # not notify it of the new size by itself. Send the signal ourselves;
        # the incus client then passes the size on to the node.
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGWINCH)

    def close(self) -> None:
        """End the session and release the PTY."""
        if self._closed:
            return
        self._closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        os.close(self.fd)

    def _set_size(self, rows: int, cols: int) -> None:
        rows = max(2, min(int(rows), 500))
        cols = max(2, min(int(cols), 1000))
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
