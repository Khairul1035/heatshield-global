import math
from datetime import datetime

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium


# ---------------------------------------------------------
# PAGE CONFIGURATION
# ---------------------------------------------------------

st.set_page_config(
    page_title="HeatShield Global",
    page_icon="🌍",
    layout="wide",
)


# ---------------------------------------------------------
# PROJECT CONSTANTS
# ---------------------------------------------------------

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


SECTOR_WEIGHTS = {
    "Office": 0,
    "Security": 8,
    "Delivery and Logistics": 12,
    "Construction": 18,
    "Agriculture": 20,
    "Heavy Manual Labour": 25,
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


# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------

def clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    """Keep a number within a specified range."""
    return max(minimum, min(value, maximum))


@st.cache_data(ttl=1800)
def geocode_location(location_name: str) -> dict | None:
    """Convert a city or location name into coordinates."""

    params = {
        "name": location_name,
        "count": 1,
        "language": "en",
        "format": "json",
    }

    response = requests.get(
        GEOCODING_URL,
        params=params,
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()
    results = data.get("results", [])

    if not results:
        return None

    return results[0]


@st.cache_data(ttl=900)
def get_weather(latitude: float, longitude: float, timezone: str) -> dict:
    """Retrieve current and hourly weather information."""

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "apparent_temperature,"
            "weather_code,"
            "wind_speed_10m"
        ),
        "hourly": (
            "temperature_2m,"
            "apparent_temperature,"
            "relative_humidity_2m,"
            "uv_index"
        ),
        "forecast_days": 2,
        "timezone": timezone,
    }

    response = requests.get(
        WEATHER_URL,
        params=params,
        timeout=20,
    )
    response.raise_for_status()

    return response.json()


@st.cache_data(ttl=900)
def get_air_quality(latitude: float, longitude: float, timezone: str) -> dict:
    """Retrieve current and hourly air-quality information."""

    params = {
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
        "timezone": timezone,
    }

    response = requests.get(
        AIR_QUALITY_URL,
        params=params,
        timeout=20,
    )
    response.raise_for_status()

    return response.json()


def calculate_heat_component(
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
) -> float:
    """Calculate the environmental component of the risk score."""

    temperature_score = clamp((apparent_temperature - 20) * 2.4, 0, 45)
    humidity_score = clamp((humidity - 35) * 0.25, 0, 15)
    uv_score = clamp(uv_index * 2, 0, 15)
    air_quality_score = clamp(air_quality_index * 0.18, 0, 15)

    return (
        temperature_score
        + humidity_score
        + uv_score
        + air_quality_score
    )


def calculate_workforce_risk(
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
    sector: str,
    intensity: str,
    exposure_hours: float,
    vulnerability: str,
) -> int:
    """Calculate an explainable workforce risk score."""

    environmental_score = calculate_heat_component(
        apparent_temperature,
        humidity,
        uv_index,
        air_quality_index,
    )

    exposure_score = clamp(exposure_hours * 2, 0, 12)

    total_score = (
        environmental_score
        + SECTOR_WEIGHTS[sector]
        + INTENSITY_WEIGHTS[intensity]
        + exposure_score
        + VULNERABILITY_WEIGHTS[vulnerability]
    )

    return round(clamp(total_score))


def classify_risk(score: int) -> tuple[str, str]:
    """Convert a numerical score into a risk classification."""

    if score < 25:
        return "Low", "Normal precautions and routine monitoring."
    if score < 45:
        return "Moderate", "Increase hydration and continue monitoring."
    if score < 65:
        return "High", "Reduce continuous exposure and increase recovery breaks."
    if score < 80:
        return "Very High", "Reschedule strenuous work and activate heat controls."

    return "Critical", "Suspend non-essential strenuous outdoor activity."


