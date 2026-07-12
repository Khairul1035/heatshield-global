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
AIR_QUALITY_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving"
)


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

def clamp(
    value: float,
    minimum: float = 0,
    maximum: float = 100,
) -> float:
    """Keep a value between the permitted limits."""

    return max(
        minimum,
        min(value, maximum),
    )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    """Convert a value safely into a float."""

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
    """Calculate straight-line distance in kilometres."""

    earth_radius_km = 6371.0

    latitude_1_radians = math.radians(latitude_1)
    latitude_2_radians = math.radians(latitude_2)

    latitude_difference = math.radians(
        latitude_2 - latitude_1
    )

    longitude_difference = math.radians(
        longitude_2 - longitude_1
    )

    calculation = (
        math.sin(latitude_difference / 2) ** 2
        + math.cos(latitude_1_radians)
        * math.cos(latitude_2_radians)
        * math.sin(longitude_difference / 2) ** 2
    )

    return (
        earth_radius_km
        * 2
        * math.atan2(
            math.sqrt(calculation),
            math.sqrt(1 - calculation),
        )
    )


# =========================================================
# OPEN-METEO FUNCTIONS
# =========================================================

@st.cache_data(ttl=1800)
def geocode_location(
    location_name: str,
) -> dict | None:
    """Convert a city or location name into coordinates."""

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

    results = response.json().get(
        "results",
        [],
    )

    if not results:
        return None

    return results[0]


@st.cache_data(ttl=900)
def get_weather(
    latitude: float,
    longitude: float,
    timezone_name: str,
) -> dict:
    """Retrieve weather and forecast information."""

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
    """Retrieve current air-quality information."""

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
            "hourly": (
                "european_aqi,"
                "pm2_5,"
                "pm10"
            ),
            "forecast_days": 2,
            "timezone": timezone_name,
        },
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


def get_current_uv(
    weather_data: dict,
) -> float:
    """Return the UV value nearest to the current hour."""

    current_time = weather_data.get(
        "current",
        {},
    ).get("time")

    hourly_data = weather_data.get(
        "hourly",
        {},
    )

    hourly_times = hourly_data.get(
        "time",
        [],
    )

    uv_values = hourly_data.get(
        "uv_index",
        [],
    )

    if not uv_values:
        return 0.0

    if current_time in hourly_times:
        index = hourly_times.index(
            current_time
        )

        return safe_float(
            uv_values[index]
        )

    return safe_float(
        uv_values[0]
    )


# =========================================================
# WORKFORCE RISK ENGINE
# =========================================================

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
    """Calculate the explainable workforce risk score."""

    components = {
        "Apparent temperature": round(
            clamp(
                (apparent_temperature - 20) * 2.4,
                0,
                45,
            ),
            1,
        ),
        "Humidity": round(
            clamp(
                (humidity - 35) * 0.25,
                0,
                15,
            ),
            1,
        ),
        "UV exposure": round(
            clamp(
                uv_index * 2,
                0,
                15,
            ),
            1,
        ),
        "Air quality": round(
            clamp(
                air_quality_index * 0.18,
                0,
                15,
            ),
            1,
        ),
        "Sector exposure": (
            SECTOR_WEIGHTS[sector]
        ),
        "Work intensity": (
            INTENSITY_WEIGHTS[intensity]
        ),
        "Exposure duration": round(
            clamp(
                exposure_hours * 2,
                0,
                12,
            ),
            1,
        ),
        "Worker vulnerability": (
            VULNERABILITY_WEIGHTS[
                vulnerability
            ]
        ),
    }

    total_score = round(
        clamp(
            sum(components.values())
        )
    )

    return total_score, components


def classify_risk(
    score: int,
) -> tuple[str, str]:
    """Convert risk score into a risk category."""

    if score < 25:
        return (
            "Low",
            "Normal precautions and routine "
            "environmental monitoring.",
        )

    if score < 45:
        return (
            "Moderate",
            "Increase hydration and continue "
            "active monitoring.",
        )

    if score < 65:
        return (
            "High",
            "Reduce continuous exposure and "
            "increase recovery periods.",
        )

    if score < 80:
        return (
            "Very High",
            "Reschedule strenuous work and "
            "activate stronger heat controls.",
        )

    return (
        "Critical",
        "Suspend non-essential strenuous "
        "outdoor activity and escalate.",
    )


def risk_marker_colour(
    score: int,
) -> str:
    """Return a Folium colour based on risk level."""

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
    """Generate explainable mitigation recommendations."""

    recommendations = []

    if score >= 80:
        recommendations.append(
            "Suspend or postpone non-essential "
            "strenuous outdoor work."
        )

    elif score >= 65:
        recommendations.append(
            "Move heavy activity to an earlier "
            "or later operational window."
        )

    elif score >= 45:
        recommendations.append(
            "Reduce continuous exposure and increase "
            "supervised recovery breaks."
        )

    else:
        recommendations.append(
            "Continue operations with routine "
            "monitoring and standard precautions."
        )

    if apparent_temperature >= 40:
        recommendations.append(
            "Provide a shaded or air-conditioned "
            "recovery area immediately."
        )

    if humidity >= 70:
        recommendations.append(
            "Increase hydration monitoring because "
            "high humidity reduces body cooling."
        )

    if uv_index >= 8:
        recommendations.append(
            "Reduce direct sunlight exposure and "
            "provide protective clothing and shade."
        )

    if air_quality_index >= 100:
        recommendations.append(
            "Reduce outdoor exposure for vulnerable "
            "workers due to poor air quality."
        )

    if intensity == "Heavy":
        recommendations.append(
            "Substitute heavy tasks with lighter "
            "preparation, inspection or planning work."
        )

    if sector in {
        "Construction",
        "Heavy manual labour",
        "Agriculture",
    }:
        recommendations.append(
            "Implement buddy monitoring and "
            "documented supervisor heat checks."
        )

    recommendations.append(
        "Escalate immediately if a person becomes "
        "confused, collapses, experiences seizures "
        "or loses consciousness."
    )

    return recommendations


