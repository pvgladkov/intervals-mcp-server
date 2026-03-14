from __future__ import annotations

import os
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import anyio
import httpx
import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

BASE_URL = os.environ.get("INTERVALS_BASE_URL", "https://intervals.icu/api/v1")
API_KEY = os.environ.get("INTERVALS_API_KEY", "")
ATHLETE_ID = os.environ.get("INTERVALS_ATHLETE_ID", "0")

mcp = FastMCP("IntervalsICU", json_response=True)
DEBUG_STDIO = os.environ.get("INTERVALS_DEBUG_STDIO") == "1"
REDACTED_KEYS = {
    "email",
    "icu_api_key",
    "icu_friend_invite_token",
}


def _debug(message: str) -> None:
    if DEBUG_STDIO:
        print(message, file=sys.stderr, flush=True)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in REDACTED_KEYS or key.endswith("_token"):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _recent_date_range(days: int = 30) -> tuple[str, str]:
    newest = date.today()
    oldest = newest - timedelta(days=days)
    return oldest.isoformat(), newest.isoformat()


def _event_date_range(past_days: int = 30, future_days: int = 30) -> tuple[str, str]:
    today = date.today()
    oldest = today - timedelta(days=past_days)
    newest = today + timedelta(days=future_days)
    return oldest.isoformat(), newest.isoformat()


def _list_result(key: str, data: Any, limit: int | None = None) -> Any:
    if isinstance(data, list):
        if limit is not None:
            data = data[:limit]
        return {key: data}
    return data


def _write_stdout_line(line: str) -> None:
    _debug(f"stdout write: {line}")
    sys.stdout.write(line)
    sys.stdout.write("\n")
    sys.stdout.flush()


@asynccontextmanager
async def threaded_stdio_server():
    """Stdio transport that avoids anyio.wrap_file hangs in this environment."""
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def stdin_reader() -> None:
        async with read_stream_writer:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if line == "":
                    _debug("stdin EOF")
                    break
                _debug(f"stdin read: {line.rstrip()}")
                try:
                    message = types.JSONRPCMessage.model_validate_json(line)
                except Exception as exc:
                    _debug(f"stdin parse error: {exc}")
                    await read_stream_writer.send(exc)
                    continue
                await read_stream_writer.send(SessionMessage(message))

    async def stdout_writer() -> None:
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                json_line = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                await asyncio.to_thread(_write_stdout_line, json_line)

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream


async def run_stdio_server() -> None:
    _debug("server run starting")
    async with threaded_stdio_server() as (read_stream, write_stream):
        _debug("stdio bridge ready")
        await mcp._mcp_server.run(
            read_stream,
            write_stream,
            mcp._mcp_server.create_initialization_options(),
        )
    _debug("server run finished")


class IntervalsClient:
    def __init__(self, api_key: str, athlete_id: str, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise ValueError("Missing INTERVALS_API_KEY environment variable")
        if not athlete_id:
            raise ValueError("Missing INTERVALS_ATHLETE_ID environment variable")
        self.api_key = api_key
        self.athlete_id = athlete_id
        self.base_url = base_url.rstrip("/")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        # Intervals.icu supports personal API access with Basic Auth:
        # username=API_KEY, password=<your key>
        # This is described in the Intervals.icu API access guide.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, auth=("API_KEY", self.api_key))
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type or response.text.startswith(("{", "[")):
                return _sanitize(response.json())
            return _sanitize(response.text)

def icu_client() -> IntervalsClient:
    return IntervalsClient(api_key=API_KEY, athlete_id=ATHLETE_ID)


@mcp.tool()
async def get_athlete() -> Any:
    """Return the athlete object for the configured Intervals.icu athlete."""
    client = icu_client()
    return await client.get(f"/athlete/{client.athlete_id}")


@mcp.tool()
async def list_activities(
    oldest: str | None = None,
    newest: str | None = None,
    activity_type: str | None = None,
    limit: int = 20,
) -> Any:
    """List recent activities for the configured athlete.

    Args:
        oldest: Lower bound date in YYYY-MM-DD.
        newest: Upper bound date in YYYY-MM-DD.
        activity_type: Optional sport type, e.g. Ride or Run.
        limit: Max number of activities to return.
    """
    client = icu_client()
    params: dict[str, Any] = {}
    if oldest is None and newest is None:
        oldest, newest = _recent_date_range()
    if oldest:
        params["oldest"] = oldest
    if newest:
        params["newest"] = newest
    if activity_type:
        params["type"] = activity_type

    data = await client.get(f"/athlete/{client.athlete_id}/activities", params=params)
    return _list_result("activities", data, limit=limit)


@mcp.tool()
async def get_activity(activity_id: str) -> Any:
    """Fetch a single activity by its Intervals.icu activity id."""
    client = icu_client()
    return await client.get(f"/activity/{activity_id}")


@mcp.tool()
async def get_wellness(local_date: str | None = None) -> Any:
    """Fetch wellness for a day in YYYY-MM-DD. Defaults to today if omitted."""
    client = icu_client()
    params: dict[str, Any] = {}
    if local_date is None:
        local_date = date.today().isoformat()
    params["localDate"] = local_date
    data = await client.get(f"/athlete/{client.athlete_id}/wellness", params=params)
    return _list_result("wellness", data)


@mcp.tool()
async def get_events(oldest: str | None = None, newest: str | None = None) -> Any:
    """Fetch planned and completed events for a date range."""
    client = icu_client()
    params: dict[str, Any] = {}
    if oldest is None and newest is None:
        oldest, newest = _event_date_range()
    if oldest:
        params["oldest"] = oldest
    if newest:
        params["newest"] = newest
    data = await client.get(f"/athlete/{client.athlete_id}/events", params=params)
    return _list_result("events", data)


@mcp.tool()
async def get_fitness(oldest: str | None = None, newest: str | None = None) -> Any:
    """Fetch fitness / fatigue / form data from the wellness endpoint."""
    client = icu_client()
    params: dict[str, Any] = {}
    if oldest is None and newest is None:
        oldest, newest = _recent_date_range()
    if oldest:
        params["oldest"] = oldest
    if newest:
        params["newest"] = newest
    data = await client.get(f"/athlete/{client.athlete_id}/wellness", params=params)
    return _list_result("fitness", data)


if __name__ == "__main__":
    anyio.run(run_stdio_server)
