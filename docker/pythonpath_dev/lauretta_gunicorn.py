# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""
Gunicorn gthread worker that cannot be hung by idle client connections.

Stock gthread has no timeout on a client connection before its first request, and
port scanners and protocol fingerprinters routinely open connections and leave
them there. Two ways this hangs the app:

* Silent connections (nothing sent). gthread parks these in its event loop until
  they become readable, and only ever expires keep-alive connections, so they stay
  forever. Each one counts against worker_connections (default 1000); at the limit
  the worker stops accepting, healthchecks included.
* Partial requests (bytes but no complete request line). These are handed to a pool
  thread that blocks in recv() with no timeout, holding the thread forever. With
  every thread held, nothing is served.

--timeout does not help (the worker is still heartbeating), nor does --keep-alive
(no request has started). This worker closes a connection that sends nothing within
SERVER_SOCKET_TIMEOUT seconds, and puts the same timeout on each socket operation of
a request, so a stalled read or write frees its thread. It is a per-operation
timeout, not a request deadline: a slow chart query spends its time in the
database, not on the socket, and is unaffected.

Selected without touching the upstream entrypoint, via the --worker-class flag
docker/entrypoints/run-server.sh already builds from the environment:

    SERVER_WORKER_CLASS=lauretta_gunicorn.TimeoutThreadWorker

A timed-out partial request is logged by gunicorn as "Socket error processing
request." with a TimeoutError traceback; silent connections are closed quietly.

This subclasses gunicorn internals, and accept() is a copy of gunicorn 23.0.0's
with one line added. After an upgrade that bumps gunicorn, diff accept() against
the new ThreadWorker.accept and confirm the startup log still reads
"Using worker: lauretta_gunicorn.TimeoutThreadWorker".
"""

from __future__ import annotations

import errno
import os
import selectors
import socket
import time
from collections import deque
from functools import partial
from typing import Any

from gunicorn.workers.gthread import TConn, ThreadWorker

SOCKET_TIMEOUT = float(os.environ.get("SERVER_SOCKET_TIMEOUT", "15"))


class TimeoutThreadWorker(ThreadWorker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Accepted connections with their first-request deadline, oldest first.
        # Only the main loop thread touches this, so it needs no lock.
        self._unread: deque[tuple[float, TConn]] = deque()

    def accept(self, server: Any, listener: socket.socket) -> None:
        try:
            sock, client = listener.accept()
            # initialize the connection object
            conn = TConn(self.cfg, sock, client, server)

            self.nr_conns += 1
            # wait until socket is readable
            with self._lock:
                self.poller.register(
                    conn.sock,
                    selectors.EVENT_READ,
                    partial(self.on_client_socket_readable, conn),
                )
            # Added: the only change from upstream.
            self._unread.append((time.monotonic() + SOCKET_TIMEOUT, conn))
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.ECONNABORTED, errno.EWOULDBLOCK):
                raise

    def murder_keepalived(self) -> None:
        super().murder_keepalived()
        # Every entry has the same timeout, so the deque is in deadline order.
        now = time.monotonic()
        while self._unread and self._unread[0][0] <= now:
            _, conn = self._unread.popleft()
            # Initialized means it became readable and was handed to a thread;
            # from there handle() and the keep-alive expiry own it.
            if conn.initialized:
                continue
            with self._lock:
                try:
                    self.poller.unregister(conn.sock)
                except (KeyError, ValueError):
                    continue
            self.nr_conns -= 1
            conn.close()

    def handle(self, conn: TConn) -> tuple[bool, TConn]:
        # TConn.init() resets the socket to fully blocking (no timeout) before
        # every request, including reused keep-alive connections, so the timeout
        # is applied here on each pass rather than once at accept time.
        conn.sock.settimeout(SOCKET_TIMEOUT)
        return super().handle(conn)
