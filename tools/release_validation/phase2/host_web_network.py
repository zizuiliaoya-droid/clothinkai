"""Fixed existing external-network attach and conservative lost-reply reconciliation."""

from pathlib import Path

from contracts import canonical, require, sha
from dashboard_contract import (
    BOARD,
    BOARD_ID,
    ERP_NETWORKS,
    NETWORK,
    NETWORK_ID,
    verify_members,
    web_networks,
)
from fixed_host_transport import DOCKER, ROOT, _capture, collect
from host_sequence import HostJournal, replay
from sql_policy import parse, require_host_authority


async def ensure_web_network(spec, journal, web_id):
    require_host_authority(spec)
    sha(web_id)
    require(
        type(journal) is HostJournal
        and journal.lock is not None
        and journal.root == Path(ROOT) / "releases" / spec.data["release_id"] / "state",
        "network_host_journal",
    )
    state, last = replay(spec, journal.read()), journal.read()[-1]
    require(
        state.pending is not None
        and state.hold is None
        and last["kind"] == "intent"
        and last["payload"]["name"] == "REPLACE_WEB",
        "network_replace_intent",
    )
    from host_migration_control import recorded_facts
    from host_release_input import HostReleaseSpec

    image_id = (
        recorded_facts(spec, journal, "BUILD_FRONTEND")["image"]["id"]
        if type(spec) is HostReleaseSpec
        else parse(spec._binding)["frontend_image"]
    )
    web = (await collect("inspect", "web"))["facts"]
    control = (await collect("web_control_show", "web"))["facts"]
    require(
        web["id"] == control["id"] == web_id
        and web["image"] == control["image"] == image_id,
        "network_new_web_identity",
    )
    require(
        control["mounts"]
        == [
            {
                "source": ROOT
                + "/releases/"
                + spec.data["release_id"]
                + "/web-control",
                "destination": "/opt/production",
                "rw": False,
            }
        ],
        "network_new_web_control",
    )
    require(
        web["networks"] in (ERP_NETWORKS, {**ERP_NETWORKS, NETWORK: NETWORK_ID}),
        "network_web_original_networks",
    )
    network = (await collect("dashboard_network", "web"))["facts"]
    board = (await collect("dashboard_board", "web"))["facts"]
    require(
        network["members"]
        in (
            {BOARD_ID: BOARD},
            {BOARD_ID: BOARD, web_id: "clothinkai-prod-web-20261018"},
        ),
        "network_foreign_member",
    )
    expected_intent = {
        "step": "REPLACE_WEB",
        "policy_sha256": spec.sha256,
        "network_id": NETWORK_ID,
        "web_id": web_id,
    }
    try:
        prior = parse(journal.directory.read("web-connect-intent.json", 8192))
    except FileNotFoundError:
        prior = None
    if prior is not None:
        require(prior == expected_intent, "network_prior_intent_binding")
        # Even observed connectivity after a lost reply does not authorize replay
        # or completion of the surrounding web replacement action.
        verify_members(network, board, web_id)
        web_networks(web, ERP_NETWORKS, web_id)
        return {
            "status": "OBSERVED_CONNECTED_HOLD",
            "repeat_connect": False,
            "release_lock": False,
        }
    if web_id in network["members"]:
        verify_members(network, board, web_id)
        web_networks(web, ERP_NETWORKS, web_id)
        return {
            "status": "ALREADY_CONNECTED_VERIFIED",
            "network_id": NETWORK_ID,
            "web_id": web_id,
        }
    require(web["networks"] == ERP_NETWORKS, "network_projection_disagreement")
    # The shared create() fsyncs both file and parent before any Docker write.
    journal.directory.create("web-connect-intent.json", canonical(expected_intent))
    try:
        require_host_authority(spec)
        await _capture(DOCKER + ("network", "connect", NETWORK_ID, web_id), timeout=15)
        network = (await collect("dashboard_network", "web"))["facts"]
        board = (await collect("dashboard_board", "web"))["facts"]
        web = (await collect("inspect", "web"))["facts"]
        verify_members(network, board, web_id)
        web_networks(web, ERP_NETWORKS, web_id)
        journal.directory.create(
            "web-connect-result.json", canonical({**expected_intent, "connected": True})
        )
        return {
            "status": "CONNECTED_VERIFIED",
            "network_id": NETWORK_ID,
            "web_id": web_id,
        }
    except BaseException:
        # Intent remains durable; reconnect and network deletion are not options.
        return {
            "status": "HOLD_UNKNOWN",
            "repeat_connect": False,
            "release_lock": False,
        }
