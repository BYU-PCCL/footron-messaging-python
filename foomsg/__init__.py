import os

from .client import MessagingClient

_URL_ENV_NAME = "FT_MSG_URL"
_URL = (
    os.environ[_URL_ENV_NAME]
    if _URL_ENV_NAME in os.environ
    else "ws://localhost:8089/out"
)


class Messaging(MessagingClient):
    def __init__(self, *, url=_URL):
        super().__init__(url)


# TODO: Set up __all__
