"""Architecture fitness function: port registry + contract governance
(ARCHITECTURE §11, §19; IMPLEMENTATION_PLAN Phase 4).

Keeps the canonical port set (``app.platform.ports``) stable and intentional,
and enforces the governance rule that a stateful infrastructure port must have a
contract test. Adding/removing a port is therefore a deliberate, reviewed change
(it fails this test until the set + coverage expectations are updated).
"""

from __future__ import annotations

import abc
import inspect
from pathlib import Path

import app.platform.ports as ports

# The canonical port set (ARCHITECTURE §11). A port is added here only with an
# ADR + ≥2 implementations or a declared external boundary.
EXPECTED_PORTS = {
    "KVStorePort",
    "StoragePort",
    "MailerPort",
    "CaptchaPort",
    "BreachCheckPort",
}

# Ports whose behavior is stateful/drift-prone and therefore REQUIRE a shared
# contract-test suite run against every implementation (ARCHITECTURE §19).
# Others (thin, stateless interface conformance) are covered by their unit tests.
CONTRACT_REQUIRED = {
    "KVStorePort": "tests/contract/test_kvstore_contract.py",
}


def test_port_set_is_exactly_as_declared():
    exported = set(ports.__all__)
    assert exported == EXPECTED_PORTS, (
        "The canonical port set changed. Adding/removing a port requires an ADR "
        "(ARCHITECTURE §11/§19). Update EXPECTED_PORTS deliberately.\n"
        f"  exported={sorted(exported)}\n  expected={sorted(EXPECTED_PORTS)}"
    )


def test_every_port_is_an_abstract_base():
    for name in ports.__all__:
        obj = getattr(ports, name)
        assert inspect.isclass(obj), f"{name} is not a class"
        assert issubclass(obj, abc.ABC) or getattr(obj, "__abstractmethods__", None), (
            f"{name} must be an abstract contract (an ABC/Protocol), not a concrete class"
        )


def test_stateful_ports_have_a_contract_test():
    backend_root = Path(__file__).resolve().parents[2]  # apps/backend
    missing = []
    for port, rel_path in CONTRACT_REQUIRED.items():
        if not (backend_root / rel_path).exists():
            missing.append(f"{port}: expected contract test at {rel_path}")
    assert not missing, "Ports missing their required contract test:\n" + "\n".join(missing)
