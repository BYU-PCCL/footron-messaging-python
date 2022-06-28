from __future__ import annotations

import asyncio
import json
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any, Dict, Set

import footron_protocol as protocol
import websockets
from footron_protocol import MessageType

from .connection import Connection, _Connection
from .errors import LockStateError
from .request import Request

if TYPE_CHECKING:
    from .types import ConnectionCallback, MessageCallback, MessageOrRequest


class MessagingClient:
    _message_queue: asyncio.Queue[protocol.BaseMessage] = asyncio.Queue()
    _connections: Dict[str, _Connection]
    _lock: protocol.Lock
    _has_initial_state: bool

    _socket: websockets.WebSocketClientProtocol
    _url: str

    _connection_listeners: Set[ConnectionCallback]
    _message_listeners: Set[MessageCallback]

    def __init__(self, url, has_initial_state: bool = False):
        self._url = url
        self._message_queue = asyncio.Queue()
        self._connections = {}
        self._lock = False
        self._has_initial_state = has_initial_state

        self._connection_listeners = set()
        self._message_listeners = set()

    @property
    def lock(self):
        return self._lock

    @property
    def has_initial_state(self):
        return self._has_initial_state

    @lock.setter
    async def lock(self, value: protocol.Lock):
        # TODO: Implement lock setting w/ display settings message side effect (is
        #  fire-and-forget fine?)
        raise NotImplementedError("Lock setting has not been implemented")

    def _reset_connection_state(self):
        self._connections = {}
        self._lock = False

    # TODO(vinhowe) (ASAP): we need to really iron down the "special first message" case
    #  and how to make it obvious to developers what it is and how/whether to use it.
    #  Maybe a config option in either Messaging or start()? Something like
    #  "has_initial_state: bool" (False by default) to indicate whether we want to make
    #  the client wait for a state push or just automatically send it a special "empty"
    #  message defined by us.

    # TODO: IT WILL BE IMPORTANT TO DOCUMENT that if developers want to send an initial
    #  message right after a new connection rather than just sending out periodic
    #  updates and making clients wait, they will have to use a connection listener,
    #  which is technically part of the "advanced" API that won't show up in quickstart
    #  examples.
    async def start(self):
        loop = asyncio.get_event_loop()

        async def close_ws(*, restart=False):
            print("Closing websocket", flush=True)

            self._reset_connection_state()
            try:
                await self._socket.close()
            finally:
                if restart:
                    print("Attempting to restart websocket", flush=True)
                    asyncio.create_task(self.start())

        async with websockets.connect(self._url) as self._socket:
            should_restart = True
            if (
                sys.platform in ["linux", "darwin"]
                and threading.current_thread() is threading.main_thread()
            ):

                async def exit_signal_handler():
                    # Prevent finally block from restarting websocket
                    nonlocal should_restart
                    should_restart = False
                    close_ws()

                loop.add_signal_handler(
                    signal.SIGINT, loop.create_task, exit_signal_handler()
                )
                loop.add_signal_handler(
                    signal.SIGTERM, loop.create_task, exit_signal_handler()
                )
            try:
                receive_task = asyncio.create_task(self._receive_handler())
                send_task = asyncio.create_task(self._send_handler())
                done, pending = await asyncio.wait(
                    [receive_task, send_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            finally:
                if should_restart:
                    await close_ws(restart=True)

    async def _receive_handler(self):
        async for message in self._socket:
            # TODO: Add support for binary messages
            if not isinstance(message, str):
                continue

            try:
                await self._on_message(protocol.deserialize(json.loads(message)))
            except Exception as e:
                print(e)

    async def _send_handler(self):
        while True:
            message = await self._message_queue.get()
            await self._socket.send(json.dumps(protocol.serialize(message)))

    def _check_outgoing_protocol_message(self, message: protocol.BaseMessage):
        if isinstance(message, protocol.AccessMessage) and not self._lock:
            raise LockStateError(
                "A lock is required to send access messages to clients"
            )

        if message.type not in [
            MessageType.ACCESS,
            MessageType.APPLICATION_APP,
            MessageType.DISPLAY_SETTINGS,
        ]:
            raise protocol.UnhandledMessageTypeError(
                f"Couldn't send message type '{message.type}'"
            )

    async def _send_protocol_message(self, message: protocol.BaseMessage):
        # We do an additional check because technically this is a public API
        self._check_outgoing_protocol_message(message)
        await self._message_queue.put(message)

    async def _on_message(self, message: protocol.BaseMessage):
        if isinstance(message, protocol.HeartbeatClientMessage):
            if not message.up:
                [self._remove_connection(id) for id in message.clients]
                return

            # TODO: This test might be expensive and unnecessary, consider simplifying
            #  or removing it
            await self._compare_heartbeat_up_connections(message.clients)
            return

        if isinstance(message, protocol.ConnectMessage):
            if message.client not in self._connections:
                connection = self._add_connection(message.client)
            else:
                connection = self._connections[message.client]

            if not self.has_initial_state and connection.accepted:
                await connection.send_empty_initial_message()
            return

        if hasattr(message, "client"):
            if message.client not in self._connections:
                raise protocol.AccessError(
                    f"Unauthorized client '{message.client}' attempted to send an authenticated message"
                )

        if isinstance(message, protocol.ApplicationClientMessage):
            listener_message = (
                message.body
                if message.req is None
                else Request(message.body, message.req)
            )

            await self._notify_message_listeners(listener_message)
            await self._connections[message.client].notify_message_listeners(
                listener_message
            )
            return

        if isinstance(message, protocol.LifecycleMessage):
            self._connections[message.client].paused = message.paused
            return

        raise protocol.UnhandledMessageTypeError(
            f"Couldn't handle message type '{message.type}'"
        )

    async def _compare_heartbeat_up_connections(self, connections):
        local_connections = set(self._connections)
        heartbeat_connections = set(connections)

        for client in heartbeat_connections.copy():
            if client in local_connections:
                heartbeat_connections.remove(client)
                local_connections.remove(client)
                continue

            self._add_connection(client)

        for client in local_connections.copy():
            if client in heartbeat_connections:
                heartbeat_connections.remove(client)
                local_connections.remove(client)
                continue

            # Client was not found locally
            self._remove_connection(client)

    async def send_message(
        self, message: Any, request_id: str = None, ignore_paused: bool = False
    ):
        """Send message to all existing connections"""
        await asyncio.gather(
            *[
                conn.send_message(message, request_id, ignore_paused)
                for conn in self._connections.values()
            ]
        )

    #
    # Client connection handling
    # (these methods just handle updating internal state and notifying listeners _after_
    # connections are added/removed)
    #

    def _add_connection(self, id: str) -> _Connection:
        connection = _Connection(
            id,
            accepted=not self.lock,
            messaging_client=self,
            send_protocol_message_fn=self._send_protocol_message,
        )
        self._connections[id] = connection
        self._notify_connection_listeners(connection)
        return connection

    def _remove_connection(self, id: str):
        if id not in self._connections:
            return

        self._connections[id].notify_close_listeners()
        del self._connections[id]

    #
    # Message listener handling
    #

    def add_message_listener(self, callback: MessageCallback):
        self._message_listeners.add(callback)

    def remove_message_listener(self, callback: MessageCallback):
        self._message_listeners.remove(callback)

    def _clear_message_listeners(self):
        self._message_listeners.clear()

    async def _notify_message_listeners(self, message: MessageOrRequest):
        event_loop = asyncio.get_event_loop()
        for callback in self._message_listeners:
            if asyncio.iscoroutinefunction(callback):
                event_loop.create_task(callback(message))
            else:
                callback(message)

    #
    # Connection listener handling
    #

    def add_connection_listener(self, callback: ConnectionCallback):
        self._connection_listeners.add(callback)

    def remove_connection_listener(self, callback: ConnectionCallback):
        self._connection_listeners.remove(callback)

    def _clear_connection_listeners(self):
        self._connection_listeners.clear()

    def _notify_connection_listeners(self, _connection: _Connection):
        [callback(Connection(_connection)) for callback in self._connection_listeners]