def build_mitigation_plan(
    score: int,
    apparent_temperature: float,
    humidity: float,
    uv_index: float,
    air_quality_index: float,
    sector: str,
    intensity: str,
) -> list[str]:
    """Create rule-based MARYAM mitigation recommendations."""

    recommendations = []

    if score >= 80:
        recommendations.append(
            "Suspend or postpone non-essential strenuous outdoor activity."
        )
    elif score >= 65:
        recommendations.append(
            "Move heavy work to an earlier or later operational window."
        )
    elif score >= 45:
        recommendations.append(
            "Reduce continuous exposure and increase supervised recovery breaks."
        )
    else:
        recommendations.append(
            "Continue operations with routine environmental monitoring."
        )

    if apparent_temperature >= 40:
        recommendations.append(
            "Provide shaded or air-conditioned recovery areas."
        )

    if humidity >= 70:
        recommendations.append(
            "Increase hydration monitoring because high humidity reduces body cooling."
        )

    if uv_index >= 8:
        recommendations.append(
            "Use sun protection, shaded routes and reduced direct solar exposure."
        )

    if air_quality_index >= 100:
        recommendations.append(
            "Reduce outdoor exposure for vulnerable workers and consider respiratory protection."
        )

    if intensity == "Heavy":
        recommendations.append(
            "Replace heavy manual tasks with lighter preparation or inspection activities."
        )

    if sector in {"Construction", "Heavy Manual Labour", "Agriculture"}:
        recommendations.append(
            "Use a buddy-monitoring system and supervisor heat checks."
        )

    return recommendations


def get_current_uv(weather_data: dict) -> float:
    """Get the nearest available UV reading."""

    hourly = weather_data.get("hourly", {})
    uv_values = hourly.get("uv_index", [])

    if not uv_values:
        return 0.0

    current_time = weather_data.get("current", {}).get("time")
    hourly_times = hourly.get("time", [])

    if current_time in hourly_times:
        index = hourly_times.index(current_time)
        return float(uv_values[index] or 0)

    return float(uv_values[0] or 0)


def risk_colour(score: int) -> str:
    """Return map marker colour based on risk."""

    if score < 25:
        return "green"
    if score < 45:
        return "blue"
    if score < 65:
        return "orange"
    return "red"


# ---------------------------------------------------------
# HEADER
# ---------------------------------------------------------

st.title("🌍 HeatShield Global")

st.markdown(
    """
    **Interactive Workforce Climate and Operational Risk Intelligence Platform**

    Transforming live environmental data into safer workforce and operational decisions.
    """
)

st.markdown(
    """
    **Created by Mohd Khairul Ridhuan bin Mohd Fadzil, Malaysia**  
    Research Strategist in AI, Risk Intelligence and Business Governance  
    Powered by **MARYAM Risk Advisor**
    """
)

st.divider()


# ---------------------------------------------------------
# SIDEBAR CONTROLS
# ---------------------------------------------------------

with st.sidebar:
    st.header("Analysis Controls")

    location_input = st.text_input(
        "Enter city or location",
        value="Riyadh",
        help="Examples: Riyadh, Kuala Lumpur, Dubai, London or Singapore.",
    )

    sector = st.selectbox(
        "Sector",
        list(SECTOR_WEIGHTS.keys()),
        index=3,
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
        "Prototype infrastructure uses public APIs and open web technologies."
    )


# ---------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------

if "active_location" not in st.session_state:
    st.session_state.active_location = "Riyadh"

if analyse_button:
    cleaned_location = location_input.strip()

    if len(cleaned_location) < 2:
        st.error("Enter a valid city or location.")
        st.stop()

    st.session_state.active_location = cleaned_location


# ---------------------------------------------------------
# DATA RETRIEVAL
# ---------------------------------------------------------

active_location = st.session_state.active_location

try:
    with st.spinner("Retrieving live location and environmental data..."):
        location = geocode_location(active_location)

        if location is None:
            st.error(
                "Location not found. Enter a valid city or region and try again."
            )
            st.stop()

        latitude = float(location["latitude"])
        longitude = float(location["longitude"])
        timezone = location.get("timezone", "auto")

        weather_data = get_weather(
            latitude,
            longitude,
            timezone,
        )

        air_quality_data = get_air_quality(
            latitude,
            longitude,
            timezone,
        )

