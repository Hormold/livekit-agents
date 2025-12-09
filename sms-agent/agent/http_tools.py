from __future__ import annotations

import json
from typing import Any

import aiohttp

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


async def http_get(url: str, params: dict[str, Any] | None = None, timeout: int = 10) -> dict[str, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": f"HTTP {resp.status}"}
    except Exception as e:
        return {"error": str(e)}


async def search_location(city: str) -> str:
    data = await http_get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1})

    if "error" in data:
        return f"Error: {data['error']}"

    results = data.get("results", [])
    if not results:
        return f"Could not find location: {city}"

    r = results[0]
    return json.dumps({
        "name": r.get("name"),
        "country": r.get("country"),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
    })


async def get_weather(latitude: float, longitude: float) -> str:
    data = await http_get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
    )

    if "error" in data:
        return f"Error: {data['error']}"

    current = data.get("current", {})
    if not current:
        return "No weather data available"

    weather_desc = WEATHER_CODES.get(current.get("weather_code", 0), "Unknown")
    temp = current.get("temperature_2m")
    feels_like = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")

    return f"{weather_desc}. {temp}°C (feels like {feels_like}°C). Humidity {humidity}%, wind {wind} km/h."


async def get_weather_by_city(city: str) -> str:
    location_json = await search_location(city)

    if location_json.startswith("Error") or location_json.startswith("Could not"):
        return location_json

    try:
        location = json.loads(location_json)
    except json.JSONDecodeError:
        return f"Failed to parse location data for {city}"

    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None:
        return f"No coordinates found for {city}"

    weather = await get_weather(lat, lon)
    name = location.get("name", city)
    country = location.get("country", "")

    return f"{name}, {country}: {weather}" if country else f"{name}: {weather}"
