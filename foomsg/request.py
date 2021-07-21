import dataclasses
from typing import Any


@dataclasses.dataclass
class Request:
    body: Any
    id: str