except requests.exceptions.Timeout:
    st.error(
        "The public data service took too long to respond. Please try again."
    )
    st.stop()

except requests.exceptions.RequestException:
    st.error(
        "Environmental data could not be retrieved. Please try again shortly."
    )
    st.stop()


# ---------------------------------------------------------
# CURRENT CONDITIONS
# ---------------------------------------------------------

current_weather = weather_data.get("current", {})
current_air = air_quality_data.get("current", {})

temperature = float(current_weather.get("temperature_2m") or 0)
apparent_temperature = float(
    current_weather.get("apparent_temperature") or 0
)
humidity = float(current_weather.get("relative_humidity_2m") or 0)
wind_speed = float(current_weather.get("wind_speed_10m") or 0)

uv_index = get_current_uv(weather_data)

air_quality_index = float(current_air.get("european_aqi") or 0)
pm25 = float(current_air.get("pm2_5") or 0)
pm10 = float(current_air.get("pm10") or 0)

risk_score = calculate_workforce_risk(
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

if administrative_area and administrative_area != location_name:
    display_location = (
        f"{location_name}, {administrative_area}, {country_name}"
    )

local_timestamp = current_weather.get("time", "Unavailable")


# ---------------------------------------------------------
# LOCATION AND TIME PANEL
# ---------------------------------------------------------

st.subheader("Live Location Intelligence")

location_col, time_col, status_col = st.columns(3)

with location_col:
    st.metric("Selected location", display_location)

with time_col:
    st.metric("Location date and time", local_timestamp)

with status_col:
    st.metric("Data status", "Live / Near-real-time")

st.caption(
    f"Timezone: {timezone} | Coordinates: "
    f"{latitude:.4f}, {longitude:.4f} | "
    f"Retrieved: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
)


# ---------------------------------------------------------
# KPI CARDS
# ---------------------------------------------------------

st.subheader("Environmental Conditions")

kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    st.metric("Temperature", f"{temperature:.1f} °C")

with kpi2:
    st.metric("Feels like", f"{apparent_temperature:.1f} °C")

with kpi3:
    st.metric("Humidity", f"{humidity:.0f}%")

with kpi4:
    st.metric("UV index", f"{uv_index:.1f}")

kpi5, kpi6, kpi7, kpi8 = st.columns(4)

with kpi5:
    st.metric("European AQI", f"{air_quality_index:.0f}")

with kpi6:
    st.metric("PM2.5", f"{pm25:.1f} μg/m³")

with kpi7:
    st.metric("PM10", f"{pm10:.1f} μg/m³")

with kpi8:
    st.metric("Wind speed", f"{wind_speed:.1f} km/h")


# ---------------------------------------------------------
# RISK PANEL
# ---------------------------------------------------------

st.subheader("Workforce Risk Intelligence")

risk_col, explanation_col = st.columns([1, 2])

with risk_col:
    st.metric(
        "Workforce Risk Score",
        f"{risk_score}/100",
    )
    st.markdown(f"### {risk_level}")
    st.write(risk_message)

with explanation_col:
    st.markdown("#### Why this score?")

    explanation_data = pd.DataFrame(
        {
            "Risk factor": [
                "Apparent temperature",
                "Humidity",
                "UV exposure",
                "Air quality",
                "Sector exposure",
                "Work intensity",
                "Exposure duration",
                "Worker vulnerability",
            ],
            "Current value": [
                f"{apparent_temperature:.1f} °C",
                f"{humidity:.0f}%",
                f"{uv_index:.1f}",
                f"{air_quality_index:.0f}",
                sector,
                intensity,
                f"{exposure_hours:.1f} hours",
                vulnerability,
            ],
        }
    )

    st.dataframe(
        explanation_data,
        use_container_width=True,
        hide_index=True,
    )


# ---------------------------------------------------------
# MARYAM MITIGATION PLAN
# ---------------------------------------------------------

st.subheader("MARYAM Live Mitigation Centre")

st.markdown(
    """
    **MARYAM — Meteorological and Risk Advisory Management System**

    The recommendations below are generated through an explainable,
    rules-based risk model.
    """
)

recommendations = build_mitigation_plan(
    score=risk_score,
    apparent_temperature=apparent_temperature,
    humidity=humidity,
    uv_index=uv_index,
    air_quality_index=air_quality_index,
    sector=sector,
    intensity=intensity,
)

for recommendation in recommendations:
    st.markdown(f"- {recommendation}")


# ---------------------------------------------------------
# INTERACTIVE MAP
# ---------------------------------------------------------

st.subheader("Interactive Location Map")

location_map = folium.Map(
    location=[latitude, longitude],
    zoom_start=11,
    control_scale=True,
)

folium.Circle(
    location=[latitude, longitude],
    radius=10000,
    color=risk_colour(risk_score),
    fill=True,
    fill_opacity=0.08,
    tooltip="10 km analysis radius",
).add_to(location_map)

folium.Marker(
    location=[latitude, longitude],
    popup=(
        f"{display_location}<br>"
        f"Risk score: {risk_score}/100<br>"
        f"Risk level: {risk_level}"
    ),
    tooltip=display_location,
    icon=folium.Icon(
        color=risk_colour(risk_score),
        icon="info-sign",
    ),
).add_to(location_map)

st_folium(
    location_map,
    width=None,
    height=500,
    use_container_width=True,
)


# ---------------------------------------------------------
# HOURLY FORECAST
# ---------------------------------------------------------

st.subheader("24-Hour Environmental Forecast")

weather_hourly = weather_data.get("hourly", {})

forecast_df = pd.DataFrame(
    {
        "Time": weather_hourly.get("time", [])[:24],
        "Temperature": weather_hourly.get(
            "temperature_2m", []
        )[:24],
        "Feels Like": weather_hourly.get(
            "apparent_temperature", []
        )[:24],
        "Humidity": weather_hourly.get(
            "relative_humidity_2m", []
        )[:24],
        "UV Index": weather_hourly.get(
            "uv_index", []
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

    with st.expander("View forecast data"):
        st.dataframe(
            forecast_df,
            use_container_width=True,
            hide_index=True,
        )
else:
    st.info("Hourly forecast data is currently unavailable.")


# ---------------------------------------------------------
# IMPACT AND GOVERNANCE
# ---------------------------------------------------------

st.divider()

impact1, impact2, impact3 = st.columns(3)

with impact1:
    st.markdown("### Social Impact")
    st.write(
        "Supports protection of outdoor, vulnerable and heat-exposed workers."
    )

with impact2:
    st.markdown("### Business Impact")
    st.write(
        "Supports workforce planning, safety and operational continuity."
    )

with impact3:
    st.markdown("### National Impact")
    st.write(
        "Supports climate resilience, labour protection and smart-city planning."
    )

with st.expander("Methodology, data sources and limitations"):
    st.markdown(
        """
        **Data sources**

        - Open-Meteo Geocoding API
        - Open-Meteo Weather API
        - Open-Meteo Air Quality API
        - OpenStreetMap through Folium

        **Methodology**

        The workforce risk score combines apparent temperature, humidity,
        UV exposure, air quality, sector exposure, work intensity,
        continuous exposure duration and worker vulnerability.

        **Important limitation**

        This platform is a research and decision-support prototype.
        It does not replace occupational health assessment, emergency
        medical guidance or legal compliance requirements.
        """
    )


# ---------------------------------------------------------
# FOOTER
# ---------------------------------------------------------

st.divider()

st.caption(
    "HeatShield Global | Created by Mohd Khairul Ridhuan bin "
    "Mohd Fadzil, Malaysia | Prototype Version 1.0 | 2026"
)
