from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union, Any, Callable, Awaitable
    from .connection import Connection
    from .request import Request

    MessageOrRequest = Union[Any, Request]

    MessageCallback = Union[Callable[[MessageOrRequest], None], Awaitable[Any]]
    ConnectionCallback = Callable[[Connection], None]
    ConnectionCloseCallback = Callable[[], None]
    LifecycleCallback = Callable[[bool], None]
