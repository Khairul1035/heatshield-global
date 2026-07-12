import math

code = r'''
import math
from datetime import datetime, timezone
from typing import Any

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium


# =========================================================
# PAGE CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="HeatShield Global",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# API ENDPOINTS
# =========================================================

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"


# =========================================================
# RISK MODEL SETTINGS
# =========================================================

SECTOR_WEIGHTS = {
    "Office and indoor work": 0,
    "Security": 8,
    "Delivery and logistics": 12,
    "Outdoor events": 14,
    "Construction": 18,
    "Agriculture": 20,
    "Heavy manual labour": 25,
}

INTENSITY_WEIGHTS = {
    "Light": 0,
    "Moderate": 8,
    "Heavy": 18,
}

VULNERABILITY_WEIGHTS = {
    "Standard": 0,
    "Elevated": 8,
    "High": 15,
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    return max(minimum, min(value, maximum))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def haversine_distance(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    earth_radius_km = 6371.0

    lat_1 = math.radians(latitude_1)
    lat_2 = math.radians(latitude_2)

    delta_latitude = math.radians(latitude_2 - latitude_1)
    delta_longitude = math.radians(longitude_2 - longitude_1)

    calculation = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(lat_1)
        * math.cos(lat_2)
        * math.sin(delta_longitude / 2) ** 2
    )

    return earth_radius_km * 2 * math.atan2(
        math.sqrt(calculation),
        math.sqrt(1 - calculation),
    )


# =========================================================
# OPEN-METEO FUNCTIONS
# =========================================================

@st.cache_data(ttl=1800)
def geocode_location(location_name: str) -> dict | None:
    response = requests.get(
        GEOCODING_URL,
        params={
            "name": location_name,
            "count": 5,
            "language": "en",
            "format": "json",
        },
        timeout=15,
    )
    response.raise_for_status()

    results = response.json().get("results", [])
    return results[0] if results else None


@st.cache_data(ttl=900)
def get_weather(
    latitude: float,
    longitude: float,
    timezone_name: str,
) -> dict:
    response = requests.get(
        WEATHER_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "apparent_temperature,"
                "weather_code,"
                "wind_speed_10m,"
                "precipitation"
            ),
            "hourly": (
                "temperature_2m,"
                "apparent_temperature,"
                "relative_humidity_2m,"
                "uv_index,"
                "precipitation_probability"
            ),
            "daily": (
                "sunrise,"
                "sunset,"
                "temperature_2m_max,"
                "temperature_2m_min"
            ),
            "forecast_days": 2,
            "timezone": timezone_name,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=900)
def get_air_quality(
    latitude: float,
    longitude: float,
    timezone_name: str,
) -> dict:
    response = requests.get(
        AIR_QUALITY_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "european_aqi,"
                "pm2_5,"
                "pm10,"
                "nitrogen_dioxide,"
                "ozone"
            ),
            "hourly": "european_aqi,pm2_5,pm10",
            "forecast_days": 2,
            "timezone": timezone_name,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def get_current_uv(weather_data: dict) -> float:
    current_time = weather_data.get("current", {}).get("time")
    hourly_data = weather_data.get("hourly", {})

    hourly_times = hourly_data.get("time", [])
    uv_values = hourly_data.get("uv_index", [])

    if not uv_values:
        return 0.0

    if current_time in hourly_times:
        return safe_float(uv_values[hourly_times.index(current_time)])

    return safe_float(uv_values[0])


# =========================================================
# RISK ENGINE
# =========================================================

def calculate_environmental_score(
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
) -> dict:
    return {
        "Apparent temperature": round(
            clamp((apparent_temperature - 20) * 2.4, 0, 45),
            1,
        ),
        "Humidity": round(
            clamp((humidity - 35) * 0.25, 0, 15),
            1,
        ),
        "UV exposure": round(
            clamp(uv_index * 2, 0, 15),
            1,
        ),
        "Air quality": round(
            clamp(air_quality_index * 0.18, 0, 15),
            1,
        ),
    }


def calculate_workforce_risk(
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
    sector: str,
    intensity: str,
    exposure_hours: float,
    vulnerability: str,
) -> tuple[int, dict]:
    components = calculate_environmental_score(
        apparent_temperature,
        humidity,
        uv_index,
        air_quality_index,
    )

    components["Sector exposure"] = SECTOR_WEIGHTS[sector]
    components["Work intensity"] = INTENSITY_WEIGHTS[intensity]
    components["Exposure duration"] = round(
        clamp(exposure_hours * 2, 0, 12),
        1,
    )
    components["Worker vulnerability"] = (
        VULNERABILITY_WEIGHTS[vulnerability]
    )

    total_score = round(clamp(sum(components.values())))
    return total_score, components


def classify_risk(score: int) -> tuple[str, str]:
    if score < 25:
        return "Low", "Normal precautions and routine environmental monitoring."
    if score < 45:
        return "Moderate", "Increase hydration and continue active monitoring."
    if score < 65:
        return "High", "Reduce continuous exposure and increase recovery periods."
    if score < 80:
        return (
            "Very High",
            "Reschedule strenuous work and activate stronger heat controls.",
        )
    return (
        "Critical",
        "Suspend non-essential strenuous outdoor activity and escalate.",
    )


def risk_marker_colour(score: int) -> str:
    if score < 25:
        return "green"
    if score < 45:
        return "blue"
    if score < 65:
        return "orange"
    return "red"


def build_mitigation_plan(
    score: int,
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
    sector: str,
    intensity: str,
) -> list[str]:
    recommendations = []

    if score >= 80:
        recommendations.append(
            "Suspend or postpone non-essential strenuous outdoor work."
        )
    elif score >= 65:
        recommendations.append(
            "Move heavy activity to an earlier or later operational window."
        )
    elif score >= 45:
        recommendations.append(
            "Reduce continuous exposure and increase supervised recovery breaks."
        )
    else:
        recommendations.append(
            "Continue operations with routine monitoring and standard precautions."
        )

    if apparent_temperature >= 40:
        recommendations.append(
            "Provide a shaded or air-conditioned recovery area immediately."
        )

    if humidity >= 70:
        recommendations.append(
            "Increase hydration monitoring because high humidity reduces body cooling."
        )

    if uv_index >= 8:
        recommendations.append(
            "Reduce direct solar exposure and provide protective clothing and shade."
        )

    if air_quality_index >= 100:
        recommendations.append(
            "Reduce outdoor exposure for vulnerable workers due to poor air quality."
        )

    if intensity == "Heavy":
        recommendations.append(
            "Substitute heavy tasks with lighter preparation, inspection or planning work."
        )

    if sector in {"Construction", "Heavy manual labour", "Agriculture"}:
        recommendations.append(
            "Implement buddy monitoring and documented supervisor heat checks."
        )

    recommendations.append(
        "Escalate immediately if a person becomes confused, collapses, "
        "experiences seizures or loses consciousness."
    )

    return recommendations


# =========================================================
# EMERGENCY REACHABILITY ENGINE
# =========================================================

@st.cache_data(ttl=1800, show_spinner=False)
def get_road_route(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> dict | None:
    coordinates = (
        f"{origin_longitude},{origin_latitude};"
        f"{destination_longitude},{destination_latitude}"
    )

    try:
        response = requests.get(
            f"{OSRM_ROUTE_URL}/{coordinates}",
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
                "alternatives": "false",
            },
            timeout=20,
            headers={
                "User-Agent": (
                    "HeatShieldGlobal/2.1 "
                    "research-decision-support-prototype"
                )
            },
        )
        response.raise_for_status()
        routes = response.json().get("routes", [])

        if not routes:
            return None

        route = routes[0]

        return {
            "road_distance_km": round(
                safe_float(route.get("distance")) / 1000,
                2,
            ),
            "estimated_minutes": round(
                safe_float(route.get("duration")) / 60
            ),
            "geometry": route.get("geometry", {}).get("coordinates", []),
        }

    except (
        requests.exceptions.Timeout,
        requests.exceptions.RequestException,
        ValueError,
    ):
        return None


def classify_emergency_access(
    estimated_minutes: float | None,
) -> tuple[str, str, str]:
    if estimated_minutes is None:
        return "⚫ Unknown", "Routing unavailable", "gray"

    if estimated_minutes <= 10:
        return (
            "🟢 Green",
            "Estimated medical access within 10 minutes",
            "green",
        )

    if estimated_minutes <= 20:
        return (
            "🟡 Amber",
            "Estimated medical access within 11–20 minutes",
            "orange",
        )

    return (
        "🔴 Red",
        "Estimated medical access exceeds 20 minutes",
        "red",
    )


def add_route_to_map(
    map_object: folium.Map,
    geometry: list,
    colour: str = "blue",
    tooltip: str = "Estimated road route",
) -> None:
    if not geometry:
        return

    folium_coordinates = [
        [coordinate[1], coordinate[0]]
        for coordinate in geometry
        if len(coordinate) >= 2
    ]

    if not folium_coordinates:
        return

    folium.PolyLine(
        locations=folium_coordinates,
        color=colour,
        weight=5,
        opacity=0.8,
        tooltip=tooltip,
    ).add_to(map_object)


def enrich_medical_routes(
    medical_records: list[dict],
    origin_latitude: float,
    origin_longitude: float,
    maximum_facilities: int = 5,
) -> list[dict]:
    ranked_records = sorted(
        medical_records,
        key=lambda item: item.get("Distance (km)", 999999),
    )[:maximum_facilities]

    enriched_records = []

    for record in ranked_records:
        route = get_road_route(
            origin_latitude,
            origin_longitude,
            record["Latitude"],
            record["Longitude"],
        )

        enriched_record = record.copy()

        if route:
            estimated_minutes = route["estimated_minutes"]
            status, access_message, map_colour = (
                classify_emergency_access(estimated_minutes)
            )

            enriched_record.update(
                {
                    "Road distance (km)": route["road_distance_km"],
                    "Estimated drive time": f"{estimated_minutes} minutes",
                    "Estimated minutes": estimated_minutes,
                    "Access status": status,
                    "Access interpretation": access_message,
                    "Route geometry": route["geometry"],
                    "Route colour": map_colour,
                }
            )
        else:
            status, access_message, map_colour = (
                classify_emergency_access(None)
            )

            enriched_record.update(
                {
                    "Road distance (km)": None,
                    "Estimated drive time": "Unavailable",
                    "Estimated minutes": 999999,
                    "Access status": status,
                    "Access interpretation": access_message,
                    "Route geometry": [],
                    "Route colour": map_colour,
                }
            )

        enriched_records.append(enriched_record)

    enriched_records.sort(
        key=lambda item: (
            item["Estimated minutes"] == 999999,
            item["Estimated minutes"],
        )
    )

    return enriched_records


# =========================================================
# SAFE-ZONE SCANNER
# =========================================================

def build_overpass_query(
    latitude: float,
    longitude: float,
    radius_metres: int,
    selected_categories: tuple[str, ...],
) -> str:
    queries = []

    if "Hospitals and clinics" in selected_categories:
        queries.extend(
            [
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["amenity"~"hospital|clinic|doctors"];',
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["healthcare"~"hospital|clinic|doctor"];',
            ]
        )

    if "Pharmacies" in selected_categories:
        queries.append(
            f'nwr(around:{radius_metres},{latitude},{longitude})'
            '["amenity"="pharmacy"];'
        )

    if "Mosques and places of worship" in selected_categories:
        queries.append(
            f'nwr(around:{radius_metres},{latitude},{longitude})'
            '["amenity"="place_of_worship"];'
        )

    if "Shopping centres and indoor facilities" in selected_categories:
        queries.extend(
            [
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["shop"="mall"];',
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["building"="retail"];',
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["amenity"~"library|community_centre|shelter"];',
            ]
        )

    if "Restaurants, cafés and hydration" in selected_categories:
        queries.extend(
            [
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["amenity"~"restaurant|cafe|fast_food|food_court"];',
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["amenity"="drinking_water"];',
            ]
        )

    if "Parks and green areas" in selected_categories:
        queries.extend(
            [
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["leisure"~"park|garden"];',
                f'nwr(around:{radius_metres},{latitude},{longitude})'
                '["natural"="wood"];',
            ]
        )

    if "Police and fire services" in selected_categories:
        queries.append(
            f'nwr(around:{radius_metres},{latitude},{longitude})'
            '["amenity"~"police|fire_station"];'
        )

    return f"""
    [out:json][timeout:30];
    (
        {"".join(queries)}
    );
    out center tags;
    """


@st.cache_data(ttl=1800, show_spinner=False)
def query_overpass(query: str) -> dict:
    last_error = None

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                timeout=40,
                headers={
                    "User-Agent": (
                        "HeatShieldGlobal/2.1 "
                        "research-decision-support-prototype"
                    )
                },
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as error:
            last_error = error

    if last_error:
        raise last_error

    return {"elements": []}


def get_element_coordinates(
    element: dict,
) -> tuple[float | None, float | None]:
    latitude = element.get("lat")
    longitude = element.get("lon")

    if latitude is not None and longitude is not None:
        return safe_float(latitude), safe_float(longitude)

    centre = element.get("center", {})
    if centre.get("lat") is not None and centre.get("lon") is not None:
        return safe_float(centre["lat"]), safe_float(centre["lon"])

    return None, None


def classify_place(tags: dict) -> tuple[str, str, int]:
    amenity = tags.get("amenity", "")
    healthcare = tags.get("healthcare", "")
    shop = tags.get("shop", "")
    building = tags.get("building", "")
    leisure = tags.get("leisure", "")
    natural = tags.get("natural", "")
    religion = tags.get("religion", "")

    if amenity == "hospital" or healthcare == "hospital":
        return "Hospital", "Emergency medical support", 1

    if amenity in {"clinic", "doctors"} or healthcare in {"clinic", "doctor"}:
        return (
            "Clinic or medical centre",
            "Medical assessment and treatment",
            2,
        )

    if amenity == "pharmacy":
        return "Pharmacy", "Medical supplies and advice", 3

    if amenity == "fire_station":
        return (
            "Fire and rescue service",
            "Emergency response support",
            2,
        )

    if amenity == "police":
        return "Police station", "Emergency and public-safety support", 3

    if amenity == "drinking_water":
        return "Drinking-water point", "Hydration", 4

    if amenity == "place_of_worship":
        if religion == "muslim":
            return "Mosque", "Potential sheltered rest location", 5
        return (
            "Place of worship",
            "Potential sheltered rest location",
            6,
        )

    if shop == "mall" or building == "retail":
        return (
            "Shopping or retail facility",
            "Potential indoor cooling and temporary recovery",
            4,
        )

    if amenity == "library":
        return "Library", "Potential indoor recovery location", 5

    if amenity == "community_centre":
        return "Community centre", "Potential temporary shelter", 5

    if amenity == "shelter":
        return "Public shelter", "Potential sheltered rest location", 5

    if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
        return (
            "Food and drink facility",
            "Potential hydration and indoor rest",
            7,
        )

    if leisure in {"park", "garden"} or natural == "wood":
        return (
            "Park or green area",
            "Potential shade; ambient heat may remain high",
            8,
        )

    return (
        "Other safety-support facility",
        "Potential temporary support location",
        9,
    )


def parse_safe_zones(
    overpass_data: dict,
    origin_latitude: float,
    origin_longitude: float,
) -> list[dict]:
    records = []
    seen_locations = set()

    for element in overpass_data.get("elements", []):
        latitude, longitude = get_element_coordinates(element)

        if latitude is None or longitude is None:
            continue

        tags = element.get("tags", {})
        category, potential_use, priority = classify_place(tags)

        name = (
            tags.get("name:en")
            or tags.get("name")
            or tags.get("operator")
            or f"Unnamed {category}"
        )

        unique_key = (
            round(latitude, 5),
            round(longitude, 5),
            category,
        )

        if unique_key in seen_locations:
            continue

        seen_locations.add(unique_key)

        distance_km = haversine_distance(
            origin_latitude,
            origin_longitude,
            latitude,
            longitude,
        )

        navigation_url = (
            "https://www.openstreetmap.org/directions?"
            "engine=fossgis_osrm_car&"
            f"route={origin_latitude}%2C{origin_longitude}"
            f"%3B{latitude}%2C{longitude}"
        )

        records.append(
            {
                "Name": name,
                "Category": category,
                "Potential use": potential_use,
                "Distance (km)": round(distance_km, 2),
                "Opening hours": tags.get(
                    "opening_hours",
                    "Not available — verify before travelling",
                ),
                "Latitude": latitude,
                "Longitude": longitude,
                "Priority": priority,
                "Navigation": navigation_url,
            }
        )

    records.sort(
        key=lambda item: (
            item["Priority"],
            item["Distance (km)"],
        )
    )

    return records[:150]


def facility_marker_colour(category: str) -> str:
    if category == "Hospital":
        return "red"
    if category == "Clinic or medical centre":
        return "darkred"
    if category == "Pharmacy":
        return "pink"
    if category in {"Fire and rescue service", "Police station"}:
        return "darkblue"
    if category in {"Mosque", "Place of worship"}:
        return "green"
    if category == "Drinking-water point":
        return "blue"
    if category in {
        "Shopping or retail facility",
        "Library",
        "Community centre",
        "Public shelter",
    }:
        return "purple"
    if category == "Food and drink facility":
        return "orange"
    return "lightgreen"


# =========================================================
# HEADER
# =========================================================

st.title("🌍 HeatShield Global")

st.markdown(
    """
    ### Interactive Workforce Climate and Operational Risk Intelligence Platform

    **Transforming live environmental and location data into safer
    workforce and operational decisions.**
    """
)

st.markdown(
    """
    **Created by Mohd Khairul Ridhuan bin Mohd Fadzil, Malaysia**  
    Research Strategist in AI, Risk Intelligence and Business Governance  
    Powered by **MARYAM — Meteorological and Risk Advisory Management System**
    """
)

st.divider()


# =========================================================
# SIDEBAR CONTROLS
# =========================================================

with st.sidebar:
    st.header("Analysis Controls")

    location_input = st.text_input(
        "Enter city or location",
        value=st.session_state.get("location_input_value", "Kajang"),
    )

    sector = st.selectbox(
        "Sector",
        list(SECTOR_WEIGHTS.keys()),
        index=4,
    )

    intensity = st.selectbox(
        "Work intensity",
        list(INTENSITY_WEIGHTS.keys()),
        index=2,
    )

    exposure_hours = st.slider(
        "Continuous exposure duration",
        min_value=1.0,
        max_value=10.0,
        value=4.0,
        step=0.5,
    )

    vulnerability = st.selectbox(
        "Worker vulnerability",
        list(VULNERABILITY_WEIGHTS.keys()),
        index=0,
    )

    analyse_button = st.button(
        "Analyse Location",
        type="primary",
        use_container_width=True,
    )


# =========================================================
# SESSION STATE
# =========================================================

if "active_location" not in st.session_state:
    st.session_state.active_location = "Kajang"

if "safe_zone_results" not in st.session_state:
    st.session_state.safe_zone_results = []

if "safe_zone_location_key" not in st.session_state:
    st.session_state.safe_zone_location_key = None

if "medical_route_results" not in st.session_state:
    st.session_state.medical_route_results = []

if "medical_route_location_key" not in st.session_state:
    st.session_state.medical_route_location_key = None

if analyse_button:
    cleaned_location = location_input.strip()

    invalid_symbols = any(
        symbol in cleaned_location
        for symbol in ["_", "{", "}", "[", "]", "<", ">"]
    )

    if len(cleaned_location) < 2 or invalid_symbols:
        st.error("Location error: Enter a valid city, district or region.")
        st.stop()

    st.session_state.active_location = cleaned_location
    st.session_state.location_input_value = cleaned_location
    st.session_state.safe_zone_results = []
    st.session_state.safe_zone_location_key = None
    st.session_state.medical_route_results = []
    st.session_state.medical_route_location_key = None


# =========================================================
# RETRIEVE LOCATION AND ENVIRONMENTAL DATA
# =========================================================

active_location = st.session_state.active_location

try:
    with st.spinner("Retrieving live location and environmental data..."):
        location = geocode_location(active_location)

        if location is None:
            st.error(
                "Location error: Location not found. "
                "Enter a valid city or region and try again."
            )
            st.stop()

        latitude = safe_float(location.get("latitude"))
        longitude = safe_float(location.get("longitude"))
        timezone_name = location.get("timezone") or "auto"

        weather_data = get_weather(
            latitude,
            longitude,
            timezone_name,
        )

        air_quality_data = get_air_quality(
            latitude,
            longitude,
            timezone_name,
        )

except requests.exceptions.Timeout:
    st.error(
        "The public environmental-data service took too long to respond."
    )
    st.stop()

except requests.exceptions.RequestException as error:
    st.error("Environmental data could not be retrieved.")
    with st.expander("Technical error"):
        st.code(str(error))
    st.stop()


# =========================================================
# CURRENT CONDITIONS
# =========================================================

current_weather = weather_data.get("current", {})
current_air = air_quality_data.get("current", {})
daily_weather = weather_data.get("daily", {})

temperature = safe_float(current_weather.get("temperature_2m"))
apparent_temperature = safe_float(
    current_weather.get("apparent_temperature")
)
humidity = safe_float(current_weather.get("relative_humidity_2m"))
wind_speed = safe_float(current_weather.get("wind_speed_10m"))
precipitation = safe_float(current_weather.get("precipitation"))

uv_index = get_current_uv(weather_data)
air_quality_index = safe_float(current_air.get("european_aqi"))
pm25 = safe_float(current_air.get("pm2_5"))
pm10 = safe_float(current_air.get("pm10"))

risk_score, risk_components = calculate_workforce_risk(
    apparent_temperature,
    humidity,
    uv_index,
    air_quality_index,
    sector,
    intensity,
    exposure_hours,
    vulnerability,
)

risk_level, risk_message = classify_risk(risk_score)

location_name = location.get("name", active_location)
country_name = location.get("country", "Unknown country")
administrative_area = location.get("admin1")

display_location = f"{location_name}, {country_name}"

if administrative_area and administrative_area != location_name:
    display_location = (
        f"{location_name}, {administrative_area}, {country_name}"
    )

local_timestamp = current_weather.get("time", "Unavailable")

sunrise_values = daily_weather.get("sunrise", [])
sunset_values = daily_weather.get("sunset", [])

sunrise = sunrise_values[0] if sunrise_values else "Unavailable"
sunset = sunset_values[0] if sunset_values else "Unavailable"

retrieved_utc = datetime.now(timezone.utc).strftime(
    "%Y-%m-%d %H:%M:%S UTC"
)


# =========================================================
# LOCATION AND TIME
# =========================================================

st.subheader("Live Location and Time Intelligence")

location_col, time_col, status_col = st.columns(3)

with location_col:
    st.metric("Selected location", display_location)

with time_col:
    st.metric("Location date and time", local_timestamp)

with status_col:
    st.metric("Data status", "Live / Near-real-time")

st.caption(
    f"Timezone: {timezone_name} | "
    f"Coordinates: {latitude:.5f}, {longitude:.5f} | "
    f"Retrieved: {retrieved_utc}"
)


# =========================================================
# ENVIRONMENTAL KPI CARDS
# =========================================================

st.subheader("Environmental Conditions")

kpi_1, kpi_2, kpi_3, kpi_4 = st.columns(4)

with kpi_1:
    st.metric("Temperature", f"{temperature:.1f} °C")
with kpi_2:
    st.metric("Feels like", f"{apparent_temperature:.1f} °C")
with kpi_3:
    st.metric("Humidity", f"{humidity:.0f}%")
with kpi_4:
    st.metric("UV index", f"{uv_index:.1f}")

kpi_5, kpi_6, kpi_7, kpi_8 = st.columns(4)

with kpi_5:
    st.metric("European AQI", f"{air_quality_index:.0f}")
with kpi_6:
    st.metric("PM2.5", f"{pm25:.1f} μg/m³")
with kpi_7:
    st.metric("PM10", f"{pm10:.1f} μg/m³")
with kpi_8:
    st.metric("Wind speed", f"{wind_speed:.1f} km/h")

time_1, time_2, rain_col = st.columns(3)

with time_1:
    st.info(f"**Sunrise:** {sunrise}")
with time_2:
    st.info(f"**Sunset:** {sunset}")
with rain_col:
    st.info(f"**Current precipitation:** {precipitation:.1f} mm")


# =========================================================
# RISK INTELLIGENCE
# =========================================================

st.subheader("Workforce Risk Intelligence")

risk_col, explanation_col = st.columns([1, 2])

with risk_col:
    st.metric("Workforce Risk Score", f"{risk_score}/100")
    st.markdown(f"## {risk_level}")
    st.write(risk_message)

with explanation_col:
    component_df = pd.DataFrame(
        {
            "Risk component": list(risk_components.keys()),
            "Contribution": list(risk_components.values()),
        }
    )
    st.bar_chart(component_df.set_index("Risk component"))

    with st.expander("View risk contribution table"):
        st.dataframe(
            component_df,
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# MARYAM MITIGATION CENTRE
# =========================================================

st.subheader("MARYAM Live Mitigation Centre")

mitigation_plan = build_mitigation_plan(
    risk_score,
    apparent_temperature,
    humidity,
    uv_index,
    air_quality_index,
    sector,
    intensity,
)

for number, recommendation in enumerate(mitigation_plan, start=1):
    st.markdown(f"**{number}.** {recommendation}")

if risk_score >= 65:
    st.error(
        """
        **Emergency escalation notice**

        If a person becomes confused, collapses, experiences seizures,
        loses consciousness or shows signs of severe heat illness,
        contact the local emergency service immediately.

        Move the person away from direct heat while awaiting trained
        medical assistance. Do not rely solely on this application.
        """
    )


# =========================================================
# MAIN LOCATION MAP
# =========================================================

st.subheader("Interactive Location Map")

main_map = folium.Map(
    location=[latitude, longitude],
    zoom_start=12,
    control_scale=True,
    tiles="OpenStreetMap",
)

folium.Circle(
    location=[latitude, longitude],
    radius=10000,
    color=risk_marker_colour(risk_score),
    fill=True,
    fill_opacity=0.08,
    tooltip="Maximum 10 km safe-zone scanning radius",
).add_to(main_map)

folium.Marker(
    location=[latitude, longitude],
    tooltip=display_location,
    popup=(
        f"<b>{display_location}</b><br>"
        f"Risk score: {risk_score}/100<br>"
        f"Risk level: {risk_level}<br>"
        f"Feels like: {apparent_temperature:.1f} °C"
    ),
    icon=folium.Icon(
        color=risk_marker_colour(risk_score),
        icon="info-sign",
    ),
).add_to(main_map)

st_folium(
    main_map,
    height=500,
    use_container_width=True,
    key="main_location_map",
)


# =========================================================
# GLOBAL SAFE-ZONE SCANNER
# =========================================================

st.divider()
st.subheader("🛟 MARYAM Global Safe-Zone Scanner")

scanner_col_1, scanner_col_2 = st.columns([1, 2])

with scanner_col_1:
    radius_km = st.select_slider(
        "Scanning radius",
        options=[1, 2, 3, 5, 7, 10],
        value=5,
        format_func=lambda value: f"{value} km",
    )

with scanner_col_2:
    selected_categories = st.multiselect(
        "Facilities to scan",
        options=[
            "Hospitals and clinics",
            "Pharmacies",
            "Mosques and places of worship",
            "Shopping centres and indoor facilities",
            "Restaurants, cafés and hydration",
            "Parks and green areas",
            "Police and fire services",
        ],
        default=[
            "Hospitals and clinics",
            "Pharmacies",
            "Mosques and places of worship",
            "Shopping centres and indoor facilities",
            "Restaurants, cafés and hydration",
        ],
    )

scan_button = st.button(
    "Scan Nearby Safety Options",
    type="primary",
    use_container_width=True,
)

current_location_key = (
    round(latitude, 5),
    round(longitude, 5),
    radius_km,
    tuple(sorted(selected_categories)),
)

if scan_button:
    if not selected_categories:
        st.warning("Select at least one facility category.")
    else:
        try:
            with st.spinner(
                f"Scanning facilities within {radius_km} km..."
            ):
                query = build_overpass_query(
                    latitude,
                    longitude,
                    radius_km * 1000,
                    tuple(selected_categories),
                )

                overpass_data = query_overpass(query)

                st.session_state.safe_zone_results = parse_safe_zones(
                    overpass_data,
                    latitude,
                    longitude,
                )

                st.session_state.safe_zone_location_key = (
                    current_location_key
                )

        except requests.exceptions.Timeout:
            st.warning(
                "The global safe-zone service took too long to respond."
            )

        except requests.exceptions.RequestException as error:
            st.warning(
                "Nearby facilities could not be retrieved."
            )
            with st.expander("Technical error"):
                st.code(str(error))

safe_zone_results = st.session_state.safe_zone_results

results_match_location = (
    st.session_state.safe_zone_location_key
    == current_location_key
)

if safe_zone_results and results_match_location:
    st.success(
        f"Found {len(safe_zone_results)} potential safety-support "
        f"locations within {radius_km} km."
    )

    safe_zone_df = pd.DataFrame(safe_zone_results)

    display_columns = [
        "Name",
        "Category",
        "Potential use",
        "Distance (km)",
        "Opening hours",
        "Navigation",
    ]

    st.dataframe(
        safe_zone_df[display_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Navigation": st.column_config.LinkColumn(
                "Open navigation",
                display_text="Open map",
            )
        },
    )

    st.subheader("Interactive Safe-Zone Map")

    safe_zone_map = folium.Map(
        location=[latitude, longitude],
        zoom_start=13,
        control_scale=True,
        tiles="OpenStreetMap",
    )

    folium.Circle(
        location=[latitude, longitude],
        radius=radius_km * 1000,
        color=risk_marker_colour(risk_score),
        fill=True,
        fill_opacity=0.05,
    ).add_to(safe_zone_map)

    folium.Marker(
        location=[latitude, longitude],
        tooltip="Current selected location",
        popup=(
            f"<b>{display_location}</b><br>"
            f"Current risk: {risk_score}/100 — {risk_level}"
        ),
        icon=folium.Icon(color="black", icon="home"),
    ).add_to(safe_zone_map)

    for safe_zone in safe_zone_results:
        popup_html = (
            f"<b>{safe_zone['Name']}</b><br>"
            f"Category: {safe_zone['Category']}<br>"
            f"Distance: {safe_zone['Distance (km)']} km<br>"
            f"Potential use: {safe_zone['Potential use']}<br>"
            f"Opening hours: {safe_zone['Opening hours']}"
        )

        folium.Marker(
            location=[
                safe_zone["Latitude"],
                safe_zone["Longitude"],
            ],
            tooltip=safe_zone["Name"],
            popup=popup_html,
            icon=folium.Icon(
                color=facility_marker_colour(
                    safe_zone["Category"]
                ),
                icon="info-sign",
            ),
        ).add_to(safe_zone_map)

    st_folium(
        safe_zone_map,
        height=600,
        use_container_width=True,
        key="safe_zone_map",
    )

elif scan_button:
    st.info(
        "No mapped facilities were found. Try a larger radius."
    )

else:
    st.info(
        "Select a radius and facility categories, then press "
        "**Scan Nearby Safety Options**."
    )


# =========================================================
# EMERGENCY MEDICAL REACHABILITY
# =========================================================

st.divider()
st.subheader("🚑 MARYAM Emergency Reachability Engine")

medical_candidates = []

if safe_zone_results and results_match_location:
    medical_candidates = [
        place
        for place in safe_zone_results
        if place["Category"] in {
            "Hospital",
            "Clinic or medical centre",
        }
    ]

if not medical_candidates:
    st.info(
        "Run the Safe-Zone Scanner with "
        "**Hospitals and clinics** selected before calculating "
        "emergency reachability."
    )

else:
    calculate_routes_button = st.button(
        "Calculate Fastest Medical Access",
        type="primary",
        use_container_width=True,
    )

    medical_location_key = (
        round(latitude, 5),
        round(longitude, 5),
        tuple(
            sorted(
                (
                    item["Name"],
                    item["Latitude"],
                    item["Longitude"],
                )
                for item in medical_candidates
            )
        ),
    )

    if calculate_routes_button:
        with st.spinner(
            "Calculating road routes to nearby medical facilities..."
        ):
            st.session_state.medical_route_results = (
                enrich_medical_routes(
                    medical_candidates,
                    latitude,
                    longitude,
                    maximum_facilities=5,
                )
            )

            st.session_state.medical_route_location_key = (
                medical_location_key
            )

    medical_route_results = (
        st.session_state.medical_route_results
    )

    route_results_match_location = (
        st.session_state.medical_route_location_key
        == medical_location_key
    )

    if medical_route_results and route_results_match_location:
        fastest_available = next(
            (
                item
                for item in medical_route_results
                if item["Estimated drive time"] != "Unavailable"
            ),
            None,
        )

        fastest_hospital = next(
            (
                item
                for item in medical_route_results
                if item["Category"] == "Hospital"
                and item["Estimated drive time"] != "Unavailable"
            ),
            None,
        )

        fastest_clinic = next(
            (
                item
                for item in medical_route_results
                if item["Category"] == "Clinic or medical centre"
                and item["Estimated drive time"] != "Unavailable"
            ),
            None,
        )

        if fastest_available:
            fastest_minutes = fastest_available["Estimated minutes"]
            access_status, access_text, _ = (
                classify_emergency_access(fastest_minutes)
            )

            summary_col_1, summary_col_2, summary_col_3 = (
                st.columns(3)
            )

            with summary_col_1:
                st.metric(
                    "Fastest mapped medical option",
                    fastest_available["Name"],
                )

            with summary_col_2:
                st.metric(
                    "Estimated driving time",
                    fastest_available["Estimated drive time"],
                )

            with summary_col_3:
                st.metric(
                    "Medical-access indicator",
                    access_status,
                )

            st.info(access_text)

            hospital_col, clinic_col = st.columns(2)

            with hospital_col:
                if fastest_hospital:
                    st.success(
                        "**Fastest mapped hospital:** "
                        f"{fastest_hospital['Name']} — "
                        f"{fastest_hospital['Estimated drive time']}"
                    )
                else:
                    st.warning(
                        "No routable mapped hospital was found."
                    )

            with clinic_col:
                if fastest_clinic:
                    st.info(
                        "**Fastest mapped clinic:** "
                        f"{fastest_clinic['Name']} — "
                        f"{fastest_clinic['Estimated drive time']}"
                    )
                else:
                    st.info(
                        "No routable mapped clinic was found."
                    )

            if fastest_available["Category"] == "Clinic or medical centre":
                st.warning(
                    "The fastest mapped option is a clinic or "
                    "medical centre. It may not provide emergency "
                    "care for severe heatstroke."
                )

        route_table = pd.DataFrame(medical_route_results)

        st.dataframe(
            route_table[
                [
                    "Name",
                    "Category",
                    "Road distance (km)",
                    "Estimated drive time",
                    "Access status",
                    "Access interpretation",
                    "Opening hours",
                    "Navigation",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Navigation": st.column_config.LinkColumn(
                    "Navigation",
                    display_text="Open map",
                )
            },
        )

        st.subheader("Emergency Medical Route Map")

        emergency_route_map = folium.Map(
            location=[latitude, longitude],
            zoom_start=13,
            control_scale=True,
            tiles="OpenStreetMap",
        )

        folium.Marker(
            location=[latitude, longitude],
            tooltip="Current selected location",
            popup=(
                f"<b>{display_location}</b><br>"
                f"Current workforce risk: "
                f"{risk_score}/100 — {risk_level}"
            ),
            icon=folium.Icon(
                color="black",
                icon="home",
            ),
        ).add_to(emergency_route_map)

        for position, medical_place in enumerate(
            medical_route_results,
            start=1,
        ):
            add_route_to_map(
                emergency_route_map,
                medical_place["Route geometry"],
                medical_place["Route colour"],
                (
                    f"Route {position}: "
                    f"{medical_place['Name']} — "
                    f"{medical_place['Estimated drive time']}"
                ),
            )

            medical_popup = (
                f"<b>{position}. {medical_place['Name']}</b><br>"
                f"Category: {medical_place['Category']}<br>"
                f"Road distance: "
                f"{medical_place['Road distance (km)']} km<br>"
                f"Estimated drive: "
                f"{medical_place['Estimated drive time']}<br>"
                f"Status: {medical_place['Access status']}"
            )

            folium.Marker(
                location=[
                    medical_place["Latitude"],
                    medical_place["Longitude"],
                ],
                tooltip=medical_place["Name"],
                popup=medical_popup,
                icon=folium.Icon(
                    color=(
                        "red"
                        if medical_place["Category"] == "Hospital"
                        else "orange"
                    ),
                    icon="plus-sign",
                ),
            ).add_to(emergency_route_map)

        st_folium(
            emergency_route_map,
            height=600,
            use_container_width=True,
            key="emergency_route_map",
        )

        st.caption(
            "Green, amber and red indicators represent estimated "
            "road-access time only. They do not represent live "
            "traffic conditions or ambulance response time."
        )


# =========================================================
# 24-HOUR FORECAST
# =========================================================

st.divider()
st.subheader("24-Hour Environmental Forecast")

weather_hourly = weather_data.get("hourly", {})

forecast_df = pd.DataFrame(
    {
        "Time": weather_hourly.get("time", [])[:24],
        "Temperature": weather_hourly.get(
            "temperature_2m",
            [],
        )[:24],
        "Feels Like": weather_hourly.get(
            "apparent_temperature",
            [],
        )[:24],
        "Humidity": weather_hourly.get(
            "relative_humidity_2m",
            [],
        )[:24],
        "UV Index": weather_hourly.get(
            "uv_index",
            [],
        )[:24],
        "Rain probability": weather_hourly.get(
            "precipitation_probability",
            [],
        )[:24],
    }
)

if not forecast_df.empty:
    forecast_df["Time"] = pd.to_datetime(forecast_df["Time"])

    st.line_chart(
        forecast_df.set_index("Time")[
            ["Temperature", "Feels Like", "UV Index"]
        ]
    )

    with st.expander("View 24-hour forecast table"):
        st.dataframe(
            forecast_df,
            use_container_width=True,
            hide_index=True,
        )

else:
    st.info("Hourly forecast data is currently unavailable.")


# =========================================================
# METHODOLOGY AND FOOTER
# =========================================================

st.divider()

with st.expander("Methodology, data sources and limitations"):
    st.markdown(
        """
        **Data sources**

        - Open-Meteo Geocoding API
        - Open-Meteo Weather API
        - Open-Meteo Air Quality API
        - OpenStreetMap
        - Public Overpass API servers
        - Public OSRM routing service

        **Important limitations**

        - Weather and air-quality data are near-real-time estimates.
        - Safe-zone coverage depends on OpenStreetMap completeness.
        - Opening hours may be missing or outdated.
        - A listed location is not automatically open, cooler or air-conditioned.
        - Route times do not include live traffic.
        - Route time is not ambulance response time.
        - The application does not replace emergency services,
          medical assessment or occupational-health professionals.
        """
    )

st.caption(
    "HeatShield Global | Created by Mohd Khairul Ridhuan "
    "bin Mohd Fadzil, Malaysia | Powered by MARYAM | "
    "Prototype Version 2.1 | 2026"
)
'''

path = Path('/mnt/data/app.py')
path.write_text(code, encoding='utf-8')
print(path)
