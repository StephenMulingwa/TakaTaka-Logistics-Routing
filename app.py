import os
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import BASE_DIR, auth_session_key, config, verify_login
from services import common

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

ALLOWED_EXTENSIONS = {".xls", ".xlsx"}
VALID_TABS = {"optimized", "strict", "dispatch"}


def _import_dispatch_services():
    from services import optimized_orders, strict_orders, weekly_dispatch

    return optimized_orders, strict_orders, weekly_dispatch


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if session.get("auth_key") != auth_session_key():
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def dispatch_orders(service_module, token, resource_id):
    order_files = request.files.getlist("orders")
    assets_file = request.files.get("assets")
    if not order_files or not assets_file or assets_file.filename == "":
        return jsonify({"error": 1, "message": "Please upload orders Excel and assets Excel."}), 400
    for f in order_files:
        if not f.filename or not allowed_file(f.filename):
            return jsonify({"error": 1, "message": "Invalid orders file type."}), 400
    if not allowed_file(assets_file.filename):
        return jsonify({"error": 1, "message": "Invalid assets file type."}), 400

    warehouse_choice = request.form.get("warehouse", "TTS")

    try:
        tf, tt = common.compute_route_timestamps_from_range(
            request.form.get("route_from"),
            request.form.get("route_end"),
        )
    except ValueError as exc:
        return jsonify({"error": 1, "message": str(exc)}), 400

    try:
        gdf_joined, truck_number_norm = service_module.process_multiple_excels(order_files)
        if gdf_joined is None or gdf_joined.empty:
            return jsonify({"error": 1, "message": "No delivery rows with valid coordinates were found."}), 400
        unit_id, vehicle_name = service_module.read_asset_id_from_excel(
            assets_file, truck_number_norm
        )
        if not unit_id:
            return jsonify(
                {
                    "error": 1,
                    "message": f"Could not find unit ID for truck (normalized): {truck_number_norm or 'UNKNOWN'}.",
                }
            ), 400

        result = service_module.send_orders_and_create_route(
            token,
            resource_id,
            unit_id,
            vehicle_name,
            gdf_joined,
            tf,
            tt,
            warehouse_choice,
        )
        if result.get("error") == 0:
            result["summary"] = {
                "delivery_points": len(gdf_joined),
                "tonnage": round(float(gdf_joined["TONNAGE"].sum()), 2),
                "amount": round(float(gdf_joined["AMOUNT"].sum()), 2),
            }
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": 1, "message": str(exc)}), 500


@app.route("/")
def index():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if verify_login(username, password):
            session.clear()
            session["logged_in"] = True
            session["username"] = username.strip()
            session["auth_key"] = auth_session_key()
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."

    return render_template(
        "login.html",
        error=error,
        hero_exists=(BASE_DIR / "static" / "img" / "takataka.gif").exists(),
        logo_exists=(BASE_DIR / "static" / "img" / "takataka.png").exists(),
        ct_logo_exists=(BASE_DIR / "static" / "img" / "controltech_logo.png").exists(),
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    tab = request.args.get("tab", "optimized")
    if tab not in VALID_TABS:
        tab = "optimized"
    return render_template(
        "dashboard.html",
        title="TakaTaka Logistics Routing",
        active_tab=tab,
        warehouses=list(common.WAREHOUSES.keys()),
        logo_exists=(BASE_DIR / "static" / "img" / "takataka.png").exists(),
        ct_logo_exists=(BASE_DIR / "static" / "img" / "controltech_logo.png").exists(),
        controltech_url=config.CONTROLTECH_URL,
    )


@app.route("/api/optimized/dispatch", methods=["POST"])
@login_required
def api_optimized_dispatch():
    optimized_orders, _, _ = _import_dispatch_services()
    return dispatch_orders(
        optimized_orders, config.WIALON_TOKEN, config.WIALON_RESOURCE_ID
    )


@app.route("/api/strict/dispatch", methods=["POST"])
@login_required
def api_strict_dispatch():
    _, strict_orders, _ = _import_dispatch_services()
    return dispatch_orders(strict_orders, config.WIALON_TOKEN, config.WIALON_RESOURCE_ID)


@app.route("/api/weekly/load", methods=["POST"])
@login_required
def api_weekly_load():
    try:
        _, _, weekly_dispatch = _import_dispatch_services()
        routes, assets_df = weekly_dispatch.load_weekly_data(BASE_DIR)
        session["weekly_meta"] = {
            "dispatch_from": request.form.get("dispatch_from"),
            "dispatch_end": request.form.get("dispatch_end"),
            "warehouse": request.form.get("warehouse", "TTS"),
        }
        summaries = weekly_dispatch.routes_to_summary(routes)
        days = sorted({r["day"] for r in summaries}, key=lambda d: weekly_dispatch.DAYS.index(d))
        return jsonify(
            {
                "error": 0,
                "message": f"Loaded {len(routes)} route template(s) and {len(assets_df)} truck(s).",
                "days": days,
                "routes": summaries,
                "assets": weekly_dispatch.assets_to_json(assets_df),
            }
        )
    except Exception as exc:
        return jsonify({"error": 1, "message": str(exc)}), 500


@app.route("/api/weekly/route", methods=["GET"])
@login_required
def api_weekly_route():
    day = request.args.get("day", "").strip()
    route_name = request.args.get("route_name", "").strip()
    if not day or not route_name:
        return jsonify({"error": 1, "message": "Day and route name are required."}), 400
    try:
        _, _, weekly_dispatch = _import_dispatch_services()
        routes, _ = weekly_dispatch.load_weekly_data(BASE_DIR)
        selected = weekly_dispatch.find_route(routes, day, route_name)
        return jsonify(
            {
                "error": 0,
                "orders": weekly_dispatch.route_orders_to_json(selected),
            }
        )
    except Exception as exc:
        return jsonify({"error": 1, "message": str(exc)}), 500


@app.route("/api/weekly/dispatch", methods=["POST"])
@login_required
def api_weekly_dispatch():
    meta = session.get("weekly_meta")
    if not meta:
        return jsonify({"error": 1, "message": "Load routes first."}), 400

    data = request.get_json(silent=True) or {}
    day = data.get("day")
    route_name = data.get("route_name")
    asset_item_id = data.get("asset_item_id")
    asset_name = data.get("asset_name")

    if not all([day, route_name, asset_item_id, asset_name]):
        return jsonify({"error": 1, "message": "Day, route, and asset are required."}), 400

    try:
        _, _, weekly_dispatch = _import_dispatch_services()
        routes, _ = weekly_dispatch.load_weekly_data(BASE_DIR)
        selected = weekly_dispatch.find_route(routes, day, route_name)
        tf, tt = common.compute_route_timestamps_from_range(
            meta.get("dispatch_from"),
            meta.get("dispatch_end"),
            from_label="Dispatch From",
            to_label="Dispatch End",
        )
        result = weekly_dispatch.send_orders_and_create_route(
            config.WIALON_TOKEN,
            config.WIALON_RESOURCE_ID,
            int(asset_item_id),
            str(asset_name),
            selected["orders"].copy(),
            tf,
            tt,
            meta.get("warehouse", "TTS"),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": 1, "message": str(exc)}), 500


if __name__ == "__main__":
    # use_reloader=False avoids endless restarts from .venv file changes on Windows.
    # .env changes are picked up on each login via verify_login().
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        use_reloader=False,
    )
