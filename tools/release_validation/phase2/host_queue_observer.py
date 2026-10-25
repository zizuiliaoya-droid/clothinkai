"""Fixed count-only Celery/Redis projection executed in the original API.

No task payload, broker configuration or credential is emitted. Celery inspect
uses its normal finite remote-control requests; it does not dispatch tasks.
"""

import json
import sys


def observe():
    import redis
    from celery import Celery

    config = globals()["_config"]()  # prepended from the existing fixed observer
    globals()["validate_config"](config)
    expected = sys.argv[1]
    app = Celery("flow78-count-observer", broker=config["REDIS_URL_CELERY_BROKER"])
    app.conf.update(accept_content=["json"], task_serializer="json")
    inspect = app.control.inspect(timeout=5)
    replies = {
        name: getattr(inspect, name)()
        for name in (
            "ping",
            "active",
            "reserved",
            "scheduled",
            "active_queues",
            "stats",
        )
    }
    for value in replies.values():
        if type(value) is not dict or set(value) != {expected}:
            raise ValueError("worker_identity")
    queue_names = sorted(v["name"] for v in replies["active_queues"][expected])
    if queue_names != ["backup", "crawler", "default", "report"]:
        raise ValueError("worker_queues")
    broker = replies["stats"][expected]["broker"]
    if (
        broker["transport"] != "redis"
        or broker["hostname"] != "prod-redis"
        or int(broker["port"]) != 6379
        or str(broker["virtual_host"]) != "1"
    ):
        raise ValueError("worker_broker")
    options = broker.get("transport_options", {})
    if (
        options.get("priority_steps", [0, 3, 6, 9]) != [0, 3, 6, 9]
        or options.get("sep", "\x06\x16") != "\x06\x16"
        or options.get("global_keyprefix", "") != ""
    ):
        raise ValueError("worker_priority_options")
    if (
        options.get("unacked_key", "unacked") != "unacked"
        or options.get("unacked_index_key", "unacked_index") != "unacked_index"
    ):
        raise ValueError("worker_unacked_keys")
    client = redis.from_url(
        config["REDIS_URL_CELERY_BROKER"], socket_connect_timeout=5, socket_timeout=5
    )
    try:
        ready = {
            queue: [
                client.llen(queue if p == 0 else queue + "\x06\x16" + str(p))
                for p in (0, 3, 6, 9)
            ]
            for queue in queue_names
        }
        return {
            "worker": expected,
            "active": len(replies["active"][expected]),
            "reserved": len(replies["reserved"][expected]),
            "scheduled": len(replies["scheduled"][expected]),
            "ready": ready,
            "unacked_hash": client.hlen("unacked"),
            "unacked_index": client.zcard("unacked_index"),
            "priority_steps": [0, 3, 6, 9],
            "broker_transport": "redis",
        }
    finally:
        client.close()
        app.close()


if __name__ == "__main__":
    try:
        print(json.dumps(observe(), sort_keys=True, separators=(",", ":")))
    except BaseException:
        raise SystemExit(2) from None
