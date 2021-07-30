from __future__ import annotations

from typing import Any, Set, Callable, Awaitable, TYPE_CHECKING

import footron_protocol as protocol

from .errors import LockStateError
if TYPE_CHECKING:
    from .types import MessageCallback, MessageOrRequest, ConnectionCloseCallback
    from .client import MessagingClient


class Connection:
    """Public connection interface"""

    _connection: _Connection

    def __init__(self, _connection: _Connection):
        self._connection = _connection

    @property
    def id(self):
        return self._connection.id

    @property
    def paused(self):
        return self._connection.paused

    def accept(self):
        return self._connection.accept()

    def send_message(self, body: Any, request_id: str = None):
        # TODO(vinhowe): Is this a bad way of constraining an interface? I manually
        #  redefined the function signatures because IDE completion didn't work any
        #  other way I tried (at least in PyCharm).
        return self._connection.send_message(body, request_id)

    def add_message_listener(self, callback: MessageCallback):
        return self._connection.add_message_listener(callback)

    def remove_message_listener(self, callback: MessageCallback):
        return self._connection.remove_message_listener(callback)

    def add_close_listener(self, callback: ConnectionCloseCallback):
        return self._connection.add_close_listener(callback)

    def remove_close_listener(self, callback: ConnectionCloseCallback):
        return self._connection.remove_close_listener(callback)


_SendProtocolMessageCallback = Callable[[protocol.BaseMessage], Awaitable[None]]


class _Connection:
    """Internally visible connection object"""

    id: str
    paused: bool
    accepted: bool

    _messaging_client: MessagingClient
    _send_protocol_message: _SendProtocolMessageCallback

    _message_listeners: Set[MessageCallback]
    _close_listeners: Set[ConnectionCloseCallback]

    def __init__(
        self,
        id: str,
        accepted: bool,
        messaging_client: MessagingClient,
        # TODO: It seems like passing in a private function from the containing class
        #  isn't the best solution here. We should find a cleaner one.
        send_protocol_message_fn: _SendProtocolMessageCallback,
        paused: bool = False,
    ):
        self.id = id
        self.accepted = accepted
        self._messaging_client = messaging_client
        self._send_protocol_message = send_protocol_message_fn
        self.paused = paused

        self._message_listeners = set()
        self._close_listeners = set()

    #
    # Access methods
    #

    async def accept(self):
        await self._update_access(True)
        if not self._messaging_client.has_initial_state:
            await self.send_empty_initial_message()

    async def deny(self, reason: str = None):
        await self._update_access(False, reason=reason)

    async def _update_access(self, accepted: bool, reason: str = None):
        if not self._messaging_client.lock:
            raise LockStateError(
                "A lock is required to set access for client connections"
            )

        await self._send_protocol_message(
            protocol.AccessMessage(accepted=accepted, client=self.id, reason=reason)
        )
        self.accepted = True

    #
    # Message methods
    #

    async def send_message(
        self, body: Any, request_id: str = None, ignore_paused: bool = False
    ):
        if not self.accepted:
            raise protocol.AccessError(
                "Attempted to send a message to a client that hasn't been accepted"
            )

        if self.paused and not ignore_paused:
            return

        await self._send_protocol_message(
            protocol.ApplicationAppMessage(body=body, req=request_id, client=self.id)
        )

    async def send_empty_initial_message(self):
        await self.send_message({"__start": ""})

    #
    # Message listener handling
    #

    def add_message_listener(self, callback: MessageCallback):
        self._message_listeners.add(callback)

    def remove_message_listener(self, callback: MessageCallback):
        self._message_listeners.remove(callback)

    def clear_message_listeners(self):
        self._message_listeners.clear()

    def notify_message_listeners(self, message: MessageOrRequest):
        [callback(message) for callback in self._message_listeners]

    #
    # Connection close listener handling
    #

    def add_close_listener(self, callback: ConnectionCloseCallback):
        self._close_listeners.add(callback)

    def remove_close_listener(self, callback: ConnectionCloseCallback):
        self._close_listeners.remove(callback)

    def clear_close_listeners(self):
        self._close_listeners.clear()

    def notify_close_listeners(self):
        [callback() for callback in self._close_listeners]
