"""TV4 connection management."""

import logging
import math
import socket
import threading


LOGGER = logging.getLogger(__name__)


def close_connection(connection):
    """Interrupt socket waits and close the connection."""
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        # Already disconnected.
        pass
    finally:
        connection.close()


class ConnectionManager:
    """Manage client workers and socket lifetimes."""

    def __init__(self, handler, *, client_timeout=30.0, max_clients=32):
        if not math.isfinite(client_timeout) or client_timeout <= 0:
            raise ValueError("client_timeout must be positive and finite")
        if isinstance(max_clients, bool) or not isinstance(max_clients, int):
            raise ValueError("max_clients must be a positive integer")
        if max_clients <= 0:
            raise ValueError("max_clients must be a positive integer")

        self.handler = handler
        self.client_timeout = client_timeout
        self.max_clients = max_clients
        self._workers = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._listener = None

    @property
    def active_count(self):
        with self._lock:
            return len(self._workers)

    def start_client(self, connection, address):
        """Start a worker or close the rejected connection."""
        with self._lock:
            if self._stopping.is_set() or len(self._workers) >= self.max_clients:
                LOGGER.warning("client_rejected address=%s", address)
                close_connection(connection)
                return False

            worker = threading.Thread(
                target=self._run_client,
                args=(connection, address),
                name=f"client-{address[0]}:{address[1]}",
                daemon=False,
            )
            try:
                connection.settimeout(self.client_timeout)
                self._workers[connection] = worker
                # Start before shutdown can join this worker.
                worker.start()
            except (OSError, RuntimeError):
                self._workers.pop(connection, None)
                close_connection(connection)
                LOGGER.exception("client_start_failed address=%s", address)
                return False

        return True

    def _run_client(self, connection, address):
        LOGGER.info("client_connected address=%s", address)
        try:
            self.handler(connection, address)
        except TimeoutError:
            if self._stopping.is_set():
                LOGGER.info("client_interrupted address=%s", address)
            else:
                LOGGER.warning("client_timeout address=%s", address)
        except (OSError, ValueError, UnicodeError) as error:
            if self._stopping.is_set():
                LOGGER.info("client_interrupted address=%s", address)
            else:
                LOGGER.warning("client_error address=%s error=%s", address, error)
        except Exception:
            LOGGER.exception("client_handler_failed address=%s", address)
        finally:
            close_connection(connection)
            with self._lock:
                self._workers.pop(connection, None)
            LOGGER.info("client_closed address=%s", address)

    def serve_forever(self, listener):
        """Accept clients until shutdown."""
        with self._lock:
            if self._stopping.is_set() or self._listener is not None:
                raise RuntimeError("ConnectionManager can serve only once")
            self._listener = listener

        try:
            listener.settimeout(0.2)
            while not self._stopping.is_set():
                try:
                    connection, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise
                self.start_client(connection, address)
        finally:
            self.shutdown()

    def shutdown(self):
        """Stop accepting, interrupt sockets and join workers."""
        with self._lock:
            self._stopping.set()
            listener = self._listener
            self._listener = None
            workers = list(self._workers.items())

        if listener is not None:
            listener.close()
        for connection, worker in workers:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            # The worker closes its socket after cleanup.
        for connection, worker in workers:
            if worker is not threading.current_thread():
                worker.join()
