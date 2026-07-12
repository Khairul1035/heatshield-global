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
    """Keep a value between minimum and maximum."""
    return max(minimum, min(value, maximum))


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value safely to float."""
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

    results = response.json().get("results", [])

    if not results:
        return None

    return results[0]


@st.cache_data(ttl=900)
def get_weather(
    latitude: float,
    longitude: float,
    timezone_name: str,
) -> dict:
    """Retrieve current and hourly weather data."""

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
    """Retrieve current and hourly air-quality data."""

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
    """Find the UV value nearest to the current forecast time."""

    current_time = weather_data.get("current", {}).get("time")
    hourly_data = weather_data.get("hourly", {})

    hourly_times = hourly_data.get("time", [])
    uv_values = hourly_data.get("uv_index", [])

    if not uv_values:
        return 0.0

    if current_time in hourly_times:
        position = hourly_times.index(current_time)
        return safe_float(uv_values[position])

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
    """Calculate explainable environmental risk components."""

    temperature_score = clamp(
        (apparent_temperature - 20) * 2.4,
        0,
        45,
    )

    humidity_score = clamp(
        (humidity - 35) * 0.25,
        0,
        15,
    )

    uv_score = clamp(
        uv_index * 2,
        0,
        15,
    )

    air_quality_score = clamp(
        air_quality_index * 0.18,
        0,
        15,
    )

    return {
        "Apparent temperature": round(temperature_score, 1),
        "Humidity": round(humidity_score, 1),
        "UV exposure": round(uv_score, 1),
        "Air quality": round(air_quality_score, 1),
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
    """Calculate the combined workforce risk score."""

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
    """Classify the workforce risk score."""

    if score < 25:
        return (
            "Low",
            "Normal precautions and routine environmental monitoring.",
        )

    if score < 45:
        return (
            "Moderate",
            "Increase hydration and continue active monitoring.",
        )

    if score < 65:
        return (
            "High",
            "Reduce continuous exposure and increase recovery periods.",
        )

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
    """Return Folium marker colour for a risk score."""

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
    """Generate the MARYAM mitigation plan."""

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

    if sector in {
        "Construction",
        "Heavy manual labour",
        "Agriculture",
    }:
        recommendations.append(
            "Implement buddy monitoring and documented supervisor heat checks."
        )

    recommendations.append(
        "Escalate immediately if a person becomes confused, collapses, "
        "experiences seizures or loses consciousness."
    )

    return recommendations


# =========================================================
# SAFE-ZONE SCANNER
# =========================================================

def build_overpass_query(
    latitude: float,
    longitude: float,
    radius_metres: int,
    selected_categories: tuple[str, ...],
) -> str:
    """Build an Overpass QL query based on selected categories."""

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

    joined_queries = "\n".join(queries)

    return f"""
    [out:json][timeout:30];
    (
        {joined_queries}
    );
    out center tags;
    """


@st.cache_data(ttl=1800, show_spinner=False)
def query_overpass(
    query: str,
) -> dict:
    """Call a public Overpass API with fallback endpoints."""

    last_error = None

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                timeout=40,
                headers={
                    "User-Agent": (
                        "HeatShieldGlobal/1.0 "
                        "research-decision-support-prototype"
                    )
                },
            )

            response.raise_for_status()
            return response.json()

        except requests.RequestException as error:
            last_error = error
            continue

    if last_error:
        raise last_error

    return {"elements": []}


def get_element_coordinates(
    element: dict,
) -> tuple[float | None, float | None]:
    """Get coordinates for nodes, ways and relations."""

    latitude = element.get("lat")
    longitude = element.get("lon")

    if latitude is not None and longitude is not None:
        return safe_float(latitude), safe_float(longitude)

    centre = element.get("center", {})

    if centre.get("lat") is not None and centre.get("lon") is not None:
        return (
            safe_float(centre.get("lat")),
            safe_float(centre.get("lon")),
        )

    return None, None


def classify_place(tags: dict) -> tuple[str, str, int]:
    """Classify an OpenStreetMap feature."""

    amenity = tags.get("amenity", "")
    healthcare = tags.get("healthcare", "")
    shop = tags.get("shop", "")
    building = tags.get("building", "")
    leisure = tags.get("leisure", "")
    natural = tags.get("natural", "")
    religion = tags.get("religion", "")

    if amenity == "hospital" or healthcare == "hospital":
        return (
            "Hospital",
            "Emergency medical support",
            1,
        )

    if amenity in {"clinic", "doctors"} or healthcare in {
        "clinic",
        "doctor",
    }:
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
            6,
        )

    if shop == "mall" or building == "retail":
        return (
            "Shopping or retail facility",
            "Potential indoor cooling and temporary recovery",
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
    """Convert Overpass results into safe-zone records."""

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

        opening_hours = tags.get(
            "opening_hours",
            "Not available — verify before travelling",
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
                "Opening hours": opening_hours,
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
    """Assign marker colours based on facility category."""

    if category == "Hospital":
        return "red"

    if category == "Clinic or medical centre":
        return "darkred"

    if category == "Pharmacy":
        return "pink"

    if category in {
        "Fire and rescue service",
        "Police station",
    }:
        return "darkblue"

    if category in {
        "Mosque",
        "Place of worship",
    }:
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
        value=st.session_state.get(
            "location_input_value",
            "Kajang",
        ),
        help=(
            "Examples: Kajang, Riyadh, Kuala Lumpur, "
            "Dubai, London, Tokyo or New York."
        ),
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

    st.caption(
        "The prototype uses public APIs and open web technologies."
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

if analyse_button:
    cleaned_location = location_input.strip()

    invalid_symbols = any(
        symbol in cleaned_location
        for symbol in ["_", "{", "}", "[", "]", "<", ">"]
    )

    if len(cleaned_location) < 2 or invalid_symbols:
        st.error(
            "Location error: Enter a valid city, district or region."
        )
        st.stop()

    st.session_state.active_location = cleaned_location
    st.session_state.location_input_value = cleaned_location
    st.session_state.safe_zone_results = []
    st.session_state.safe_zone_location_key = None


# =========================================================
# RETRIEVE LOCATION AND ENVIRONMENTAL DATA
# =========================================================

active_location = st.session_state.active_location

try:
    with st.spinner(
        "Retrieving live location and environmental data..."
    ):
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
        "The public environmental-data service took too long to respond. "
        "Please try again."
    )
    st.stop()

except requests.exceptions.RequestException as error:
    st.error(
        "Environmental data could not be retrieved. "
        "Please try again shortly."
    )

    with st.expander("Technical error"):
        st.code(str(error))

    st.stop()


# =========================================================
# CURRENT CONDITIONS
# =========================================================

current_weather = weather_data.get("current", {})
current_air = air_quality_data.get("current", {})
daily_weather = weather_data.get("daily", {})

temperature = safe_float(
    current_weather.get("temperature_2m")
)

apparent_temperature = safe_float(
    current_weather.get("apparent_temperature")
)

humidity = safe_float(
    current_weather.get("relative_humidity_2m")
)

wind_speed = safe_float(
    current_weather.get("wind_speed_10m")
)

precipitation = safe_float(
    current_weather.get("precipitation")
)

uv_index = get_current_uv(weather_data)

air_quality_index = safe_float(
    current_air.get("european_aqi")
)

pm25 = safe_float(
    current_air.get("pm2_5")
)

pm10 = safe_float(
    current_air.get("pm10")
)

risk_score, risk_components = calculate_workforce_risk(
    apparent_temperature=apparent_temperature,
    humidity=humidity,
    uv_index=uv_index,
    air_quality_index=air_quality_index,
    sector=sector,
    intensity=intensity,
    exposure_hours=exposure_hours,
    vulnerability=vulnerability,
)

risk_level, risk_message = classify_risk(risk_score)

location_name = location.get("name", active_location)
country_name = location.get("country", "Unknown country")
administrative_area = location.get("admin1")

display_location = f"{location_name}, {country_name}"

if (
    administrative_area
    and administrative_area != location_name
):
    display_location = (
        f"{location_name}, {administrative_area}, "
        f"{country_name}"
    )

local_timestamp = current_weather.get(
    "time",
    "Unavailable",
)

sunrise_values = daily_weather.get("sunrise", [])
sunset_values = daily_weather.get("sunset", [])

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

retrieved_utc = datetime.now(timezone.utc).strftime(
    "%Y-%m-%d %H:%M:%S UTC"
)


# =========================================================
# LOCATION AND TIME
# =========================================================

st.subheader("Live Location and Time Intelligence")

location_col, time_col, status_col = st.columns(3)

with location_col:
    st.metric(
        "Selected location",
        display_location,
    )

with time_col:
    st.metric(
        "Location date and time",
        local_timestamp,
    )

with status_col:
    st.metric(
        "Data status",
        "Live / Near-real-time",
    )

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
    st.metric(
        "Temperature",
        f"{temperature:.1f} °C",
    )

with kpi_2:
    st.metric(
        "Feels like",
        f"{apparent_temperature:.1f} °C",
    )

with kpi_3:
    st.metric(
        "Humidity",
        f"{humidity:.0f}%",
    )

with kpi_4:
    st.metric(
        "UV index",
        f"{uv_index:.1f}",
    )

kpi_5, kpi_6, kpi_7, kpi_8 = st.columns(4)

with kpi_5:
    st.metric(
        "European AQI",
        f"{air_quality_index:.0f}",
    )

with kpi_6:
    st.metric(
        "PM2.5",
        f"{pm25:.1f} μg/m³",
    )

with kpi_7:
    st.metric(
        "PM10",
        f"{pm10:.1f} μg/m³",
    )

with kpi_8:
    st.metric(
        "Wind speed",
        f"{wind_speed:.1f} km/h",
    )

time_1, time_2, rain_col = st.columns(3)

with time_1:
    st.info(f"**Sunrise:** {sunrise}")

with time_2:
    st.info(f"**Sunset:** {sunset}")

with rain_col:
    st.info(
        f"**Current precipitation:** {precipitation:.1f} mm"
    )


# =========================================================
# RISK INTELLIGENCE
# =========================================================

st.subheader("Workforce Risk Intelligence")

risk_col, explanation_col = st.columns([1, 2])

with risk_col:
    st.metric(
        "Workforce Risk Score",
        f"{risk_score}/100",
    )

    st.markdown(f"## {risk_level}")
    st.write(risk_message)

with explanation_col:
    st.markdown("#### Explainable risk components")

    component_df = pd.DataFrame(
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
        component_df.set_index("Risk component")
    )

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

st.markdown(
    """
    **MARYAM evaluates current environmental exposure, work characteristics
    and worker vulnerability to recommend immediate and preventive actions.**
    """
)

mitigation_plan = build_mitigation_plan(
    score=risk_score,
    apparent_temperature=apparent_temperature,
    humidity=humidity,
    uv_index=uv_index,
    air_quality_index=air_quality_index,
    sector=sector,
    intensity=intensity,
)

for number, recommendation in enumerate(
    mitigation_plan,
    start=1,
):
    st.markdown(
        f"**{number}.** {recommendation}"
    )


# =========================================================
# EMERGENCY PANEL
# =========================================================

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

st.markdown(
    """
    Scan nearby facilities that may support emergency response,
    temporary shelter, hydration or heat-risk mitigation.

    Results depend on the completeness of OpenStreetMap data.
    A listed location is **not automatically confirmed as cooler,
    open, air-conditioned or medically appropriate**.
    """
)

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
        st.warning(
            "Select at least one facility category."
        )
    else:
        try:
            with st.spinner(
                f"Scanning facilities within {radius_km} km..."
            ):
                query = build_overpass_query(
                    latitude=latitude,
                    longitude=longitude,
                    radius_metres=radius_km * 1000,
                    selected_categories=tuple(
                        selected_categories
                    ),
                )

                overpass_data = query_overpass(query)

                safe_zone_results = parse_safe_zones(
                    overpass_data=overpass_data,
                    origin_latitude=latitude,
                    origin_longitude=longitude,
                )

                st.session_state.safe_zone_results = (
                    safe_zone_results
                )

                st.session_state.safe_zone_location_key = (
                    current_location_key
                )

        except requests.exceptions.Timeout:
            st.warning(
                "The global safe-zone service took too long to respond. "
                "Please reduce the radius or try again."
            )

        except requests.exceptions.RequestException as error:
            st.warning(
                "Nearby facilities could not be retrieved from the "
                "public OpenStreetMap service. Please try again."
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

    safe_zone_df = pd.DataFrame(
        safe_zone_results
    )

    emergency_df = safe_zone_df[
        safe_zone_df["Category"].isin(
            [
                "Hospital",
                "Clinic or medical centre",
                "Pharmacy",
                "Fire and rescue service",
                "Police station",
            ]
        )
    ].copy()

    shelter_df = safe_zone_df[
        safe_zone_df["Category"].isin(
            [
                "Mosque",
                "Place of worship",
                "Shopping or retail facility",
                "Library",
                "Community centre",
                "Public shelter",
            ]
        )
    ].copy()

    hydration_df = safe_zone_df[
        safe_zone_df["Category"].isin(
            [
                "Drinking-water point",
                "Food and drink facility",
                "Park or green area",
            ]
        )
    ].copy()

    result_tab_1, result_tab_2, result_tab_3, result_tab_4 = (
        st.tabs(
            [
                "All locations",
                "Emergency medical",
                "Shelter and cooling",
                "Hydration and rest",
            ]
        )
    )

    display_columns = [
        "Name",
        "Category",
        "Potential use",
        "Distance (km)",
        "Opening hours",
        "Navigation",
    ]

    with result_tab_1:
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

    with result_tab_2:
        if emergency_df.empty:
            st.info(
                "No mapped medical or emergency facilities were "
                "found within the selected radius."
            )
        else:
            st.dataframe(
                emergency_df[display_columns],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Navigation": st.column_config.LinkColumn(
                        "Open navigation",
                        display_text="Open map",
                    )
                },
            )

    with result_tab_3:
        if shelter_df.empty:
            st.info(
                "No mapped shelter or indoor-recovery options were "
                "found within the selected radius."
            )
        else:
            st.dataframe(
                shelter_df[display_columns],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Navigation": st.column_config.LinkColumn(
                        "Open navigation",
                        display_text="Open map",
                    )
                },
            )

    with result_tab_4:
        if hydration_df.empty:
            st.info(
                "No mapped hydration, food or green-area options were "
                "found within the selected radius."
            )
        else:
            st.dataframe(
                hydration_df[display_columns],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Navigation": st.column_config.LinkColumn(
                        "Open navigation",
                        display_text="Open map",
                    )
                },
            )

    # SAFE-ZONE MAP
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
        tooltip=f"{radius_km} km scanning radius",
    ).add_to(safe_zone_map)

    folium.Marker(
        location=[latitude, longitude],
        tooltip="Current selected location",
        popup=(
            f"<b>{display_location}</b><br>"
            f"Current risk: {risk_score}/100 — {risk_level}"
        ),
        icon=folium.Icon(
            color="black",
            icon="home",
        ),
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

elif scan_button and not safe_zone_results:
    st.info(
        "No mapped facilities were found. Try a larger radius "
        "or select additional categories."
    )

else:
    st.info(
        "Select a radius and facility categories, then press "
        "**Scan Nearby Safety Options**."
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
    forecast_df["Time"] = pd.to_datetime(
        forecast_df["Time"]
    )

    st.line_chart(
        forecast_df.set_index("Time")[
            [
                "Temperature",
                "Feels Like",
                "UV Index",
            ]
        ]
    )

    with st.expander("View 24-hour forecast table"):
        st.dataframe(
            forecast_df,
            use_container_width=True,
            hide_index=True,
        )

else:
    st.info(
        "Hourly forecast data is currently unavailable."
    )


# =========================================================
# IMPACT AND GOVERNANCE
# =========================================================

st.divider()

impact_1, impact_2, impact_3 = st.columns(3)

with impact_1:
    st.markdown("### Social Impact")
    st.write(
        "Supports protection of outdoor, vulnerable and "
        "heat-exposed workers."
    )

with impact_2:
    st.markdown("### Business Impact")
    st.write(
        "Supports workforce planning, occupational safety "
        "and operational continuity."
    )

with impact_3:
    st.markdown("### National Impact")
    st.write(
        "Supports climate resilience, labour protection "
        "and smart-city planning."
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

        **Risk methodology**

        The workforce risk score combines:

        - apparent temperature;
        - humidity;
        - UV exposure;
        - air quality;
        - sector exposure;
        - work intensity;
        - continuous exposure duration; and
        - worker vulnerability.

        **Safe-zone methodology**

        Nearby facilities are identified through OpenStreetMap
        tags and ranked primarily by their potential emergency,
        shelter or hydration function and straight-line distance.

        **Important limitations**

        - Distances are straight-line estimates, not confirmed road distances.
        - Opening hours may be absent or outdated.
        - A listed building is not necessarily open or air-conditioned.
        - A park is not necessarily cooler or safe during extreme heat.
        - OpenStreetMap coverage differs between countries and regions.
        - The platform does not replace occupational-health professionals,
          medical assessment, emergency services or legal requirements.
        """
    )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "HeatShield Global | Created by Mohd Khairul Ridhuan "
    "bin Mohd Fadzil, Malaysia | Powered by MARYAM | "
    "Prototype Version 2.0 | 2026"
)
