import io
import json
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
import pandas as pd
import pytz
import requests
import streamlit as st


WAREHOUSES = {
    "TTS": {"lat": -1.1404981978961632, "lon": 36.733312587683756},
    "Rubis Banana": {"lat": -1.1747329246654616, "lon": 36.758819701449724},
}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PILOT_DAY_ZIPS = {
    "Monday": "Monday.zip",
    "Tuesday": "Tuesday.zip",
    "Wednesday": "Wednesday.zip",
    "Thursday": "Thursday.zip",
    "Friday": "Friday.zip",
    "Saturday": "Saturday.zip",
}
LOCAL_ASSETS_FILE = "Takataka Ids.xlsx"


os.environ["TZ"] = "Africa/Nairobi"
try:
    time.tzset()
except Exception:
    pass


def normalize_plate(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def extract_coordinates(coord_str):
    if not isinstance(coord_str, str):
        return None, None
    try:
        text = coord_str.strip().upper().replace(" ", "")
        if "LAT:" in text and "LONG:" in text:
            parts = text.split("LONG:")
            lat = float(parts[0].replace("LAT:", ""))
            lon = float(parts[1])
            return lat, lon
        if "," in text:
            nums = re.findall(r"-?\d+\.\d+", text)
            if len(nums) >= 2:
                return float(nums[0]), float(nums[1])
    except Exception:
        pass
    return None, None


def read_orders_excel(file_like):
    df = pd.read_excel(file_like, header=7)
    cleaned_cols = []
    keep_mask = []
    for col in df.columns:
        col_str = str(col)
        col_norm = re.sub(r"\s+", " ", col_str).replace("\u00A0", " ").strip().upper()

        # Drop export artifacts:
        # 1) unnamed filler columns
        # 2) accidental header cells containing full "LAT: ... LONG: ..." strings
        # 3) numeric header cells (often lat/lon values leaked into header row)
        is_unnamed = col_norm.startswith("UNNAMED")
        has_lat_long_header = bool(re.search(r"\bLAT\s*:\s*.*\bLONG\s*:", col_norm))
        is_numeric_header = False
        try:
            float(col_str)
            is_numeric_header = True
        except Exception:
            is_numeric_header = False

        keep = not (is_unnamed or has_lat_long_header or is_numeric_header)
        cleaned_cols.append(col_norm)
        keep_mask.append(keep)

    df.columns = cleaned_cols
    df = df.loc[:, keep_mask]

    required_cols = {"CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "COORDINATES"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = df[df["CUSTOMER ID"].notna()]
    df = df[~df["CUSTOMER NAME"].astype(str).str.contains("TOTAL", case=False, na=False)]
    for col in ("TONNAGE", "AMOUNT"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(),
                errors="coerce",
            ).fillna(0)
        else:
            df[col] = 0

    df = df.reset_index(drop=True)
    df["PRIORITY"] = range(1, len(df) + 1)
    df[["LAT", "LONG"]] = df["COORDINATES"].apply(lambda x: pd.Series(extract_coordinates(x)))
    df = df.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)
    df["LAT"] = df["LAT"].astype(float)
    df["LONG"] = df["LONG"].astype(float)
    return df


def detect_day_from_name(name: str):
    lower_name = name.lower()
    for day in DAYS:
        if day.lower() in lower_name:
            return day
    return None


def load_routes_from_zips(zip_files):
    routes = []
    for zf in zip_files:
        zip_day_name = detect_day_from_name(str(zf.name))
        zip_bytes = io.BytesIO(zf.getvalue())
        with zipfile.ZipFile(zip_bytes, "r") as z:
            for member in z.namelist():
                lower = member.lower()
                if not (lower.endswith(".xlsx") or lower.endswith(".xls")):
                    continue
                base_name = os.path.basename(member)
                day_name = detect_day_from_name(base_name) or zip_day_name
                if day_name is None:
                    continue
                with z.open(member) as fh:
                    data = io.BytesIO(fh.read())
                    try:
                        route_df = read_orders_excel(data)
                    except Exception:
                        continue
                    if route_df.empty:
                        continue
                    routes.append(
                        {
                            "day": day_name,
                            "route_name": os.path.splitext(base_name)[0],
                            "source_zip": zf.name,
                            "orders": route_df,
                        }
                    )
    return routes


def load_routes_from_local_zips(base_dir: Path):
    routes = []
    for day_name, zip_name in PILOT_DAY_ZIPS.items():
        zip_path = base_dir / zip_name
        if not zip_path.exists():
            continue
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.namelist():
                lower = member.lower()
                if not (lower.endswith(".xlsx") or lower.endswith(".xls")):
                    continue
                base_name = os.path.basename(member)
                inferred_day = detect_day_from_name(base_name) or day_name
                with z.open(member) as fh:
                    data = io.BytesIO(fh.read())
                    try:
                        route_df = read_orders_excel(data)
                    except Exception:
                        continue
                    if route_df.empty:
                        continue
                    routes.append(
                        {
                            "day": inferred_day,
                            "route_name": os.path.splitext(base_name)[0],
                            "source_zip": zip_name,
                            "orders": route_df,
                        }
                    )
    return routes


def read_assets(assets_file):
    df = pd.read_excel(assets_file)
    df.columns = [c.strip().lower() for c in df.columns]
    name_col = None
    for candidate in ("reportname", "name", "unit", "unitname"):
        if candidate in df.columns:
            name_col = candidate
            break
    if name_col is None or "itemid" not in df.columns:
        raise ValueError("Assets file must have ReportName/Name/Unit and itemId columns.")
    out = df[[name_col, "itemid"]].dropna().copy()
    out["itemid"] = out["itemid"].astype(int)
    out["asset_name"] = out[name_col].astype(str)
    out["asset_label"] = out["asset_name"] + " (ID: " + out["itemid"].astype(str) + ")"
    out["asset_norm"] = out["asset_name"].apply(normalize_plate)
    return out[["asset_name", "asset_norm", "itemid", "asset_label"]]


def send_orders_and_create_route(token, resource_id, unit_id, vehicle_name, orders_df, tf, tt, warehouse_choice):
    try:
        import math

        base_url = "https://hst-api.wialon.com/wialon/ajax.html"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        login_payload = {"svc": "token/login", "params": json.dumps({"token": str(token).strip()})}
        login_result = requests.post(base_url, data=login_payload, headers=headers, timeout=30).json()
        if "eid" not in login_result:
            return {"error": 1, "message": f"Login failed: {login_result}"}
        session_id = login_result["eid"]

        wh = WAREHOUSES[warehouse_choice]
        wh_lat, wh_lon = wh["lat"], wh["lon"]
        wh_name = warehouse_choice

        def calc_distance_km(y1, x1, y2, x2):
            r = 6371
            y1, x1, y2, x2 = map(math.radians, [y1, x1, y2, x2])
            dlat, dlon = y2 - y1, x2 - x1
            a = math.sin(dlat / 2) ** 2 + math.cos(y1) * math.cos(y2) * math.sin(dlon / 2) ** 2
            return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        def get_osrm_polyline(start, end):
            try:
                url = f"https://router.project-osrm.org/route/v1/driving/{start['x']},{start['y']};{end['x']},{end['y']}?overview=full&geometries=polyline"
                js = requests.get(url, timeout=15).json()
                if isinstance(js, dict) and js.get("routes"):
                    route = js["routes"][0]
                    return route.get("geometry"), int(route.get("distance", 0))
            except Exception:
                pass
            return None, int(calc_distance_km(start["y"], start["x"], end["y"], end["x"]) * 1000)

        route_id = int(time.time())
        current_time = int(time.time())
        sequence_index = 0
        last_visit_time = int(tf)
        route_orders = []

        route_orders.append(
            {
                "uid": int(unit_id),
                "id": 0,
                "n": wh_name,
                "p": {
                    "ut": 0,
                    "rep": True,
                    "w": "0",
                    "c": "0",
                    "r": {"vt": last_visit_time, "ndt": 3, "id": route_id, "i": sequence_index, "m": 0, "t": 0},
                    "u": int(unit_id),
                    "a": f"{wh_name} ({wh_lat}, {wh_lon})",
                    "weight": "0",
                    "cost": "0",
                },
                "f": 260,
                "tf": tf,
                "tt": tt,
                "r": 100,
                "y": wh_lat,
                "x": wh_lon,
                "s": 0,
                "sf": 0,
                "trt": 0,
                "st": current_time,
                "cnm": 0,
                "callMode": "create",
                "u": int(unit_id),
                "weight": "0",
                "cost": "0",
                "cargo": {"weight": "0", "cost": "0"},
                "cmp": {"unitRequirements": {"values": []}},
                "gfn": {"geofences": {}},
                "ej": {},
                "cf": {},
            }
        )

        prev = {"y": wh_lat, "x": wh_lon}
        for idx, row in orders_df.iterrows():
            lat, lon = float(row["LAT"]), float(row["LONG"])
            order_name = str(row.get("CUSTOMER NAME", f"Order {idx + 1}"))
            location = str(row.get("LOCATION", "Unknown"))
            weight_kg = int(float(row.get("TONNAGE", 0)) * 1000)
            cost_val = float(row.get("AMOUNT", 0))
            priority = int(row.get("PRIORITY", idx + 1))
            polyline, mileage = get_osrm_polyline(prev, {"y": lat, "x": lon})
            last_visit_time += 180
            sequence_index += 1
            route_orders.append(
                {
                    "uid": int(unit_id),
                    "id": idx + 1,
                    "n": order_name,
                    "p": {
                        "ut": 180,
                        "rep": True,
                        "w": str(weight_kg),
                        "c": str(int(cost_val)),
                        "r": {"vt": last_visit_time, "ndt": 3, "id": route_id, "i": sequence_index, "m": mileage, "t": 0},
                        "u": int(unit_id),
                        "a": f"{location} ({lat}, {lon})",
                        "weight": str(weight_kg),
                        "cost": str(int(cost_val)),
                        "pr": priority,
                    },
                    "f": 0,
                    "tf": tf,
                    "tt": tt,
                    "r": 100,
                    "y": lat,
                    "x": lon,
                    "rp": polyline,
                    "s": 0,
                    "sf": 0,
                    "trt": 0,
                    "st": current_time,
                    "cnm": 0,
                    "callMode": "create",
                    "u": int(unit_id),
                    "weight": str(weight_kg),
                    "cost": str(int(cost_val)),
                    "cargo": {"weight": str(weight_kg), "cost": str(int(cost_val))},
                    "cmp": {"unitRequirements": {"values": []}},
                    "gfn": {"geofences": {}},
                    "ej": {},
                    "cf": {},
                }
            )
            prev = {"y": lat, "x": lon}

        sequence_index += 1
        polyline_back, mileage_back = get_osrm_polyline(prev, {"y": wh_lat, "x": wh_lon})
        route_orders.append(
            {
                "uid": int(unit_id),
                "id": len(route_orders),
                "n": wh_name,
                "p": {
                    "ut": 0,
                    "rep": True,
                    "w": "0",
                    "c": "0",
                    "r": {"vt": last_visit_time + 180, "ndt": 3, "id": route_id, "i": sequence_index, "m": mileage_back, "t": 0},
                    "u": int(unit_id),
                    "a": f"{wh_name} ({wh_lat}, {wh_lon})",
                    "weight": "0",
                    "cost": "0",
                },
                "f": 264,
                "tf": tf,
                "tt": tt,
                "r": 100,
                "y": wh_lat,
                "x": wh_lon,
                "rp": polyline_back,
                "s": 0,
                "sf": 0,
                "trt": 0,
                "st": current_time,
                "cnm": 0,
                "callMode": "create",
                "u": int(unit_id),
                "weight": "0",
                "cost": "0",
                "cargo": {"weight": "0", "cost": "0"},
                "cmp": {"unitRequirements": {"values": []}},
                "gfn": {"geofences": {}},
                "ej": {},
                "cf": {},
            }
        )

        total_mileage = sum(o["p"]["r"]["m"] for o in route_orders)
        total_cost = sum(float(o["p"]["c"]) for o in route_orders if o["f"] == 0)
        total_weight = sum(int(o["p"]["w"]) for o in route_orders if o["f"] == 0)

        batch_payload = {
            "svc": "core/batch",
            "params": json.dumps(
                {
                    "params": [
                        {
                            "svc": "order/route_update",
                            "params": {
                                "itemId": int(resource_id),
                                "orders": route_orders,
                                "uid": route_id,
                                "routeId": route_id,
                                "callMode": "create",
                                "exp": 0,
                                "f": 0,
                                "n": f"{vehicle_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                "summary": {
                                    "countOrders": len(route_orders),
                                    "mileage": total_mileage,
                                    "priceMileage": 0,
                                    "priceTotal": total_cost,
                                    "weight": total_weight,
                                    "cost": total_cost,
                                },
                            },
                        }
                    ],
                    "flags": 0,
                }
            ),
            "sid": session_id,
        }

        route_result = requests.post(base_url, data=batch_payload, timeout=90).json()
        if isinstance(route_result, list):
            first = route_result[0]
            if isinstance(first, dict) and first.get("error", 0) == 0:
                planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
                return {"error": 0, "message": "Route created successfully", "planning_url": planning_url}
            return {"error": first.get("error", 1), "message": first.get("reason", "Unknown error")}
        if isinstance(route_result, dict) and route_result.get("error", 0) == 0:
            planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
            return {"error": 0, "message": "Route created successfully", "planning_url": planning_url}
        return {"error": 1, "message": f"Route creation failed: {route_result}"}
    except Exception as e:
        return {"error": 1, "message": str(e)}


