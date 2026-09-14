"""Free Wialon order ID slots on a resource when the order pool is full."""

import json
import time

import requests

# Wialon resources use uint16 order IDs (1..32767). Leave headroom for new routes.
ORDER_ID_CEILING = 32767
ORDER_COUNT_THRESHOLD = 31500
BATCH_SIZE = 250


def _fetch_orders(base_url: str, session_id: str, resource_id: int) -> dict:
    payload = {
        "svc": "core/search_item",
        "params": json.dumps({"id": int(resource_id), "flags": 0x7FFFFFFF}),
        "sid": session_id,
    }
    result = requests.post(base_url, data=payload, timeout=180).json()
    item = result.get("item") if isinstance(result, dict) else None
    orders = (item or {}).get("orders") or {}
    return orders if isinstance(orders, dict) else {}


def _cleanup_candidates(orders: dict, now: int | None = None) -> list[tuple[str, int]]:
    now = now or int(time.time())
    candidates: list[tuple[str, int]] = []

    for key, order in orders.items():
        if not str(key).isdigit():
            continue
        order_id = int(key)
        created = order.get("ct") or order.get("mt") or 0
        status = order.get("s", 0)
        unit_id = order.get("u", 0)
        flags = order.get("f", 0)

        if status in (2, 3) and created and (now - created) > 3 * 86400:
            candidates.append(("register", order_id))
        elif (
            unit_id in (0, None)
            and status == 0
            and flags == 0
            and created
            and (now - created) > 7 * 86400
        ):
            candidates.append(("delete", order_id))

    # Prefer clearing oldest / lowest IDs first.
    candidates.sort(key=lambda item: item[1])
    return candidates


def _run_batch(base_url: str, session_id: str, resource_id: int, actions: list[tuple[str, int]]) -> int:
    if not actions:
        return 0

    params = {
        "params": [
            {
                "svc": "order/update",
                "params": {
                    "itemId": int(resource_id),
                    "id": order_id,
                    "callMode": mode,
                    **({"force": 1} if mode == "delete" else {}),
                },
            }
            for mode, order_id in actions
        ],
        "flags": 0,
    }
    response = requests.post(
        base_url,
        data={"svc": "core/batch", "params": json.dumps(params), "sid": session_id},
        timeout=180,
    ).json()

    if not isinstance(response, list):
        return 0

    ok = 0
    for item in response:
        if isinstance(item, list):
            ok += 1
        elif isinstance(item, dict) and item.get("error", 1) == 0:
            ok += 1
    return ok


def order_pool_is_full(orders: dict) -> bool:
    if not orders:
        return False
    numeric_ids = [int(k) for k in orders if str(k).isdigit()]
    if not numeric_ids:
        return False
    return len(numeric_ids) >= ORDER_COUNT_THRESHOLD or max(numeric_ids) >= ORDER_ID_CEILING - 200


def ensure_order_capacity(
    base_url: str,
    session_id: str,
    resource_id: int,
    *,
    slots_needed: int = 500,
) -> int:
    """Clear old completed / inactive orders until there is room for new routes."""
    orders = _fetch_orders(base_url, session_id, resource_id)
    if not order_pool_is_full(orders):
        return 0

    candidates = _cleanup_candidates(orders)
    target = max(slots_needed, 500)
    cleared = 0

    for start in range(0, len(candidates), BATCH_SIZE):
        if cleared >= target:
            break
        batch = candidates[start : start + BATCH_SIZE]
        cleared += _run_batch(base_url, session_id, resource_id, batch)

    return cleared


def is_order_capacity_error(message: str) -> bool:
    text = str(message or "")
    return "UNKNOWN_ORDER_GET_ERROR" in text or "ORDER_INCOMPATIBLE_ROUTE_ID" in text
