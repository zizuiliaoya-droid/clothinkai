"""Pinned deployed board contract; pure validation, no authority or network writes."""

import hashlib
from copy import deepcopy
from pathlib import Path

from contracts import exact, require, sha

NETWORK = "clothinkai-progress-internal-20261024"
NETWORK_ID = "bb623b86f5c0dd8dccf5f7416492f5a92403023bfc6750c9a714a73cfdbda3d9"
BOARD = "clothinkai-progress-20261024"
BOARD_ID = "6445a72e527375f7161cc98baca5318781efcdea372d19861eba7fa9067977b7"
BOARD_IMAGE = "sha256:04b257c436e2d331f5ff06a9c6f885c526805ade5fc793206bfec03b0e1533b6"
OLD_WEB_ID = "e2e248c47c83adf65147664058c9a8c05b19ff7dc738c2ab867ca3ce96b80e96"
WEB = "clothinkai-prod-web-20261018"
ACTIVE_SHA = "4e9eeec6ebcca861e5861fd6306218750db5587d6c3c871afd65786162627fa8"
ROUTES_SHA = "0397e6f075bdc0324648bfa9d7721992c16a8c3647be971c3b73f011bd419ac2"
EXTERNAL = {"name": NETWORK, "external": True}
ERP_NETWORKS = {
    "clothinkai-prod-internal-20261018": "692df112e180e51a3fd635307c963c73155039886cc90e4f25f066fe14e34c17",
    "clothinkai-prod-egress-20261018": "687827d836c46646db92955c4aa16e24dc17e55fbfc773a5564a7559bbee5de5",
}


def route_bytes():
    raw = (
        Path(__file__)
        .with_name("templates")
        .joinpath("dashboard-routes.conf.inc")
        .read_bytes()
    )
    require(hashlib.sha256(raw).hexdigest() == ROUTES_SHA, "dashboard_routes_drift")
    return raw


def validate_routes(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 65536, "nginx_size")
    routes = route_bytes()
    require(raw.count(routes) == 1, "dashboard_routes_missing")
    outside = raw.replace(routes, b"")
    require(
        b"progress" not in outside and b"listen 443 ssl;" in raw.split(routes)[0],
        "dashboard_route_shadow",
    )
    return hashlib.sha256(raw).hexdigest()


def original_active(raw):
    require(
        validate_routes(raw) == ACTIVE_SHA and len(raw) == 2364,
        "dashboard_original_active",
    )
    return raw


def with_external_network(base):
    """Used for both new compose and old-web recovery compose. No board service."""
    result = deepcopy(base)
    networks = result["networks"]
    require(
        NETWORK not in networks or networks[NETWORK] == EXTERNAL,
        "dashboard_external_changed",
    )
    require(
        not any(
            key != NETWORK and value.get("name") == NETWORK
            for key, value in networks.items()
        ),
        "dashboard_network_alias",
    )
    networks[NETWORK] = deepcopy(EXTERNAL)
    for name, service in result["services"].items():
        attached = service.get("networks")
        require(type(attached) is dict, "compose_network_mapping")
        if name == "prod-web":
            require(
                NETWORK not in attached or attached[NETWORK] in (None, {}),
                "dashboard_web_alias",
            )
            attached[NETWORK] = {}
        else:
            require(NETWORK not in attached, "dashboard_erp_isolation")
    require(
        "prod-web" in result["services"] and BOARD not in result["services"],
        "dashboard_service_scope",
    )
    return result


def network_projection(raw):
    from contracts import parse_json

    require(type(raw) is bytes and len(raw) <= 16384, "dashboard_network_size")
    row = parse_json(raw)
    exact(row, ("id", "name", "internal", "driver", "scope", "members"))
    require(
        row["id"] == NETWORK_ID
        and row["name"] == NETWORK
        and row["internal"] is True
        and row["driver"] == "bridge"
        and row["scope"] == "local",
        "dashboard_network_identity",
    )
    require(
        type(row["members"]) is dict and 1 <= len(row["members"]) <= 2,
        "dashboard_members",
    )
    for identity, name in row["members"].items():
        sha(identity)
        require(name in {BOARD, WEB}, "dashboard_unknown_member")
    require(row["members"].get(BOARD_ID) == BOARD, "dashboard_board_member")
    return row


def verify_members(network, board, web_id):
    sha(web_id)
    # Revalidate even if called with an in-memory projection supplied by a caller.
    from contracts import canonical

    network = network_projection(canonical(network))
    exact(board, ("id", "image", "networks", "aliases"))
    require(
        board["id"] == BOARD_ID
        and board["image"] == BOARD_IMAGE
        and board["networks"] == {NETWORK: NETWORK_ID},
        "dashboard_board_identity",
    )
    require(
        type(board["aliases"]) is list
        and "progress-dashboard" in board["aliases"]
        and set(board["aliases"]) <= {"progress-dashboard", BOARD, BOARD_ID[:12]},
        "dashboard_board_alias",
    )
    require(
        network["members"] == {BOARD_ID: BOARD, web_id: WEB}, "dashboard_web_membership"
    )
    return True


def reconcile_connect(network, board, web_id, *, intent_persisted):
    """A lost connect reply never permits replay or cleanup, including absent edge."""
    require(intent_persisted is True, "dashboard_connect_intent")
    verify_members(network, board, web_id)
    return {
        "status": "OBSERVED_CONNECTED_HOLD",
        "connected": True,
        "repeat_connect": False,
        "release_lock": False,
        "pending": ["fresh_web_network_readback", "explicit_reconcile_receipt"],
        "live_enabled": False,
    }


def web_networks(web, original, web_id):
    sha(web_id)
    require(web["id"] == web_id and web["name"] == "/" + WEB, "dashboard_web_identity")
    require(
        type(original) is dict and NETWORK not in original and len(original) == 2,
        "dashboard_original_networks",
    )
    for name, identity in original.items():
        require(
            name
            in {"clothinkai-prod-internal-20261018", "clothinkai-prod-egress-20261018"},
            "dashboard_original_network_name",
        )
        sha(identity)
    require(
        web["networks"] == {**original, NETWORK: NETWORK_ID},
        "dashboard_web_network_drift",
    )
    return True