# =========================================================
# SAFE-ZONE SCANNER FUNCTIONS
# =========================================================

def build_overpass_query(
    latitude: float,
    longitude: float,
    radius_metres: int,
    selected_categories: tuple[str, ...],
) -> str:
    """Build the OpenStreetMap Overpass query."""

    queries = []

    if "Hospitals and clinics" in selected_categories:
        queries.extend(
            [
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["amenity"~'
                    '"hospital|clinic|doctors"];'
                ),
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["healthcare"~'
                    '"hospital|clinic|doctor"];'
                ),
            ]
        )

    if "Pharmacies" in selected_categories:
        queries.append(
            (
                f'nwr(around:{radius_metres},'
                f'{latitude},{longitude})'
                '["amenity"="pharmacy"];'
            )
        )

    if (
        "Mosques and places of worship"
        in selected_categories
    ):
        queries.append(
            (
                f'nwr(around:{radius_metres},'
                f'{latitude},{longitude})'
                '["amenity"="place_of_worship"];'
            )
        )

    if (
        "Shopping centres and indoor facilities"
        in selected_categories
    ):
        queries.extend(
            [
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["shop"="mall"];'
                ),
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["building"="retail"];'
                ),
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["amenity"~'
                    '"library|community_centre|shelter"];'
                ),
            ]
        )

    if (
        "Restaurants, cafés and hydration"
        in selected_categories
    ):
        queries.extend(
            [
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["amenity"~'
                    '"restaurant|cafe|fast_food|food_court"];'
                ),
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["amenity"="drinking_water"];'
                ),
            ]
        )

    if (
        "Parks and green areas"
        in selected_categories
    ):
        queries.extend(
            [
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["leisure"~"park|garden"];'
                ),
                (
                    f'nwr(around:{radius_metres},'
                    f'{latitude},{longitude})'
                    '["natural"="wood"];'
                ),
            ]
        )

    if (
        "Police and fire services"
        in selected_categories
    ):
        queries.append(
            (
                f'nwr(around:{radius_metres},'
                f'{latitude},{longitude})'
                '["amenity"~'
                '"police|fire_station"];'
            )
        )

    joined_queries = "".join(
        queries
    )

    return (
        "[out:json][timeout:30];"
        f"({joined_queries});"
        "out center tags;"
    )


@st.cache_data(
    ttl=1800,
    show_spinner=False,
)
def query_overpass(
    query: str,
) -> dict:
    """Call public Overpass servers with fallback."""

    last_error = None

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data={
                    "data": query,
                },
                timeout=40,
                headers={
                    "User-Agent": (
                        "HeatShieldGlobal/2.2 "
                        "research-prototype"
                    )
                },
            )

            response.raise_for_status()

            return response.json()

        except requests.RequestException as error:
            last_error = error

    if last_error:
        raise last_error

    return {
        "elements": []
    }


def get_element_coordinates(
    element: dict,
) -> tuple[float | None, float | None]:
    """Return coordinates for nodes, ways or relations."""

    latitude = element.get(
        "lat"
    )

    longitude = element.get(
        "lon"
    )

    if (
        latitude is not None
        and longitude is not None
    ):
        return (
            safe_float(latitude),
            safe_float(longitude),
        )

    centre = element.get(
        "center",
        {},
    )

    if (
        centre.get("lat") is not None
        and centre.get("lon") is not None
    ):
        return (
            safe_float(
                centre["lat"]
            ),
            safe_float(
                centre["lon"]
            ),
        )

    return None, None


