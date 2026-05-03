# app.py
import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st
from google.cloud import bigquery

st.set_page_config(
    page_title="Air Quality Pipeline",
    page_icon="🌍",
    layout="wide",
)

REQUIRED_POLLUTANTS = ["pm25", "pm10", "no2"]

# Reusable CTE: keep only stations whose metadata says they have pm25, pm10 and no2
ELIGIBLE_STATIONS_CTE = """
latest_station_metadata AS (
    SELECT *
    FROM air_quality_raw.station_metadata
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY station_id
        ORDER BY loaded_at DESC, datetime_last_utc DESC
    ) = 1
),
eligible_stations AS (
    SELECT station_id, station_name
    FROM latest_station_metadata
    WHERE pollutants_available IS NOT NULL
      AND REGEXP_CONTAINS(LOWER(pollutants_available), r'(^|[^a-z0-9])pm25([^a-z0-9]|$)')
      AND REGEXP_CONTAINS(LOWER(pollutants_available), r'(^|[^a-z0-9])pm10([^a-z0-9]|$)')
      AND REGEXP_CONTAINS(LOWER(pollutants_available), r'(^|[^a-z0-9])no2([^a-z0-9]|$)')
)
"""


# ── BQ client (uses application-default creds or service account) ──
@st.cache_resource
def get_client():
    return bigquery.Client()


@st.cache_data(ttl=300)  # cache 5 min
def run_query(sql: str) -> pd.DataFrame:
    return get_client().query(sql).to_dataframe()


def format_age_hours(hours) -> str:
    if pd.isna(hours):
        return "No data yet"

    total_minutes = max(int(round(float(hours) * 60)), 0)
    days, remainder = divmod(total_minutes, 1440)
    hrs, mins = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hrs}h ago"
    if hrs > 0:
        return f"{hrs}h {mins}m ago"
    return f"{mins}m ago"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. HEADER + PIPELINE HEALTH SCORECARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.title("🌍 Air Quality Pipeline Dashboard")

freshness = run_query("""
    SELECT source, status, records_written,
           duration_seconds, minutes_since_last_success
    FROM air_quality_staging.ingestion_freshness
""")

if not freshness.empty:
    cols = st.columns(len(freshness))
    for i, row in freshness.iterrows():
        with cols[i]:
            mins = row["minutes_since_last_success"]
            if mins < 90:
                icon = "🟢"
            elif mins < 360:
                icon = "🟡"
            else:
                icon = "🔴"

            st.metric(
                label=f"{icon} {row['source']}",
                value=f"{mins:.0f} min ago",
                delta=f"{row['records_written']} rows · {row['duration_seconds']:.0f}s",
            )

st.divider()

# Get the filtered station list once and reuse it
stations = run_query(f"""
    WITH {ELIGIBLE_STATIONS_CTE}
    SELECT DISTINCT station_id, station_name
    FROM eligible_stations
    ORDER BY station_name
""")

st.caption(
    "Showing only stations whose metadata includes all required pollutants: "
    + ", ".join(REQUIRED_POLLUTANTS)
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. STATION MAP (colored by staleness)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.subheader("📍 Station Freshness Map")

station_fresh = run_query(f"""
    WITH {ELIGIBLE_STATIONS_CTE}
    SELECT sf.station_id, sf.station_name, sf.latitude, sf.longitude,
           sf.openaq_age_hours, sf.weather_age_hours,
           sf.openaq_stale, sf.weather_stale, sf.overall_stale,
           sf.last_openaq_utc, sf.last_weather_utc
    FROM air_quality_staging.station_freshness sf
    JOIN eligible_stations es
      ON sf.station_id = es.station_id
    WHERE sf.latitude IS NOT NULL
""")

if station_fresh.empty:
    st.info("No stations found with pm25, pm10 and no2 available.")
else:
    # Compute the most recent update across OpenAQ + Weather
    station_fresh["last_data_update_utc"] = station_fresh[
        ["last_openaq_utc", "last_weather_utc"]
    ].max(axis=1)

    station_fresh["last_data_update_age_hours"] = station_fresh[
        ["openaq_age_hours", "weather_age_hours"]
    ].min(axis=1, skipna=True)

    station_fresh["last_data_update_label"] = station_fresh["last_data_update_age_hours"].apply(
        format_age_hours
    )

    station_fresh["last_data_update_source"] = station_fresh.apply(
        lambda row: (
            "No data"
            if pd.isna(row["openaq_age_hours"]) and pd.isna(row["weather_age_hours"])
            else "OpenAQ"
            if pd.isna(row["weather_age_hours"])
            or (
                pd.notna(row["openaq_age_hours"])
                and row["openaq_age_hours"] <= row["weather_age_hours"]
            )
            else "Weather"
        ),
        axis=1,
    )

    station_fresh["last_data_update_utc_label"] = station_fresh["last_data_update_utc"].apply(
        lambda ts: ts.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(ts) else "No data"
    )

    # Map a color: green = fresh, yellow = partial stale, red = both stale
    def staleness_color(row):
        if row["openaq_stale"] and row["weather_stale"]:
            return [220, 50, 50, 180]  # red
        elif row["overall_stale"]:
            return [240, 180, 0, 180]  # yellow
        return [0, 180, 80, 180]  # green

    station_fresh["color"] = station_fresh.apply(staleness_color, axis=1)

    st.pydeck_chart(
        pdk.Deck(
            initial_view_state=pdk.ViewState(
                latitude=station_fresh["latitude"].mean(),
                longitude=station_fresh["longitude"].mean(),
                zoom=5,
                pitch=0,
            ),
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    data=station_fresh,
                    get_position=["longitude", "latitude"],
                    get_color="color",
                    get_radius=5,
                    radius_units="pixels",
                    radius_min_pixels=2,
                    radius_max_pixels=6,
                    pickable=True,
                )
            ],
            tooltip={
                "text": (
                    "{station_name}\n"
                    "Last data update: {last_data_update_label}\n"
                    "Latest source: {last_data_update_source}\n"
                    "Timestamp: {last_data_update_utc_label}"
                )
            },
        )
    )

