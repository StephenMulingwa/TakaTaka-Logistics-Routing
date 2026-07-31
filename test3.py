import streamlit as st
import pandas as pd
import requests
import json
import time
from datetime import datetime
import pytz
import os
import base64
import re

# =============================
# Warehouses
# =============================
WAREHOUSES = {
    "TTS": {"lat": -1.1404981978961632, "lon": 36.733312587683756},
    "Rubis Banana": {"lat": -1.1747329246654616, "lon": 36.758819701449724},
}

# =============================
# Helpers: UI + environment
# =============================
os.environ['TZ'] = 'Africa/Nairobi'
try:
    time.tzset()
except Exception:
    pass


def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()


def set_background():
    try:
        background_image = get_base64_image("TakaTaka_thumbnail_solution.jpg")
        st.markdown(
            f"""
            <style>
            .stApp {{
                background-image: url("data:image/jpg;base64,{background_image}");
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def show_logo_top_right(image_path, width=120):
    try:
        logo_base64 = get_base64_image(image_path)
        st.markdown(
            f"""
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div></div>
                <div style="margin-right: 1rem;">
                    <img src="data:image/png;base64,{logo_base64}" width="{width}">
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# =============================
# Normalization & parsing
# =============================

def normalize_plate(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def extract_truck_number_from_text(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    patterns = [
        r"Truck\s*(?:Number|No\.?|#)?\s*[:\-]?\s*([A-Z0-9\- ]{4,})",
        r"\b([A-Z]{2,3}\s*\d{3,4}\s*[A-Z])\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return normalize_plate(m.group(1))
    return None

# =============================
# Excel readers
# =============================

def extract_coordinates(coord_str):
    """
    Extract (latitude, longitude) from text.
    Handles both 'LAT: -1.2 LONG: 36.8' and simple '(-1.2, 36.8)' or '-1.2,36.8' formats.
    """
    if not isinstance(coord_str, str):
        return None, None
    try:
        text = coord_str.strip().upper().replace(" ", "")
        # Match LAT/LONG formatted strings
        if "LAT:" in text and "LONG:" in text:
            parts = text.split("LONG:")
            lat = float(parts[0].replace("LAT:", ""))
            lon = float(parts[1])
            return lat, lon
        # Match comma-separated numeric pairs
        if "," in text:
            nums = re.findall(r"-?\d+\.\d+", text)
            if len(nums) >= 2:
                return float(nums[0]), float(nums[1])
    except Exception:
        pass
    return None, None




def read_asset_id_from_excel(excel_file, truck_number_norm):
    df = pd.read_excel(excel_file)
    df.columns = [col.strip().lower() for col in df.columns]
    name_col = None
    for candidate in ("reportname", "name", "unit", "unitname"):
        if candidate in df.columns:
            name_col = candidate
            break
    if name_col is None or "itemid" not in df.columns:
        raise ValueError("Assets Excel must contain columns like 'ReportName' (or 'Name') and 'itemId'.")
    df["normalized_name"] = df[name_col].astype(str).apply(normalize_plate)
    if not truck_number_norm:
        return None, None
    match = df[df["normalized_name"] == truck_number_norm]
    if match.empty:
        match = df[df["normalized_name"].str.contains(re.escape(truck_number_norm), na=False)]
    if not match.empty:
        row = match.iloc[0]
        return int(row["itemid"]), str(row[name_col])
    return None, None

# =============================
# Wialon API (Modified to support warehouse selection)
# =============================
def read_excel_to_df(excel_file):
    """
    Read an orders Excel file while preserving every row (no grouping).
    Each row becomes an independent order, maintaining Excel order.
    Drops rows without valid coordinates.
    """
    raw_df = pd.read_excel(excel_file, header=None)
    truck_number_norm = None

    # Try extracting truck number from first column text
    if 0 in raw_df.columns:
        for row in raw_df[0].astype(str).tolist():
            truck_number_norm = extract_truck_number_from_text(row)
            if truck_number_norm:
                break

    # Excel row 8 (index 7) contains headers
    header_row_idx = 7
    df = pd.read_excel(excel_file, header=header_row_idx)

    # Clean column names
    df.columns = [
        re.sub(r"\s+", " ", str(col)).replace("\u00A0", " ").strip().upper()
        for col in df.columns
    ]
    df = df.loc[:, ~df.columns.str.startswith("UNNAMED")]

    required_cols = {"CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "COORDINATES"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in orders Excel: {missing}")

    # Filter valid rows
    df = df[df["CUSTOMER ID"].notna()]
    df = df[~df["CUSTOMER NAME"].astype(str).str.contains("TOTAL", case=False, na=False)]

    # Normalize numeric columns
    for col in ("TONNAGE", "AMOUNT"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(),
                errors="coerce"
            ).fillna(0)
        else:
            df[col] = 0

    # Keep Excel's original row order
    df = df.reset_index(drop=True)
    df["__ORDER_IDX"] = df.index.astype(int)

    # Extract coordinates
    df[["LAT", "LONG"]] = df["COORDINATES"].apply(
        lambda x: pd.Series(extract_coordinates(x))
    )

    # Drop rows with invalid coordinates
    invalid_rows = df[df[["LAT", "LONG"]].isna().any(axis=1)]
    if not invalid_rows.empty:
        st.warning(f"⚠️ Dropping {len(invalid_rows)} rows with missing or invalid coordinates:")
        st.dataframe(
            invalid_rows[["CUSTOMER NAME", "LOCATION", "COORDINATES"]],
            use_container_width=True
        )

    df = df.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)

    # Ensure numeric coordinate types
    df["LAT"] = df["LAT"].astype(float)
    df["LONG"] = df["LONG"].astype(float)

    return df, truck_number_norm


# PATCH: enforce nearest-first sequence regardless of Wialon optimizer
def send_orders_and_create_route(token, resource_id, unit_id, vehicle_name, df_grouped, tf, tt, warehouse_choice):
    """
    Create Wialon route (no optimization) following Excel order.
    Computes mileage leg-by-leg using OSRM and includes polylines.
    """

    try:
        import math, requests, time, json
        base_url = "https://hst-api.wialon.com/wialon/ajax.html"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # ---- Login ----
        st.info("🔐 Logging in to Wialon...")
        login_payload = {"svc": "token/login", "params": json.dumps({"token": str(token).strip()})}
        login_response = requests.post(base_url, data=login_payload, headers=headers, timeout=30)
        login_result = login_response.json()
        if "eid" not in login_result:
            return {"error": 1, "message": f"Login failed: {login_result}"}
        session_id = login_result["eid"]

        # ---- Warehouse ----
        wh = WAREHOUSES[warehouse_choice]
        wh_lat, wh_lon = wh["lat"], wh["lon"]
        wh_name = warehouse_choice
        wh_coords = f"{wh_lat}, {wh_lon}"
        st.info(f"📦 Using warehouse: {wh_name} ({wh_lat}, {wh_lon})")

        # ---- Filter and order ----
        df_grouped = df_grouped.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)
        df_grouped["PRIORITY"] = range(1, len(df_grouped) + 1)
        st.dataframe(df_grouped[["CUSTOMER NAME", "LOCATION", "LAT", "LONG", "PRIORITY"]])

        # ---- Distance helper ----
        def calc_distance_km(y1, x1, y2, x2):
            R = 6371
            y1, x1, y2, x2 = map(math.radians, [y1, x1, y2, x2])
            dlat, dlon = y2 - y1, x2 - x1
            a = math.sin(dlat / 2) ** 2 + math.cos(y1) * math.cos(y2) * math.sin(dlon / 2) ** 2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # ---- OSRM polyline helper ----
        def get_osrm_polyline(start, end):
            try:
                url = f"https://router.project-osrm.org/route/v1/driving/{start['x']},{start['y']};{end['x']},{end['y']}?overview=full&geometries=polyline"
                r = requests.get(url, timeout=15)
                js = r.json()
                if isinstance(js, dict) and js.get("routes"):
                    return js["routes"][0].get("geometry"), int(js["routes"][0].get("distance", 0))
            except Exception:
                pass
            return None, int(calc_distance_km(start["y"], start["x"], end["y"], end["x"]) * 1000)

        # ---- Build route orders ----
        route_id = int(time.time())
        current_time = int(time.time())
        sequence_index = 0
        last_visit_time = int(tf)
        route_orders = []

        # Start warehouse (f=260)
        route_orders.append({
            "uid": int(unit_id),
            "id": 0,
            "n": wh_name,
            "p": {
                "ut": 0, "rep": True, "w": "0", "c": "0",
                "r": {"vt": last_visit_time, "ndt": 3, "id": route_id,
                      "i": sequence_index, "m": 0, "t": 180},
                "u": int(unit_id), "a": f"{wh_name} ({wh_lat}, {wh_lon})",
                "weight": "0", "cost": "0"
            },
            "f": 260,
            "tf": tf, "tt": tt, "r": 100, "y": wh_lat, "x": wh_lon,
            "s": 0,
            "sf": 0,
            "trt": 0,
            "st": current_time,
            "cnm": 0,
            "callMode": "create", "u": int(unit_id),
            "weight": "0", "cost": "0",
            "cargo": {"weight": "0", "cost": "0"},
            "cmp": {"unitRequirements": {"values": []}},
            "gfn": {"geofences": {}}, "ej": {}, "cf": {}
        })

        # Add customer orders in Excel order
        prev = {"y": wh_lat, "x": wh_lon}
        for idx, row in df_grouped.iterrows():
            try:
                lat, lon = float(row["LAT"]), float(row["LONG"])
            except Exception:
                continue

            order_name = str(row.get("CUSTOMER NAME", f"Order {idx+1}"))
            location = str(row.get("LOCATION", "Unknown"))
            weight_kg = int(float(row.get("TONNAGE", 0)) * 1000)
            cost_val = float(row.get("AMOUNT", 0))
            pr_val = int(row["PRIORITY"])

            polyline, mileage = get_osrm_polyline(prev, {"y": lat, "x": lon})
            last_visit_time += 180
            sequence_index += 1

            route_orders.append({
                "uid": int(unit_id),
                "id": idx + 1,
                "n": order_name,
                "p": {
                    "ut": 180, "rep": True,
                    "w": str(weight_kg), "c": str(int(cost_val)),
                    "r": {"vt": last_visit_time, "ndt": 3, "id": route_id,
                          "i": sequence_index, "m": mileage, "t": 0},
                    "u": int(unit_id),
                    "a": f"{location} ({lat}, {lon})",
                    "weight": str(weight_kg),
                    "cost": str(int(cost_val)),
                    "pr": pr_val
                },
                "f": 0,
                "tf": tf, "tt": tt,
                "r": 100, "y": lat, "x": lon,
                "rp": polyline,
                "s": 0,
                "sf": 0,
                "trt": 0,
                "st": current_time,
                "cnm": 0,
                "callMode": "create", "u": int(unit_id),
                "weight": str(weight_kg), "cost": str(int(cost_val)),
                "cargo": {"weight": str(weight_kg), "cost": str(int(cost_val))},
                "cmp": {"unitRequirements": {"values": []}},
                "gfn": {"geofences": {}}, "ej": {}, "cf": {}
            })

            prev = {"y": lat, "x": lon}

        # Back to warehouse (f=264)
        sequence_index += 1
        polyline_back, mileage_back = get_osrm_polyline(prev, {"y": wh_lat, "x": wh_lon})
        route_orders.append({
            "uid": int(unit_id),
            "id": len(route_orders),
            "n": wh_name,
            "p": {"ut": 0, "rep": True, "w": "0", "c": "0",
                  "r": {"vt": last_visit_time + 180, "ndt": 3, "id": route_id,
                        "i": sequence_index, "m": mileage_back, "t": 0},
                  "u": int(unit_id), "a": f"{wh_name} ({wh_lat}, {wh_lon})",
                  "weight": "0", "cost": "0"},
            "f": 264, "tf": tf, "tt": tt, "r": 100,
            "y": wh_lat, "x": wh_lon,
            "rp": polyline_back,
            "s": 0,
            "sf": 0,
            "trt": 0,
            "st": current_time,
            "cnm": 0,
            "callMode": "create", "u": int(unit_id),
            "weight": "0", "cost": "0",
            "cargo": {"weight": "0", "cost": "0"},
            "cmp": {"unitRequirements": {"values": []}},
            "gfn": {"geofences": {}}, "ej": {}, "cf": {}
        })

        total_mileage = sum(o["p"]["r"]["m"] for o in route_orders)
        total_cost = sum(float(o["p"]["c"]) for o in route_orders if o["f"] == 0)
        total_weight = sum(int(o["p"]["w"]) for o in route_orders if o["f"] == 0)

        # ---- Build final payload ----
        batch_payload = {
            "svc": "core/batch",
            "params": json.dumps({
                "params": [{
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
                }],
                "flags": 0,
            }),
            "sid": session_id,
        }

        # ---- Send ----
        st.info("📦 Creating final route (Excel order + OSRM polylines)...")
        route_response = requests.post(base_url, data=batch_payload, timeout=90)
        route_result = route_response.json()

        if isinstance(route_result, list):
            first = route_result[0]
            if isinstance(first, dict) and first.get("error", 0) == 0:
                planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
                return {"error": 0, "message": "✅ Route created successfully", "planning_url": planning_url}
            return {"error": first.get("error", 1), "message": first.get("reason", "Unknown error")}
        if isinstance(route_result, dict) and route_result.get("error", 0) == 0:
            planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
            return {"error": 0, "message": "✅ Route created successfully", "planning_url": planning_url}

        return {"error": 1, "message": f"Route creation failed: {route_result}"}

    except Exception as e:
        st.error(f"❌ Unexpected error: {e}")
        st.write("Error line:", e.__traceback__.tb_lineno)
        return {"error": 1, "message": str(e)}




def process_multiple_excels(excel_files):
    all_gdfs = []
    truck_numbers = set()
    for excel_file in excel_files:
        gdf_joined, truck_number = read_excel_to_df(excel_file)
        if gdf_joined is not None and len(gdf_joined):
            all_gdfs.append(gdf_joined)
        if truck_number:
            truck_numbers.add(truck_number)
    if not all_gdfs:
        raise ValueError("No valid data found in any of the Excel files.")
    if len(truck_numbers) > 1:
        raise ValueError(f"Multiple truck numbers found (after normalization): {', '.join(sorted(truck_numbers))}")
    combined_gdf = pd.concat(all_gdfs, ignore_index=True)
    #combined_gdf = combined_gdf.drop_duplicates(subset=["CUSTOMER ID", "LOCATION"], keep="first")
    combined_gdf = combined_gdf.reset_index(drop=True)
    sole_truck = next(iter(truck_numbers)) if truck_numbers else None
    return combined_gdf, sole_truck


def run_wialon_uploader():
    st.subheader("\U0001F4E6 Logistics Excel Orders Uploader (via Logistics API)")
    with st.form("upload_form"):
        excel_files = st.file_uploader("Upload Excel File(s) - All must be for the same truck", type=["xls", "xlsx"], accept_multiple_files=True)
        assets_file = st.file_uploader("Upload Excel File (Assets)", type=["xls", "xlsx"])
        selected_date = st.date_input("Select Route Date")
        col1, col2 = st.columns(2)
        with col1:
            start_hour = st.slider("Route Start Hour", 0, 23, 6)
        with col2:
            end_hour = st.slider("Route End Hour", 0, 23, 18)
        overnight_route = st.checkbox(
            "End time is on the next day (overnight route)",
            value=(end_hour <= start_hour),
            help="Enable when route starts in the evening and ends the following morning."
        )
        warehouse_choice = st.selectbox("Select Warehouse", list(WAREHOUSES.keys()))
        token = st.text_input("Enter your Wialon Token", type="password")
        resource_id = st.text_input("Enter Wialon Resource ID")
        show_debug = st.checkbox("Show debug info", value=False)
        submit_btn = st.form_submit_button("Upload and Dispatch")

    if submit_btn:
        if not excel_files or not assets_file or not token or not resource_id:
            st.error("Please upload orders Excel, assets Excel, token, and resource ID.")
            return
        try:
            with st.spinner("Processing..."):
                tz = pytz.timezone('Africa/Nairobi')
                start_time = tz.localize(datetime.combine(selected_date, datetime.min.time().replace(hour=start_hour)))
                if overnight_route:
                    end_date = selected_date + pd.Timedelta(days=1)
                else:
                    end_date = selected_date
                end_time = tz.localize(datetime.combine(end_date, datetime.min.time().replace(hour=end_hour)))
                tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
                if tt <= tf:
                    st.error("End time must be after start time. Enable overnight mode if the route ends next day.")
                    return
                gdf_joined, truck_number_norm = process_multiple_excels(excel_files)
                if gdf_joined is None or gdf_joined.empty:
                    st.error("No delivery rows with valid coordinates were found.")
                    return
                unit_id, vehicle_name = read_asset_id_from_excel(assets_file, truck_number_norm)
                if not unit_id:
                    st.error(f"Could not find unit ID for truck (normalized): {truck_number_norm or 'UNKNOWN'}.")
                    return
                st.info("Summary of orders:")
                st.write(f"Delivery points: {len(gdf_joined)}")
                st.write(f"Tonnage: {gdf_joined['TONNAGE'].sum():.2f}")
                st.write(f"Amount: {gdf_joined['AMOUNT'].sum():.2f}")
                result = send_orders_and_create_route(token, int(resource_id), unit_id, vehicle_name, gdf_joined, tf, tt, warehouse_choice)
                if result.get("error") == 0:
                    st.success("✅ Route created successfully!")
                    st.markdown(f"[Open Wialon Logistics]({result['planning_url']})", unsafe_allow_html=True)
                    st.balloons()
                else:
                    st.error(f"❌ Failed: {result.get('message', 'Unknown error')}")
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")


#if __name__ == "__main__":
    #run_wialon_uploader()

   