def classify_place(
    tags: dict,
) -> tuple[str, str, int]:
    """Classify OpenStreetMap facilities."""

    amenity = tags.get(
        "amenity",
        "",
    )

    healthcare = tags.get(
        "healthcare",
        "",
    )

    shop = tags.get(
        "shop",
        "",
    )

    building = tags.get(
        "building",
        "",
    )

    leisure = tags.get(
        "leisure",
        "",
    )

    natural = tags.get(
        "natural",
        "",
    )

    religion = tags.get(
        "religion",
        "",
    )

    if (
        amenity == "hospital"
        or healthcare == "hospital"
    ):
        return (
            "Hospital",
            "Emergency medical support",
            1,
        )

    if (
        amenity in {
            "clinic",
            "doctors",
        }
        or healthcare in {
            "clinic",
            "doctor",
        }
    ):
        return (
            "Clinic or medical centre",
            "Medical assessment and treatment",
            2,
        )

    if amenity == "pharmacy":
        return (
            "Pharmacy",
            "Medical supplies and advice",
            3,
        )

    if amenity == "fire_station":
        return (
            "Fire and rescue service",
            "Emergency response support",
            2,
        )

    if amenity == "police":
        return (
            "Police station",
            "Emergency and public-safety support",
            3,
        )

    if amenity == "drinking_water":
        return (
            "Drinking-water point",
            "Hydration",
            4,
        )

    if amenity == "place_of_worship":
        if religion == "muslim":
            return (
                "Mosque",
                "Potential sheltered rest location",
                5,
            )

        return (
            "Place of worship",
            "Potential sheltered rest location",
            5,
        )

    if (
        shop == "mall"
        or building == "retail"
    ):
        return (
            "Shopping or retail facility",
            "Potential indoor cooling and "
            "temporary recovery",
            4,
        )

    if amenity == "library":
        return (
            "Library",
            "Potential indoor recovery location",
            5,
        )

    if amenity == "community_centre":
        return (
            "Community centre",
            "Potential temporary shelter",
            5,
        )

    if amenity == "shelter":
        return (
            "Public shelter",
            "Potential sheltered rest location",
            5,
        )

    if amenity in {
        "restaurant",
        "cafe",
        "fast_food",
        "food_court",
    }:
        return (
            "Food and drink facility",
            "Potential hydration and indoor rest",
            7,
        )

    if (
        leisure in {
            "park",
            "garden",
        }
        or natural == "wood"
    ):
        return (
            "Park or green area",
            "Potential shade; ambient heat "
            "may remain high",
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
    """Convert Overpass results into safe-zone records."""

    records = []
    seen_locations = set()

    for element in overpass_data.get(
        "elements",
        [],
    ):
        latitude, longitude = (
            get_element_coordinates(
                element
            )
        )

        if (
            latitude is None
            or longitude is None
        ):
            continue

        tags = element.get(
            "tags",
            {},
        )

        category, potential_use, priority = (
            classify_place(tags)
        )

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

        seen_locations.add(
            unique_key
        )

        distance_km = haversine_distance(
            origin_latitude,
            origin_longitude,
            latitude,
            longitude,
        )

        navigation_url = (
            "https://www.openstreetmap.org/directions?"
            "engine=fossgis_osrm_car&"
            f"route={origin_latitude}%2C"
            f"{origin_longitude}%3B"
            f"{latitude}%2C{longitude}"
        )

        records.append(
            {
                "Name": name,
                "Category": category,
                "Potential use": potential_use,
                "Distance (km)": round(
                    distance_km,
                    2,
                ),
                "Opening hours": tags.get(
                    "opening_hours",
                    "Not available — verify "
                    "before travelling",
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


def facility_marker_colour(
    category: str,
) -> str:
    """Return marker colour based on facility type."""

    category_colours = {
        "Hospital": "red",
        "Clinic or medical centre": "darkred",
        "Pharmacy": "pink",
        "Fire and rescue service": "darkblue",
        "Police station": "darkblue",
        "Mosque": "green",
        "Place of worship": "green",
        "Drinking-water point": "blue",
        "Shopping or retail facility": "purple",
        "Library": "purple",
        "Community centre": "purple",
        "Public shelter": "purple",
        "Food and drink facility": "orange",
        "Park or green area": "lightgreen",
    }

    return category_colours.get(
        category,
        "cadetblue",
    )


# =========================================================
# EMERGENCY REACHABILITY FUNCTIONS
# =========================================================

@st.cache_data(
    ttl=1800,
    show_spinner=False,
)
def get_road_route(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> dict | None:
    """Retrieve road route and driving estimate."""

    coordinates = (
        f"{origin_longitude},"
        f"{origin_latitude};"
        f"{destination_longitude},"
        f"{destination_latitude}"
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
                    "HeatShieldGlobal/2.2 "
                    "research-prototype"
                )
            },
        )

        response.raise_for_status()

        routes = response.json().get(
            "routes",
            [],
        )

        if not routes:
            return None

        route = routes[0]

        return {
            "road_distance_km": round(
                safe_float(
                    route.get("distance")
                )
                / 1000,
                2,
            ),
            "estimated_minutes": max(
                1,
                round(
                    safe_float(
                        route.get("duration")
                    )
                    / 60
                ),
            ),
            "geometry": route.get(
                "geometry",
                {},
            ).get(
                "coordinates",
                [],
            ),
        }

    except (
        requests.RequestException,
        ValueError,
    ):
        return None


def classify_emergency_access(
    estimated_minutes: float | None,
) -> tuple[str, str, str]:
    """Create traffic-light medical-access indicator."""

    if estimated_minutes is None:
        return (
            "⚫ Unknown",
            "Routing data is unavailable.",
            "gray",
        )

    if estimated_minutes <= 10:
        return (
            "🟢 Green",
            "Estimated medical access is "
            "within 10 minutes.",
            "green",
        )

    if estimated_minutes <= 20:
        return (
            "🟡 Amber",
            "Estimated medical access is "
            "within 11–20 minutes.",
            "orange",
        )

    return (
        "🔴 Red",
        "Estimated medical access exceeds "
        "20 minutes.",
        "red",
    )


def enrich_medical_routes(
    medical_records: list[dict],
    origin_latitude: float,
    origin_longitude: float,
    maximum_facilities: int = 5,
) -> list[dict]:
    """Calculate route information for nearby medical sites."""

    nearest_candidates = sorted(
        medical_records,
        key=lambda item: item[
            "Distance (km)"
        ],
    )[:maximum_facilities]

    enriched_records = []

    for record in nearest_candidates:
        route = get_road_route(
            origin_latitude,
            origin_longitude,
            record["Latitude"],
            record["Longitude"],
        )

        enriched_record = record.copy()

        if route:
            estimated_minutes = route[
                "estimated_minutes"
            ]

            (
                access_status,
                access_interpretation,
                route_colour,
            ) = classify_emergency_access(
                estimated_minutes
            )

            enriched_record.update(
                {
                    "Road distance (km)": route[
                        "road_distance_km"
                    ],
                    "Estimated drive time": (
                        f"{estimated_minutes} minutes"
                    ),
                    "Estimated minutes": (
                        estimated_minutes
                    ),
                    "Access status": (
                        access_status
                    ),
                    "Access interpretation": (
                        access_interpretation
                    ),
                    "Route geometry": route[
                        "geometry"
                    ],
                    "Route colour": route_colour,
                }
            )

        else:
            (
                access_status,
                access_interpretation,
                route_colour,
            ) = classify_emergency_access(
                None
            )

            enriched_record.update(
                {
                    "Road distance (km)": None,
                    "Estimated drive time": (
                        "Unavailable"
                    ),
                    "Estimated minutes": 999999,
                    "Access status": (
                        access_status
                    ),
                    "Access interpretation": (
                        access_interpretation
                    ),
                    "Route geometry": [],
                    "Route colour": route_colour,
                }
            )

        enriched_records.append(
            enriched_record
        )

    enriched_records.sort(
        key=lambda item: (
            item["Estimated minutes"]
            == 999999,
            item["Estimated minutes"],
        )
    )

    return enriched_records


def add_route_to_map(
    map_object: folium.Map,
    geometry: list,
    colour: str,
    tooltip: str,
) -> None:
    """Draw route geometry on the Folium map."""

    route_points = [
        [
            coordinate[1],
            coordinate[0],
        ]
        for coordinate in geometry
        if (
            isinstance(
                coordinate,
                list,
            )
            and len(coordinate) >= 2
        )
    ]

    if not route_points:
        return

    folium.PolyLine(
        locations=route_points,
        color=colour,
        weight=5,
        opacity=0.85,
        tooltip=tooltip,
    ).add_to(
        map_object
    )


# =========================================================
# HEADER
# =========================================================

st.title(
    "🌍 HeatShield Global"
)

st.markdown(
    """
    ### Interactive Workforce Climate and Operational Risk Intelligence Platform

    **Transforming live environmental and geospatial data into safer
    workforce and emergency-access decisions.**

    **Created by Mohd Khairul Ridhuan bin Mohd Fadzil, Malaysia**  
    Powered by **MARYAM — Meteorological and Risk Advisory Management System**
    """
)

st.divider()


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:
    st.header(
        "Analysis Controls"
    )

    location_input = st.text_input(
        "Enter city or location",
        value=st.session_state.get(
            "location_input_value",
            "Kajang",
        ),
        help=(
            "Examples: Kajang, Riyadh, "
            "Kuala Lumpur, Dubai, London, "
            "Tokyo or New York."
        ),
    )

    sector = st.selectbox(
        "Sector",
        list(
            SECTOR_WEIGHTS.keys()
        ),
        index=4,
    )

    intensity = st.selectbox(
        "Work intensity",
        list(
            INTENSITY_WEIGHTS.keys()
        ),
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
        list(
            VULNERABILITY_WEIGHTS.keys()
        ),
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

default_session_values = {
    "active_location": "Kajang",
    "safe_zone_results": [],
    "safe_zone_location_key": None,
    "medical_route_results": [],
    "medical_route_location_key": None,
}

for (
    session_key,
    default_value,
) in default_session_values.items():
    if session_key not in st.session_state:
        st.session_state[
            session_key
        ] = default_value


if analyse_button:
    cleaned_location = (
        location_input.strip()
    )

    if len(cleaned_location) < 2:
        st.error(
            "Enter a valid city, "
            "district or region."
        )
        st.stop()

    st.session_state.active_location = (
        cleaned_location
    )

    st.session_state.location_input_value = (
        cleaned_location
    )

    st.session_state.safe_zone_results = []

    st.session_state.safe_zone_location_key = None

    st.session_state.medical_route_results = []

    st.session_state.medical_route_location_key = None


# =========================================================
# RETRIEVE LOCATION AND ENVIRONMENTAL DATA
# =========================================================

try:
    with st.spinner(
        "Retrieving live location "
        "and environmental data..."
    ):
        location = geocode_location(
            st.session_state.active_location
        )

        if location is None:
            st.error(
                "Location not found. Enter a "
                "valid city or region."
            )
            st.stop()

        latitude = safe_float(
            location.get("latitude")
        )

        longitude = safe_float(
            location.get("longitude")
        )

        timezone_name = (
            location.get("timezone")
            or "auto"
        )

        weather_data = get_weather(
            latitude,
            longitude,
            timezone_name,
        )

        air_quality_data = (
            get_air_quality(
                latitude,
                longitude,
                timezone_name,
            )
        )

except requests.exceptions.Timeout:
    st.error(
        "The environmental-data service "
        "took too long to respond. "
        "Please try again."
    )
    st.stop()

except requests.RequestException as error:
    st.error(
        "Environmental data could not be "
        "retrieved. Please try again shortly."
    )

    with st.expander(
        "Technical error"
    ):
        st.code(
            str(error)
        )

    st.stop()


# =========================================================
# CURRENT CONDITIONS
# =========================================================

current_weather = weather_data.get(
    "current",
    {},
)

current_air = air_quality_data.get(
    "current",
    {},
)

daily_weather = weather_data.get(
    "daily",
    {},
)

temperature = safe_float(
    current_weather.get(
        "temperature_2m"
    )
)

apparent_temperature = safe_float(
    current_weather.get(
        "apparent_temperature"
    )
)

humidity = safe_float(
    current_weather.get(
        "relative_humidity_2m"
    )
)

wind_speed = safe_float(
    current_weather.get(
        "wind_speed_10m"
    )
)

precipitation = safe_float(
    current_weather.get(
        "precipitation"
    )
)

uv_index = get_current_uv(
    weather_data
)

air_quality_index = safe_float(
    current_air.get(
        "european_aqi"
    )
)

pm25 = safe_float(
    current_air.get(
        "pm2_5"
    )
)

pm10 = safe_float(
    current_air.get(
        "pm10"
    )
)

risk_score, risk_components = (
    calculate_workforce_risk(
        apparent_temperature,
        humidity,
        uv_index,
        air_quality_index,
        sector,
        intensity,
        exposure_hours,
        vulnerability,
    )
)

risk_level, risk_message = (
    classify_risk(
        risk_score
    )
)

location_name = location.get(
    "name",
    st.session_state.active_location,
)

country_name = location.get(
    "country",
    "Unknown country",
)

administrative_area = location.get(
    "admin1"
)

display_location = (
    f"{location_name}, "
    f"{country_name}"
)

if (
    administrative_area
    and administrative_area
    != location_name
):
    display_location = (
        f"{location_name}, "
        f"{administrative_area}, "
        f"{country_name}"
    )

sunrise_values = daily_weather.get(
    "sunrise",
    [],
)

sunset_values = daily_weather.get(
    "sunset",
    [],
)

sunrise = (
    sunrise_values[0]
    if sunrise_values
    else "Unavailable"
)

sunset = (
    sunset_values[0]
    if sunset_values
    else "Unavailable"
)


# =========================================================
# LOCATION AND TIME
# =========================================================

st.subheader(
    "Live Location and Time Intelligence"
)

location_column, time_column, status_column = (
    st.columns(3)
)

location_column.metric(
    "Selected location",
    display_location,
)

time_column.metric(
    "Location date and time",
    current_weather.get(
        "time",
        "Unavailable",
    ),
)

status_column.metric(
    "Data status",
    "Live / Near-real-time",
)

st.caption(
    f"Timezone: {timezone_name} | "
    f"Coordinates: "
    f"{latitude:.5f}, {longitude:.5f} | "
    f"Retrieved: "
    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
)


# =========================================================
# ENVIRONMENTAL CONDITIONS
# =========================================================

st.subheader(
    "Environmental Conditions"
)

environment_columns = st.columns(4)

environment_columns[0].metric(
    "Temperature",
    f"{temperature:.1f} °C",
)

environment_columns[1].metric(
    "Feels like",
    f"{apparent_temperature:.1f} °C",
)

environment_columns[2].metric(
    "Humidity",
    f"{humidity:.0f}%",
)

environment_columns[3].metric(
    "UV index",
    f"{uv_index:.1f}",
)

air_columns = st.columns(4)

air_columns[0].metric(
    "European AQI",
    f"{air_quality_index:.0f}",
)

air_columns[1].metric(
    "PM2.5",
    f"{pm25:.1f} μg/m³",
)

air_columns[2].metric(
    "PM10",
    f"{pm10:.1f} μg/m³",
)

air_columns[3].metric(
    "Wind speed",
    f"{wind_speed:.1f} km/h",
)

additional_columns = st.columns(3)

additional_columns[0].info(
    f"**Sunrise:** {sunrise}"
)

additional_columns[1].info(
    f"**Sunset:** {sunset}"
)

additional_columns[2].info(
    f"**Current precipitation:** "
    f"{precipitation:.1f} mm"
)


# =========================================================
# RISK INTELLIGENCE
# =========================================================

st.subheader(
    "Workforce Risk Intelligence"
)

risk_column, explanation_column = (
    st.columns(
        [1, 2]
    )
)

with risk_column:
    st.metric(
        "Workforce Risk Score",
        f"{risk_score}/100",
    )

    st.markdown(
        f"## {risk_level}"
    )

    st.write(
        risk_message
    )

with explanation_column:
    component_dataframe = pd.DataFrame(
        {
            "Risk component": list(
                risk_components.keys()
            ),
            "Contribution": list(
                risk_components.values()
            ),
        }
    )

    st.bar_chart(
        component_dataframe.set_index(
            "Risk component"
        )
    )

    with st.expander(
        "View risk contribution table"
    ):
        st.dataframe(
            component_dataframe,
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# MARYAM MITIGATION CENTRE
# =========================================================

st.subheader(
    "MARYAM Live Mitigation Centre"
)

mitigation_plan = build_mitigation_plan(
    risk_score,
    apparent_temperature,
    humidity,
    uv_index,
    air_quality_index,
    sector,
    intensity,
)

for (
    recommendation_number,
    recommendation,
) in enumerate(
    mitigation_plan,
    start=1,
):
    st.markdown(
        f"**{recommendation_number}.** "
        f"{recommendation}"
    )


if risk_score >= 65:
    st.error(
        """
        **Emergency escalation notice**

        If a person becomes confused, collapses, experiences
        seizures, loses consciousness, or shows signs of severe
        heat illness, contact local emergency services immediately.

        Move the person away from direct heat while awaiting
        trained medical assistance.
        """
    )


# =========================================================
# MAIN MAP
# =========================================================

st.subheader(
    "Interactive Location Map"
)

main_map = folium.Map(
    location=[
        latitude,
        longitude,
    ],
    zoom_start=12,
    control_scale=True,
)

folium.Circle(
    location=[
        latitude,
        longitude,
    ],
    radius=10000,
    color=risk_marker_colour(
        risk_score
    ),
    fill=True,
    fill_opacity=0.08,
    tooltip=(
        "Maximum 10 km safe-zone "
        "scanning radius"
    ),
).add_to(
    main_map
)

folium.Marker(
    location=[
        latitude,
        longitude,
    ],
    tooltip=display_location,
    popup=(
        f"<b>{display_location}</b><br>"
        f"Risk: {risk_score}/100 — "
        f"{risk_level}<br>"
        f"Feels like: "
        f"{apparent_temperature:.1f} °C"
    ),
    icon=folium.Icon(
        color=risk_marker_colour(
            risk_score
        ),
        icon="info-sign",
    ),
).add_to(
    main_map
)

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

st.subheader(
    "🛟 MARYAM Global Safe-Zone Scanner"
)

st.markdown(
    """
    Results depend on OpenStreetMap completeness.

    A listed location is not automatically confirmed as open,
    cooler, air-conditioned or medically appropriate.
    """
)

scanner_radius_column, scanner_category_column = (
    st.columns(
        [1, 2]
    )
)

with scanner_radius_column:
    radius_km = st.select_slider(
        "Scanning radius",
        options=[
            1,
            2,
            3,
            5,
            7,
            10,
        ],
        value=5,
        format_func=lambda value: (
            f"{value} km"
        ),
    )

with scanner_category_column:
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
    tuple(
        sorted(
            selected_categories
        )
    ),
)


if scan_button:
    if not selected_categories:
        st.warning(
            "Select at least one "
            "facility category."
        )

    else:
        try:
            with st.spinner(
                f"Scanning facilities within "
                f"{radius_km} km..."
            ):
                overpass_query = (
                    build_overpass_query(
                        latitude,
                        longitude,
                        radius_km * 1000,
                        tuple(
                            selected_categories
                        ),
                    )
                )

                overpass_data = (
                    query_overpass(
                        overpass_query
                    )
                )

                st.session_state.safe_zone_results = (
                    parse_safe_zones(
                        overpass_data,
                        latitude,
                        longitude,
                    )
                )

                st.session_state.safe_zone_location_key = (
                    current_location_key
                )

                st.session_state.medical_route_results = []

                st.session_state.medical_route_location_key = None

        except requests.RequestException as error:
            st.warning(
                "Nearby facilities could not be "
                "retrieved. Reduce the radius "
                "or try again."
            )

            with st.expander(
                "Technical error"
            ):
                st.code(
                    str(error)
                )


safe_zone_results = (
    st.session_state.safe_zone_results
)

results_match_location = (
    st.session_state.safe_zone_location_key
    == current_location_key
)


if (
    safe_zone_results
    and results_match_location
):
    st.success(
        f"Found {len(safe_zone_results)} "
        f"potential safety-support locations "
        f"within {radius_km} km."
    )

    safe_zone_dataframe = pd.DataFrame(
        safe_zone_results
    )

    emergency_dataframe = (
        safe_zone_dataframe[
            safe_zone_dataframe[
                "Category"
            ].isin(
                [
                    "Hospital",
                    "Clinic or medical centre",
                    "Pharmacy",
                    "Fire and rescue service",
                    "Police station",
                ]
            )
        ]
    )

    shelter_dataframe = (
        safe_zone_dataframe[
            safe_zone_dataframe[
                "Category"
            ].isin(
                [
                    "Mosque",
                    "Place of worship",
                    "Shopping or retail facility",
                    "Library",
                    "Community centre",
                    "Public shelter",
                ]
            )
        ]
    )

    hydration_dataframe = (
        safe_zone_dataframe[
            safe_zone_dataframe[
                "Category"
            ].isin(
                [
                    "Drinking-water point",
                    "Food and drink facility",
                    "Park or green area",
                ]
            )
        ]
    )

    result_tabs = st.tabs(
        [
            "All locations",
            "Emergency medical",
            "Shelter and cooling",
            "Hydration and rest",
        ]
    )

    display_columns = [
        "Name",
        "Category",
        "Potential use",
        "Distance (km)",
        "Opening hours",
        "Navigation",
    ]

    result_dataframes = [
        safe_zone_dataframe,
        emergency_dataframe,
        shelter_dataframe,
        hydration_dataframe,
    ]

    for result_tab, result_dataframe in zip(
        result_tabs,
        result_dataframes,
    ):
        with result_tab:
            if result_dataframe.empty:
                st.info(
                    "No mapped facilities were "
                    "found in this category."
                )

            else:
                st.dataframe(
                    result_dataframe[
                        display_columns
                    ],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Navigation": (
                            st.column_config.LinkColumn(
                                "Navigation",
                                display_text=(
                                    "Open map"
                                ),
                            )
                        )
                    },
                )

    st.subheader(
        "Interactive Safe-Zone Map"
    )

    safe_zone_map = folium.Map(
        location=[
            latitude,
            longitude,
        ],
        zoom_start=13,
        control_scale=True,
    )

    folium.Circle(
        location=[
            latitude,
            longitude,
        ],
        radius=radius_km * 1000,
        color=risk_marker_colour(
            risk_score
        ),
        fill=True,
        fill_opacity=0.05,
    ).add_to(
        safe_zone_map
    )

    folium.Marker(
        location=[
            latitude,
            longitude,
        ],
        tooltip="Current selected location",
        popup=(
            f"<b>{display_location}</b><br>"
            f"Risk: {risk_score}/100 — "
            f"{risk_level}"
        ),
        icon=folium.Icon(
            color="black",
            icon="home",
        ),
    ).add_to(
        safe_zone_map
    )

    for place in safe_zone_results:
        folium.Marker(
            location=[
                place["Latitude"],
                place["Longitude"],
            ],
            tooltip=place["Name"],
            popup=(
                f"<b>{place['Name']}</b><br>"
                f"Category: "
                f"{place['Category']}<br>"
                f"Distance: "
                f"{place['Distance (km)']} km<br>"
                f"Potential use: "
                f"{place['Potential use']}<br>"
                f"Opening hours: "
                f"{place['Opening hours']}"
            ),
            icon=folium.Icon(
                color=facility_marker_colour(
                    place["Category"]
                ),
                icon="info-sign",
            ),
        ).add_to(
            safe_zone_map
        )

    st_folium(
        safe_zone_map,
        height=600,
        use_container_width=True,
        key="safe_zone_map",
    )

elif scan_button:
    st.info(
        "No mapped facilities were found. "
        "Try a larger radius or other categories."
    )

else:
    st.info(
        "Select a radius and facility categories, "
        "then press **Scan Nearby Safety Options**."
    )


# =========================================================
# EMERGENCY REACHABILITY ENGINE
# =========================================================

st.divider()

st.subheader(
    "🚑 MARYAM Emergency Reachability Engine"
)

st.markdown(
    """
    This module estimates road distance and driving time to
    mapped hospitals and clinics.

    It does not include live traffic, ambulance availability,
    hospital capacity or guaranteed arrival time.
    """
)

medical_candidates = []

if (
    safe_zone_results
    and results_match_location
):
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
        "**Hospitals and clinics** selected first."
    )

else:
    st.write(
        f"{len(medical_candidates)} mapped "
        f"hospitals or clinics are available "
        f"for route analysis."
    )

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
                    place["Name"],
                    place["Latitude"],
                    place["Longitude"],
                )
                for place in medical_candidates
            )
        ),
    )

    if calculate_routes_button:
        with st.spinner(
            "Calculating road routes to "
            "nearby medical facilities..."
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

    if (
        medical_route_results
        and route_results_match_location
    ):
        fastest_available = next(
            (
                place
                for place
                in medical_route_results
                if place[
                    "Estimated drive time"
                ] != "Unavailable"
            ),
            None,
        )

        fastest_hospital = next(
            (
                place
                for place
                in medical_route_results
                if (
                    place["Category"]
                    == "Hospital"
                    and place[
                        "Estimated drive time"
                    ] != "Unavailable"
                )
            ),
            None,
        )

        fastest_clinic = next(
            (
                place
                for place
                in medical_route_results
                if (
                    place["Category"]
                    == "Clinic or medical centre"
                    and place[
                        "Estimated drive time"
                    ] != "Unavailable"
                )
            ),
            None,
        )

        if fastest_available:
            (
                access_status,
                access_interpretation,
                _,
            ) = classify_emergency_access(
                fastest_available[
                    "Estimated minutes"
                ]
            )

            summary_columns = st.columns(3)

            summary_columns[0].metric(
                "Fastest mapped medical option",
                fastest_available["Name"],
            )

            summary_columns[1].metric(
                "Estimated driving time",
                fastest_available[
                    "Estimated drive time"
                ],
            )

            summary_columns[2].metric(
                "Medical-access indicator",
                access_status,
            )

            st.info(
                access_interpretation
            )

            hospital_column, clinic_column = (
                st.columns(2)
            )

            if fastest_hospital:
                hospital_column.success(
                    "**Fastest mapped hospital:** "
                    f"{fastest_hospital['Name']} — "
                    f"{fastest_hospital['Estimated drive time']}"
                )

            else:
                hospital_column.warning(
                    "No routable mapped hospital "
                    "was found among the analysed "
                    "facilities."
                )

            if fastest_clinic:
                clinic_column.info(
                    "**Fastest mapped clinic:** "
                    f"{fastest_clinic['Name']} — "
                    f"{fastest_clinic['Estimated drive time']}"
                )

            else:
                clinic_column.info(
                    "No routable mapped clinic "
                    "was found among the analysed "
                    "facilities."
                )

            if (
                fastest_available["Category"]
                == "Clinic or medical centre"
            ):
                st.warning(
                    "The fastest mapped option is a clinic. "
                    "It may not provide emergency care for "
                    "severe heatstroke; a hospital may be "
                    "more appropriate."
                )

        route_dataframe = pd.DataFrame(
            medical_route_results
        )

        route_columns = [
            "Name",
            "Category",
            "Road distance (km)",
            "Estimated drive time",
            "Access status",
            "Access interpretation",
            "Opening hours",
            "Navigation",
        ]

        st.dataframe(
            route_dataframe[
                route_columns
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Navigation": (
                    st.column_config.LinkColumn(
                        "Navigation",
                        display_text="Open map",
                    )
                )
            },
        )

        st.subheader(
            "Emergency Medical Route Map"
        )

        emergency_route_map = folium.Map(
            location=[
                latitude,
                longitude,
            ],
            zoom_start=13,
            control_scale=True,
        )

        folium.Marker(
            location=[
                latitude,
                longitude,
            ],
            tooltip="Current selected location",
            popup=(
                f"<b>{display_location}</b><br>"
                f"Risk: {risk_score}/100 — "
                f"{risk_level}"
            ),
            icon=folium.Icon(
                color="black",
                icon="home",
            ),
        ).add_to(
            emergency_route_map
        )

        for (
            route_position,
            medical_place,
        ) in enumerate(
            medical_route_results,
            start=1,
        ):
            add_route_to_map(
                emergency_route_map,
                medical_place[
                    "Route geometry"
                ],
                medical_place[
                    "Route colour"
                ],
                (
                    f"Route {route_position}: "
                    f"{medical_place['Name']} — "
                    f"{medical_place['Estimated drive time']}"
                ),
            )

            if (
                medical_place[
                    "Road distance (km)"
                ]
                is not None
            ):
                road_distance_text = (
                    f"{medical_place['Road distance (km)']} km"
                )

            else:
                road_distance_text = (
                    "Unavailable"
                )

            folium.Marker(
                location=[
                    medical_place["Latitude"],
                    medical_place["Longitude"],
                ],
                tooltip=medical_place["Name"],
                popup=(
                    f"<b>{route_position}. "
                    f"{medical_place['Name']}</b><br>"
                    f"Category: "
                    f"{medical_place['Category']}<br>"
                    f"Road distance: "
                    f"{road_distance_text}<br>"
                    f"Estimated drive: "
                    f"{medical_place['Estimated drive time']}<br>"
                    f"Status: "
                    f"{medical_place['Access status']}"
                ),
                icon=folium.Icon(
                    color=(
                        "red"
                        if medical_place["Category"]
                        == "Hospital"
                        else "orange"
                    ),
                    icon="plus-sign",
                ),
            ).add_to(
                emergency_route_map
            )

        st_folium(
            emergency_route_map,
            height=600,
            use_container_width=True,
            key="emergency_route_map",
        )

        st.caption(
            "Green, amber and red indicators "
            "represent estimated road-access time only. "
            "They do not represent live traffic or "
            "ambulance response time."
        )

    else:
        st.info(
            "Press **Calculate Fastest Medical Access** "
            "to calculate routes and estimated driving time."
        )