st.divider()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. POLLUTANT TIME SERIES (interactive selectors)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.subheader("📈 Pollutant Trends")

if stations.empty:
    st.warning("No eligible stations found for pollutant trend analysis.")
else:
    col_station, col_poll, col_days = st.columns([2, 1, 1])

    with col_station:
        sel_station = st.selectbox(
            "Station",
            stations["station_id"],
            format_func=lambda x: stations.loc[stations.station_id == x, "station_name"].iloc[0],
        )

    with col_poll:
        pollutants = run_query(f"""
            SELECT DISTINCT pollutant
            FROM air_quality_analytics.hourly_combined
            WHERE station_id = '{sel_station}'
            ORDER BY pollutant
        """)

        if pollutants.empty:
            sel_poll = None
            st.warning("No pollutants found for this station in hourly_combined.")
        else:
            sel_poll = st.selectbox("Pollutant", pollutants["pollutant"])

    with col_days:
        sel_days = st.slider("Days back", 1, 14, 3)

    if sel_poll is not None:
        ts = run_query(f"""
            SELECT hour_utc, pollutant_value, temperature_2m,
                   wind_speed_10m, boundary_layer_height
            FROM air_quality_analytics.hourly_combined
            WHERE station_id  = '{sel_station}'
              AND pollutant   = '{sel_poll}'
              AND hour_utc   >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {sel_days} DAY)
            ORDER BY hour_utc
        """)

        if ts.empty:
            st.warning("No data for this selection.")
        else:
            # Dual-axis: pollutant + temperature
            base = alt.Chart(ts).encode(x=alt.X("hour_utc:T", title="Time (UTC)"))

            line_poll = base.mark_line(color="#4c78a8").encode(
                y=alt.Y("pollutant_value:Q", title=sel_poll),
                tooltip=["hour_utc:T", "pollutant_value:Q"],
            )
            line_temp = base.mark_line(color="#e45756", strokeDash=[4, 2]).encode(
                y=alt.Y("temperature_2m:Q", title="Temp (°C)"),
                tooltip=["hour_utc:T", "temperature_2m:Q"],
            )

            chart = alt.layer(line_poll, line_temp).resolve_scale(y="independent")
            st.altair_chart(chart, use_container_width=True)

            with st.expander("Weather overlay details"):
                st.line_chart(ts.set_index("hour_utc")[["wind_speed_10m", "boundary_layer_height"]])

st.divider()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. INGESTION RUN HISTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.subheader("🔄 Ingestion Run History")

daily = run_query("""
    SELECT *
    FROM air_quality_analytics.daily_ingestion_stats
    ORDER BY run_date DESC
    LIMIT 30
""")

bars = (
    alt.Chart(daily)
    .mark_bar()
    .encode(
        x=alt.X("run_date:T", title="Date"),
        y=alt.Y("total_records:Q", title="Records written"),
        color=alt.Color("source:N"),
        tooltip=["run_date:T", "source:N", "total_records:Q", "successes:Q", "errors:Q"],
    )
)
st.altair_chart(bars, use_container_width=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. STATION FRESHNESS TABLE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.subheader("🗂️ Station Detail")

if station_fresh.empty:
    st.info("No eligible stations to display.")
else:
    st.dataframe(
        station_fresh[
            [
                "station_id",
                "station_name",
                "openaq_age_hours",
                "weather_age_hours",
                "openaq_stale",
                "weather_stale",
            ]
        ]
        .sort_values("openaq_age_hours", ascending=False)
        .style.applymap(
            lambda v: "background-color: #ffcccc" if v is True else "",
            subset=["openaq_stale", "weather_stale"],
        ),
        use_container_width=True,
        height=400,
    )
