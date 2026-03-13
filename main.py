from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("INTERVALS_BASE_URL", "https://intervals.icu/api/v1")
API_KEY = os.environ.get("INTERVALS_API_KEY", "")
ATHLETE_ID = os.environ.get("INTERVALS_ATHLETE_ID", "")

mcp = FastMCP("IntervalsICU", json_response=True)


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
                return response.json()
            return response.text



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
    if oldest:
        params["oldest"] = oldest
    if newest:
        params["newest"] = newest
    if activity_type:
        params["type"] = activity_type

    data = await client.get(f"/athlete/{client.athlete_id}/activities", params=params)
    if isinstance(data, list):
        return data[:limit]
    return data


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
    return await client.get(f"/athlete/{client.athlete_id}/wellness", params=params)


@mcp.tool()
async def get_events(oldest: str | None = None, newest: str | None = None) -> Any:
    """Fetch planned and completed events for a date range."""
    client = icu_client()
    params: dict[str, Any] = {}
    if oldest:
        params["oldest"] = oldest
    if newest:
        params["newest"] = newest
    return await client.get(f"/athlete/{client.athlete_id}/events", params=params)


@mcp.tool()
async def get_fitness() -> Any:
    """Fetch fitness / fatigue / form data for the configured athlete.

    Note: endpoint names have varied in community examples over time. If this tool
    returns a 404, inspect your current API docs and adjust the path accordingly.
    """
    client = icu_client()
    return await client.get(f"/athlete/{client.athlete_id}/fitness")


if __name__ == "__main__":
    mcp.run(transport="stdio")