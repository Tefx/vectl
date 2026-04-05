from __future__ import annotations

import json
import threading
import time

import pytest

from vectl.orchestration.control_channel import (
    ControlChannelMessage,
    FilesystemControlChannel,
    PendingActionLimitExceededError,
    send_to_control,
)


def _first_pending_path(channel: FilesystemControlChannel, run_id: str):
    pending = channel.control_layout(run_id)["pending"]
    files = sorted(pending.glob("*.json"))
    assert len(files) == 1
    return files[0]


def test_send_persists_pause_request_in_pending_layout(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path)

    channel.send(
        ControlChannelMessage(
            msg_type="control.pause",
            sender="operator",
            payload=("run-123", "maintenance"),
            timestamp=100.0,
        )
    )

    pending_path = _first_pending_path(channel, "run-123")
    payload = json.loads(pending_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-123"
    assert payload["msg_type"] == "control.pause"
    assert payload["payload"] == ["run-123", "maintenance"]


def test_bounded_pending_actions_enforced(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path, max_pending_actions=1)
    channel.send(ControlChannelMessage(msg_type="control.pause", sender="op", payload=("run",)))
    with pytest.raises(PendingActionLimitExceededError):
        channel.send(
            ControlChannelMessage(msg_type="control.unpause", sender="op", payload=("run",))
        )


def test_applied_and_rejected_acknowledgement_lookup(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path)
    channel.send(ControlChannelMessage(msg_type="control.pause", sender="op", payload=("run",)))
    request = channel.list_requests("run")[0]

    channel.acknowledge_applied("run", request.action_id, reason="done")
    applied = channel.lookup_acknowledgement("run", request.action_id)
    assert applied is not None
    assert applied.status == "applied"

    channel.send(ControlChannelMessage(msg_type="control.stop", sender="op", payload=("run",)))
    reject_request = channel.list_requests("run")[0]
    channel.acknowledge_rejected("run", reject_request.action_id, reason="blocked")
    rejected = channel.lookup_acknowledgement("run", reject_request.action_id)
    assert rejected is not None
    assert rejected.status == "rejected"


def test_wait_for_ack_timeout_is_explicit(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path, poll_interval_seconds=0.01)
    acknowledgement = channel.wait_for_acknowledgement("run", "missing", timeout_seconds=0.05)
    assert acknowledgement.status == "timeout"


def test_malformed_and_duplicate_pending_files_are_rejected(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path)
    pending = channel.control_layout("run")["pending"]
    pending.mkdir(parents=True)

    malformed = pending / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")

    duplicate_payload = {
        "action_id": "dup",
        "run_id": "run",
        "msg_type": "control.pause",
        "sender": "op",
        "payload": ["run"],
        "timestamp": 1.0,
    }
    (pending / "a.json").write_text(json.dumps(duplicate_payload), encoding="utf-8")
    (pending / "b.json").write_text(json.dumps(duplicate_payload), encoding="utf-8")

    listed = channel.list_requests("run", status="pending")
    assert len(listed) == 1
    rejected = channel.control_layout("run")["rejected"]
    rejected_files = sorted(rejected.glob("*.json"))
    assert len(rejected_files) == 2


def test_run_local_path_normalization_prevents_cross_run_leakage(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path)
    channel.send(ControlChannelMessage(msg_type="control.pause", sender="op", payload=("../runA",)))
    channel.send(ControlChannelMessage(msg_type="control.pause", sender="op", payload=("runB",)))

    run_a_pending = channel.control_layout("../runA")["pending"]
    run_b_pending = channel.control_layout("runB")["pending"]
    assert len(tuple(run_a_pending.glob("*.json"))) == 1
    assert len(tuple(run_b_pending.glob("*.json"))) == 1
    assert run_a_pending != run_b_pending


def test_wait_for_ack_observes_async_applied_receipt(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path, poll_interval_seconds=0.01)
    channel.send(ControlChannelMessage(msg_type="control.pause", sender="op", payload=("run",)))
    request = channel.list_requests("run")[0]

    def ack_later() -> None:
        time.sleep(0.03)
        channel.acknowledge_applied("run", request.action_id, reason="ok")

    thread = threading.Thread(target=ack_later)
    thread.start()
    try:
        acknowledgement = channel.wait_for_acknowledgement(
            "run", request.action_id, timeout_seconds=0.2
        )
    finally:
        thread.join()

    assert acknowledgement.status == "applied"


def test_send_to_control_requires_explicit_channel_instance(tmp_path) -> None:
    channel = FilesystemControlChannel(runs_root=tmp_path / "custom")

    send_to_control(
        ControlChannelMessage(msg_type="control.pause", sender="op", payload=("run",)),
        channel=channel,
    )
    pending = channel.list_requests("run")
    assert len(pending) == 1
