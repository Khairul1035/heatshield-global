import math
from datetime import datetime, timezone
from typing import Any

src = Path("/mnt/data/Pasted text(27).txt")
text = src.read_text(encoding="utf-8")

# 1) Add OSRM endpoint after OVERPASS_ENDPOINTS block
overpass_block = '''OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
'''
if "OSRM_ROUTE_URL" not in text:
    text = text.replace(
        overpass_block,
        overpass_block + '\nOSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"\n'
    )

# 2) Insert emergency reachability functions before SAFE-ZONE SCANNER
marker = '''# =========================================================
# SAFE-ZONE SCANNER
# =========================================================
'''
emergency_functions = r'''
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
    """Return road distance, estimated drive time and route geometry."""

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
            "estimated_minutes": max(
                1,
                round(safe_float(route.get("duration")) / 60),
            ),
            "geometry": route.get("geometry", {}).get(
                "coordinates",
                [],
            ),
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
    """Classify estimated medical access using traffic-light logic."""

    if estimated_minutes is None:
        return (
            "⚫ Unknown",
            "Routing data is unavailable.",
            "gray",
        )

    if estimated_minutes <= 10:
        return (
            "🟢 Green",
            "Estimated medical access is within 10 minutes.",
            "green",
        )

    if estimated_minutes <= 20:
        return (
            "🟡 Amber",
            "Estimated medical access is within 11–20 minutes.",
            "orange",
        )

    return (
        "🔴 Red",
        "Estimated medical access exceeds 20 minutes.",
        "red",
    )


def add_route_to_map(
    map_object: folium.Map,
    geometry: list,
    colour: str,
    tooltip: str,
) -> None:
    """Draw an OSRM route on a Folium map."""

    if not geometry:
        return

    route_points = [
        [coordinate[1], coordinate[0]]
        for coordinate in geometry
        if isinstance(coordinate, list) and len(coordinate) >= 2
    ]

    if not route_points:
        return

    folium.PolyLine(
        locations=route_points,
        color=colour,
        weight=5,
        opacity=0.85,
        tooltip=tooltip,
    ).add_to(map_object)


def enrich_medical_routes(
    medical_records: list[dict],
    origin_latitude: float,
    origin_longitude: float,
    maximum_facilities: int = 5,
) -> list[dict]:
    """Calculate road distance and estimated drive time for medical sites."""

    nearest_candidates = sorted(
        medical_records,
        key=lambda item: item.get("Distance (km)", 999999),
    )[:maximum_facilities]

    enriched_records = []

    for record in nearest_candidates:
        route = get_road_route(
            origin_latitude=origin_latitude,
            origin_longitude=origin_longitude,
            destination_latitude=record["Latitude"],
            destination_longitude=record["Longitude"],
        )

        enriched_record = record.copy()

        if route:
            estimated_minutes = route["estimated_minutes"]
            status, interpretation, route_colour = (
                classify_emergency_access(estimated_minutes)
            )

            enriched_record.update(
                {
                    "Road distance (km)": route["road_distance_km"],
                    "Estimated drive time": (
                        f"{estimated_minutes} minutes"
                    ),
                    "Estimated minutes": estimated_minutes,
                    "Access status": status,
                    "Access interpretation": interpretation,
                    "Route geometry": route["geometry"],
                    "Route colour": route_colour,
                }
            )

        else:
            status, interpretation, route_colour = (
                classify_emergency_access(None)
            )

            enriched_record.update(
                {
                    "Road distance (km)": None,
                    "Estimated drive time": "Unavailable",
                    "Estimated minutes": 999999,
                    "Access status": status,
                    "Access interpretation": interpretation,
                    "Route geometry": [],
                    "Route colour": route_colour,
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


'''
if "def get_road_route(" not in text:
    text = text.replace(marker, emergency_functions + marker)

# 3) Add emergency session state
session_marker = '''if "safe_zone_location_key" not in st.session_state:
    st.session_state.safe_zone_location_key = None
'''
session_add = session_marker + '''
if "medical_route_results" not in st.session_state:
    st.session_state.medical_route_results = []

if "medical_route_location_key" not in st.session_state:
    st.session_state.medical_route_location_key = None
'''
if '"medical_route_results"' not in text:
    text = text.replace(session_marker, session_add)

