from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union, Any, Callable
    from .connection import Connection
    from .request import Request

    MessageOrRequest = Union[Any, Request]

    MessageCallback = Callable[[MessageOrRequest], None]
    ConnectionCallback = Callable[[Connection], None]
    ConnectionCloseCallback = Callable[[], None]
