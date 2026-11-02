"""One Python capability client spanning compatible runner-container profiles."""

from __future__ import annotations

from urllib.parse import urlsplit

import aiohttp
from stream_pack.catalog import load_catalog


class RemoteExecutionError(RuntimeError):
    pass


class RemoteCapabilityClient:
    def __init__(self, runners: dict[str, str], *, token: str):
        if not token or not runners or set(runners) - {"media", "chat"}:
            raise ValueError("Private runner profiles and transport token are required")
        for url in runners.values():
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("Runner addresses must be HTTP(S) origins without credentials")
        self.catalog = load_catalog()
        self.runners = {key: value.rstrip("/") for key, value in runners.items()}
        self._token = token
        self._session = None
        self._routes = {}

    async def start(self):
        if self._session is not None:
            return
        self._session = aiohttp.ClientSession(headers={"Authorization": "Bearer " + self._token})
        try:
            for profile, url in self.runners.items():
                async with self._session.get(
                    url + "/internal/capabilities",
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise RemoteExecutionError("Runner discovery failed")
                    declaration = await response.json()
                for entry in declaration["capabilities"]:
                    spec = self.catalog.get(entry["id"])
                    expected = (
                        "chat" if spec and spec.document["dependency_group"] == "chat" else "media"
                    )
                    if (
                        spec is None
                        or spec.digest != entry["digest"]
                        or profile != expected
                        or spec.id in self._routes
                    ):
                        raise RemoteExecutionError(
                            "Runner capability declaration does not match the installed catalog"
                        )
                    self._routes[spec.id] = url
            if set(self._routes) != set(self.catalog):
                raise RemoteExecutionError("Not all installed capabilities have compatible runners")
        except BaseException:
            await self.close()
            raise

    async def execute(self, capability, inputs, *, timeout=300):
        self.catalog[capability].validate(inputs)
        if self._session is None:
            raise RemoteExecutionError("Runner client has not been started")
        async with self._session.post(
            self._routes[capability] + "/internal/capabilities/" + capability + "/execute",
            json={"input": inputs, "timeout": timeout},
            timeout=aiohttp.ClientTimeout(total=timeout + 15),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise RemoteExecutionError("Capability execution failed")
            result = await response.json()
        self.catalog[capability].validate_output(result)
        return result

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._routes.clear()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()