# 4) Reset emergency state when analysing new location
reset_marker = '''    st.session_state.safe_zone_results = []
    st.session_state.safe_zone_location_key = None
'''
reset_add = reset_marker + '''    st.session_state.medical_route_results = []
    st.session_state.medical_route_location_key = None
'''
if "st.session_state.medical_route_results = []" not in text.split("# =========================================================\n# RETRIEVE LOCATION")[0]:
    text = text.replace(reset_marker, reset_add, 1)

# 5) Insert emergency panel before 24-HOUR FORECAST
forecast_marker = '''# =========================================================
# 24-HOUR FORECAST
# =========================================================
'''
emergency_panel = r'''
# =========================================================
# EMERGENCY MEDICAL REACHABILITY
# =========================================================

st.divider()

st.subheader("🚑 MARYAM Emergency Reachability Engine")

st.markdown(
    """
    This module estimates which mapped hospital or clinic may be
    reached fastest through the road network.

    **Important:** The result is a routing estimate. It does not
    include live traffic, ambulance availability, hospital capacity,
    emergency-department capability or guaranteed arrival time.
    """
)

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
    st.write(
        f"{len(medical_candidates)} mapped hospitals or clinics "
        "are available for route analysis."
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
                    medical_records=medical_candidates,
                    origin_latitude=latitude,
                    origin_longitude=longitude,
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
            access_status, access_text, _ = (
                classify_emergency_access(
                    fastest_available["Estimated minutes"]
                )
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
                        "No routable mapped hospital was found "
                        "among the analysed facilities."
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
                        "No routable mapped clinic was found "
                        "among the analysed facilities."
                    )

            if (
                fastest_available["Category"]
                == "Clinic or medical centre"
            ):
                st.warning(
                    "The fastest mapped option is a clinic or "
                    "medical centre. It may not provide emergency "
                    "care for severe heatstroke. A hospital may be "
                    "more appropriate for life-threatening symptoms."
                )

        route_table = pd.DataFrame(medical_route_results)

        route_display_columns = [
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
            route_table[route_display_columns],
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
                map_object=emergency_route_map,
                geometry=medical_place["Route geometry"],
                colour=medical_place["Route colour"],
                tooltip=(
                    f"Route {position}: "
                    f"{medical_place['Name']} — "
                    f"{medical_place['Estimated drive time']}"
                ),
            )

            road_distance = medical_place[
                "Road distance (km)"
            ]

            road_distance_text = (
                f"{road_distance} km"
                if road_distance is not None
                else "Unavailable"
            )

            medical_popup = (
                f"<b>{position}. "
                f"{medical_place['Name']}</b><br>"
                f"Category: "
                f"{medical_place['Category']}<br>"
                f"Road distance: "
                f"{road_distance_text}<br>"
                f"Estimated drive: "
                f"{medical_place['Estimated drive time']}<br>"
                f"Status: "
                f"{medical_place['Access status']}"
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

    else:
        st.info(
            "Press **Calculate Fastest Medical Access** to analyse "
            "road distance and estimated driving time."
        )


'''
if "MARYAM Emergency Reachability Engine" not in text:
    text = text.replace(forecast_marker, emergency_panel + forecast_marker)

# 6) Update methodology and version
text = text.replace(
    "- Public Overpass API servers\n",
    "- Public Overpass API servers\n        - Public OSRM routing service\n",
)

text = text.replace(
    "- Distances are straight-line estimates, not confirmed road distances.\n",
    "- Safe-zone distances are straight-line estimates.\n"
    "        - Emergency-route distances use road-network routing estimates.\n"
    "        - Route times do not include live traffic conditions.\n"
    "        - Route time is not ambulance response time.\n",
)

text = text.replace(
    "Prototype Version 2.0 | 2026",
    "Prototype Version 2.1 | 2026",
)

# Final safety checks
for forbidden in [
    "code = r'''",
    "path.write_text(",
    "/mnt/data/app.py",
]:
    if forbidden in text:
        raise ValueError(f"Forbidden wrapper text found: {forbidden}")

out = Path("/mnt/data/app.py")
out.write_text(text, encoding="utf-8")
py_compile.compile(str(out), doraise=True)

print(f"Created: {out}")
print(f"Lines: {len(text.splitlines())}")
print("Syntax check: PASSED")
print("Emergency engine present:", "MARYAM Emergency Reachability Engine" in text)
print("OSRM present:", "OSRM_ROUTE_URL" in text)
