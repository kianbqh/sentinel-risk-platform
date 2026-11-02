import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kind_run import validate_serving_state


def fixture():
    deployment = {"metadata": {"generation": 2}, "spec": {"replicas": 2},
                  "status": {"observedGeneration": 2, "updatedReplicas": 2, "readyReplicas": 2, "availableReplicas": 2}}
    pods = [{"metadata": {"name": uid, "uid": uid}, "spec": {"containers": [{"image": "gateway:m1b-b"}]},
             "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}
            for uid in ("new-1", "new-2")]
    endpoints = [{"endpoints": [{"targetRef": {"kind": "Pod", "uid": uid},
                                 "conditions": {"ready": True, "terminating": False}}
                                for uid in ("new-1", "new-2")]}]
    return deployment, pods, endpoints


def test_terminating_old_pod_still_listed_is_preserved_but_not_serving():
    deployment, pods, endpoints = fixture()
    old = copy.deepcopy(pods[0])
    old["metadata"].update(uid="old", deletionTimestamp="2026-11-02T05:40:01Z")
    old["spec"]["containers"][0]["image"] = "gateway:m1b-a"
    pods.append(old)
    endpoints[0]["endpoints"].append({"targetRef": {"kind": "Pod", "uid": "old"},
                                    "conditions": {"ready": False, "terminating": True}})
    ready = validate_serving_state(deployment, pods, endpoints, "m1b-b")
    assert {pod["metadata"]["uid"] for pod in ready} == {"new-1", "new-2"}
    assert len(pods) == 3  # The original full observation remains intact.


def test_active_old_service_endpoint_cannot_be_ignored():
    deployment, pods, endpoints = fixture()
    pods[0]["spec"]["containers"][0]["image"] = "gateway:m1b-a"
    with pytest.raises(AssertionError, match="actual serving Pod"):
        validate_serving_state(deployment, pods, endpoints, "m1b-b")


def test_stale_endpoint_and_unobserved_generation_are_rejected():
    deployment, pods, endpoints = fixture()
    endpoints[0]["endpoints"][0]["targetRef"]["uid"] = "old"
    with pytest.raises(AssertionError, match="exactly match"):
        validate_serving_state(deployment, pods, endpoints, "m1b-b")
    deployment["status"]["observedGeneration"] = 1
    with pytest.raises(AssertionError, match="not observed"):
        validate_serving_state(deployment, pods, endpoints, "m1b-b")