def run_weekly_dispatch():
    st.subheader("Weekly Scheduled Dispatch")
    st.caption("Routes are loaded from local Monday.zip and Tuesday.zip in this app folder.")

    with st.form("weekly_dispatch_form"):
        selected_date = st.date_input("Dispatch Date")
        col1, col2 = st.columns(2)
        with col1:
            start_hour = st.slider("Route Start Hour", 0, 23, 6)
        with col2:
            end_hour = st.slider("Route End Hour", 0, 23, 18)
        overnight_route = st.checkbox(
            "End time is on the next day (overnight route)",
            value=(end_hour <= start_hour),
            help="Enable when route starts in the evening and ends the following morning.",
        )
        warehouse_choice = st.selectbox("Select Warehouse", list(WAREHOUSES.keys()))
        token = st.text_input("Enter your Wialon Token", type="password")
        resource_id = st.text_input("Enter Wialon Resource ID")
        prepare_btn = st.form_submit_button("Load Routes")

    if prepare_btn:
        try:
            base_dir = Path(__file__).parent
            routes = load_routes_from_local_zips(base_dir)
            if not routes:
                expected = ", ".join(PILOT_DAY_ZIPS.values())
                st.error(f"No valid route templates found. Expected local zip files: {expected}")
                return
            assets_path = base_dir / LOCAL_ASSETS_FILE
            if not assets_path.exists():
                st.error(f"Assets file not found: {LOCAL_ASSETS_FILE}")
                return
            assets_df = read_assets(assets_path)
            st.session_state["weekly_routes"] = routes
            st.session_state["weekly_assets"] = assets_df
            st.session_state["weekly_dispatch_meta"] = {
                "selected_date": selected_date,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "overnight_route": overnight_route,
                "warehouse_choice": warehouse_choice,
                "token": token,
                "resource_id": resource_id,
            }
            st.success(f"Loaded {len(routes)} route template(s) and {len(assets_df)} truck(s) from {LOCAL_ASSETS_FILE}.")
        except Exception as e:
            st.error(f"Failed to load templates: {e}")
            return

    routes = st.session_state.get("weekly_routes")
    meta = st.session_state.get("weekly_dispatch_meta")
    if not routes or meta is None:
        return

    available_days = sorted({r["day"] for r in routes}, key=lambda d: DAYS.index(d))
    selected_day = st.selectbox("Select Day", available_days)
    day_routes = [r for r in routes if r["day"] == selected_day]

    routes_table = pd.DataFrame(
        [
            {
                "Route Name": r["route_name"],
                "Stops": len(r["orders"]),
                "Total Tonnage": round(r["orders"]["TONNAGE"].sum(), 2),
                "Total Amount": round(r["orders"]["AMOUNT"].sum(), 2),
                "Source Zip": r["source_zip"],
            }
            for r in day_routes
        ]
    )
    st.markdown("### Routes")
    st.dataframe(routes_table, use_container_width=True)

    route_names = [r["route_name"] for r in day_routes]
    selected_route_name = st.selectbox("Select Route to Dispatch", route_names)
    selected_route = next(r for r in day_routes if r["route_name"] == selected_route_name)
    st.markdown("### Selected Route Stops")
    st.dataframe(
        selected_route["orders"][["PRIORITY", "CUSTOMER NAME", "LOCATION", "TONNAGE", "AMOUNT"]],
        use_container_width=True,
    )

    assets_df = st.session_state.get("weekly_assets")
    if assets_df is None or assets_df.empty:
        st.info(f"No trucks loaded. Ensure {LOCAL_ASSETS_FILE} exists, then click 'Load Routes'.")
        return

    asset_label = st.selectbox("Select Asset (searchable)", assets_df["asset_label"].tolist())
    selected_asset = assets_df[assets_df["asset_label"] == asset_label].iloc[0]

    if st.button("Dispatch Selected Route", type="primary"):
        if not meta["token"] or not meta["resource_id"]:
            st.error("Token and Resource ID are required.")
            return
        tz = pytz.timezone("Africa/Nairobi")
        start_time = tz.localize(datetime.combine(meta["selected_date"], datetime.min.time().replace(hour=meta["start_hour"])))
        end_date = meta["selected_date"] + pd.Timedelta(days=1) if meta["overnight_route"] else meta["selected_date"]
        end_time = tz.localize(datetime.combine(end_date, datetime.min.time().replace(hour=meta["end_hour"])))
        tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
        if tt <= tf:
            st.error("End time must be after start time.")
            return

        with st.spinner("Dispatching selected route..."):
            result = send_orders_and_create_route(
                token=meta["token"],
                resource_id=int(meta["resource_id"]),
                unit_id=int(selected_asset["itemid"]),
                vehicle_name=str(selected_asset["asset_name"]),
                orders_df=selected_route["orders"].copy(),
                tf=tf,
                tt=tt,
                warehouse_choice=meta["warehouse_choice"],
            )
        if result.get("error") == 0:
            st.success("Route dispatched successfully.")
            st.markdown(f"[Open Wialon Logistics]({result['planning_url']})")
            st.balloons()
        else:
            st.error(f"Dispatch failed: {result.get('message', 'Unknown error')}")

