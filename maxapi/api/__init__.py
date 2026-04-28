"""MAX userbot posting gateway REST API package.

The package ships two equivalent entry points:

* The FastAPI app at :mod:`api.main` (REST gateway, see ``openapi.yml``).
* The Python SDK :class:`api.client.MaxUserBot`, re-exported here.
"""

from api.client import MaxUserBot

__all__: list[str] = ["MaxUserBot"]