# =========================================================
# 24-HOUR FORECAST
# =========================================================

st.divider()

st.subheader(
    "24-Hour Environmental Forecast"
)

hourly_weather = weather_data.get(
    "hourly",
    {},
)

forecast_dataframe = pd.DataFrame(
    {
        "Time": hourly_weather.get(
            "time",
            [],
        )[:24],
        "Temperature": hourly_weather.get(
            "temperature_2m",
            [],
        )[:24],
        "Feels Like": hourly_weather.get(
            "apparent_temperature",
            [],
        )[:24],
        "Humidity": hourly_weather.get(
            "relative_humidity_2m",
            [],
        )[:24],
        "UV Index": hourly_weather.get(
            "uv_index",
            [],
        )[:24],
        "Rain probability": hourly_weather.get(
            "precipitation_probability",
            [],
        )[:24],
    }
)

if not forecast_dataframe.empty:
    forecast_dataframe["Time"] = (
        pd.to_datetime(
            forecast_dataframe["Time"]
        )
    )

    st.line_chart(
        forecast_dataframe.set_index(
            "Time"
        )[
            [
                "Temperature",
                "Feels Like",
                "UV Index",
            ]
        ]
    )

    with st.expander(
        "View 24-hour forecast table"
    ):
        st.dataframe(
            forecast_dataframe,
            use_container_width=True,
            hide_index=True,
        )

