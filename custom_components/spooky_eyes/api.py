"""Async client for a Spooky Eyes board: REST for commands, WebSocket for pushed state."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

WS_BACKOFF_MIN = 1.0
WS_BACKOFF_MAX = 60.0


class SpookyEyesApiError(Exception):
    """Raised on non-2xx responses or transport errors."""


class SpookyEyesClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int = 80) -> None:
        self._session = session
        self.host = host
        self.port = port
        self._ws_task: asyncio.Task | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}" if self.port == 80 else f"http://{self.host}:{self.port}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method, url, timeout=aiohttp.ClientTimeout(total=10), **kwargs
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise SpookyEyesApiError(f"{resp.status} on {path}: {text[:200]}")
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SpookyEyesApiError(f"{type(err).__name__} on {path}: {err}") from err

    async def get_info(self) -> dict[str, Any]:
        return await self._request("GET", "/api/info")

    async def get_state(self) -> dict[str, Any]:
        return await self._request("GET", "/api/state")

    async def set_state(self, **changes: Any) -> dict[str, Any]:
        """POST a partial state; the board answers with its full state."""
        return await self._request("POST", "/api/state", json=changes)

    async def action(self, action: str, **params: Any) -> dict[str, Any]:
        return await self._request("POST", "/api/action", json={"action": action, **params})

    # ─── Sounds (firmware with features.speaker) ────────────────────────────

    async def get_sounds(self) -> dict[str, Any]:
        """{"builtin": [...], "clips": [{"name", "bytes"}], "free_bytes": N}"""
        return await self._request("GET", "/api/sounds")

    async def upload_sound(self, name: str, wav: bytes) -> dict[str, Any]:
        """Multipart upload of a 16-bit PCM WAV clip; the board answers with its sound listing."""
        form = aiohttp.FormData()
        form.add_field("file", wav, filename=f"{name}.wav", content_type="audio/wav")
        url = f"{self.base_url}/api/sounds"
        try:
            async with self._session.post(
                url, params={"name": name}, data=form, timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise SpookyEyesApiError(f"{resp.status} on /api/sounds: {text[:200]}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SpookyEyesApiError(f"{type(err).__name__} on /api/sounds: {err}") from err

    async def delete_sound(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", "/api/sounds", params={"name": name})

    # ─── WebSocket push ────────────────────────────────────────────────────

    def start_listener(
        self,
        on_state: Callable[[dict[str, Any]], None],
        on_connection: Callable[[bool], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Run a background WebSocket listener that reconnects with exponential backoff."""
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.get_running_loop().create_task(
                self._listen_forever(on_state, on_connection, on_event)
            )

    async def stop_listener(self) -> None:
        task, self._ws_task = self._ws_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _listen_forever(
        self,
        on_state: Callable[[dict[str, Any]], None],
        on_connection: Callable[[bool], None] | None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        backoff = WS_BACKOFF_MIN
        url = f"{self.base_url.replace('http://', 'ws://', 1)}/ws"
        while True:
            try:
                async with self._session.ws_connect(url, heartbeat=30) as ws:
                    backoff = WS_BACKOFF_MIN
                    if on_connection:
                        on_connection(True)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._dispatch(msg.data, on_state, on_event)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.debug("Spooky Eyes websocket %s failed: %s", url, err)
            if on_connection:
                on_connection(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, WS_BACKOFF_MAX)

    @staticmethod
    def _dispatch(
        raw: str,
        on_state: Callable[[dict[str, Any]], None],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring non-JSON websocket frame: %.80s", raw)
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "state" and isinstance(msg.get("state"), dict):
            on_state(msg["state"])
        elif msg.get("type") == "event" and isinstance(msg.get("event"), str) and on_event:
            on_event(msg)