else:
    st.info(
        "Hourly forecast data is "
        "currently unavailable."
    )


# =========================================================
# IMPACT AND GOVERNANCE
# =========================================================

st.divider()

impact_columns = st.columns(3)

impact_columns[0].markdown(
    "### Social Impact"
)

impact_columns[0].write(
    "Supports protection of outdoor, "
    "vulnerable and heat-exposed workers."
)

impact_columns[1].markdown(
    "### Business Impact"
)

impact_columns[1].write(
    "Supports workforce planning, "
    "occupational safety and "
    "operational continuity."
)

impact_columns[2].markdown(
    "### National Impact"
)

impact_columns[2].write(
    "Supports climate resilience, "
    "labour protection and "
    "smart-city planning."
)


with st.expander(
    "Methodology, data sources and limitations"
):
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

        - Safe-zone distances are straight-line estimates.
        - Emergency-route distance and travel time use
          road-network routing estimates.
        - Route times do not include live traffic conditions.
        - Route time is not ambulance response time.
        - Opening hours may be missing or outdated.
        - A listed facility is not automatically open,
          cooler, air-conditioned or medically appropriate.
        - OpenStreetMap coverage differs between locations.
        - This platform does not replace emergency services,
          medical assessment or occupational-health professionals.
        """
    )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "HeatShield Global | Created by "
    "Mohd Khairul Ridhuan bin Mohd Fadzil, Malaysia | "
    "Powered by MARYAM | Prototype Version 2.2 | 2026"
)
