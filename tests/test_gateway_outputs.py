"""Backend choice for outputs: native when configured, otherwise PrimeLAN."""
from types import MappingProxyType

from custom_components.inim_prime.gateway import InimPrimeGateway
from custom_components.inim_prime.models.outputs import UnifiedOutput


class FakeBackend:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self._outputs = {1: UnifiedOutput(1, name, False)}

    @property
    def outputs(self):
        return MappingProxyType(self._outputs)

    async def update_outputs(self):
        self.calls.append("update")
        return self.outputs

    async def set_output(self, output_id, state, level=None):
        self.calls.append(("set", output_id, state, level))


async def test_both_backends_use_native_for_outputs():
    native, primelan = FakeBackend("native"), FakeBackend("primelan")
    gw = InimPrimeGateway(native=native, primelan=primelan)
    assert gw.supports_outputs and gw.supports_scenarios
    assert gw.outputs[1].label == "native"
    await gw.update_outputs()
    await gw.set_output(1, True)
    assert native.calls == ["update", ("set", 1, True, None)]
    assert primelan.calls == ["update"]  # once, only to read labels; commands go to native


async def test_primelan_only_uses_primelan_for_outputs():
    primelan = FakeBackend("primelan")
    gw = InimPrimeGateway(primelan=primelan)
    assert gw.outputs[1].label == "primelan"
    await gw.set_output(1, True, 50)
    assert primelan.calls == [("set", 1, True, 50)]


async def test_native_only_has_outputs_but_no_scenarios():
    gw = InimPrimeGateway(native=FakeBackend("native"))
    assert gw.supports_outputs and not gw.supports_scenarios


def test_outer_frame_hidden_with_primelan():
    from custom_components.inim_prime.config_flow.models import ConfigData
    from custom_components.inim_prime.config_flow.schemas import build_connection_schema

    def native_keys(use_primelan):
        schema = build_connection_schema(flow_data=ConfigData(use_native=True, use_primelan=use_primelan))
        native = next(v for k, v in schema.items() if str(k) == "native")
        return {str(k) for k in native.schema.schema}

    assert "use_outer_frame" not in native_keys(True)
    assert "use_outer_frame" in native_keys(False)


async def test_connection_errors_say_which_backend_failed():
    from unittest.mock import patch
    from custom_components.inim_prime.config_flow import config_flow as cf
    from custom_components.inim_prime.config_flow.models import ConfigData, NativeConfig, PrimelanConfig

    data = ConfigData(host="192.0.2.1", use_native=True, use_primelan=True,
                      native=NativeConfig(6004, "pass", True, None),
                      primelan=PrimelanConfig("k", False))

    async def boom(*a, **k):
        raise TimeoutError()

    async def ok(*a, **k):
        return "SER1"

    with patch.object(cf, "test_native_connection_and_get_serial", boom), \
         patch.object(cf, "test_primelan_connection", ok):
        errors, ph = await cf._run_connection_tests(data, get_serial=True)
    assert errors == {"base": "connection_failed"}
    assert ph["status"].startswith("Native: FAILED (timeout") and ph["status"].endswith("PrimeLAN: OK")

    async def refused(*a, **k):
        raise ConnectionRefusedError()

    with patch.object(cf, "test_native_connection_and_get_serial", ok), \
         patch.object(cf, "test_primelan_connection", refused):
        errors, ph = await cf._run_connection_tests(data, get_serial=True)
    assert errors == {"base": "connection_failed"} and data.serial_number == "SER1"
    assert ph["status"] == "Native: OK | PrimeLAN: FAILED (connection refused (port closed))"

    with patch.object(cf, "test_native_connection_and_get_serial", boom), \
         patch.object(cf, "test_primelan_connection", refused):
        errors, ph = await cf._run_connection_tests(data, get_serial=True)
    assert errors == {"base": "connection_failed"}
    assert ph["status"].count("FAILED") == 2

    # nothing failing -> no error
    with patch.object(cf, "test_native_connection_and_get_serial", ok), \
         patch.object(cf, "test_primelan_connection", ok):
        errors, _ = await cf._run_connection_tests(data, get_serial=True)
    assert errors == {}


async def test_native_output_without_label_does_not_crash():
    from unittest.mock import patch
    from custom_components.inim_prime import native_compat  # noqa: F401
    from inim.prime.native.helpers import terminals as t
    from inim.prime.native.models.terminals import TerminalType

    class Status:
        def __init__(self, typ):
            self.type = typ

    async def statuses(*a, **k):
        return {6: Status(TerminalType.OUTPUT)}

    async def empty(*a, **k):
        return {}

    async def board_labels(*a, **k):
        return {1005: "SIRENA"}  # only on-board outputs, like the library

    with patch.object(t, "get_terminal_statuses", statuses), \
         patch.object(t, "get_zone_labels", empty), patch.object(t, "get_zone_settings", empty), \
         patch.object(native_compat, "_original_get_output_labels", board_labels), \
         patch.object(t.Output, "decode", lambda **kw: kw):
        terminals = await t.initialize_terminals(protocol=None)
    assert terminals[6]["label"] == ""


async def test_native_outputs_named_from_primelan():
    native, primelan = FakeBackend("native"), FakeBackend("primelan")
    native._outputs = {1: UnifiedOutput(1, "", False), 2: UnifiedOutput(2, "Board", True)}
    primelan._outputs = {1: UnifiedOutput(1, "CHIUSURA CASA", False), 2: UnifiedOutput(2, "x", True)}
    gw = InimPrimeGateway(native=native, primelan=primelan)
    outputs = await gw.update_outputs()
    assert outputs[1].label == "CHIUSURA CASA" and outputs[2].label == "Board"
    # PrimeLAN asked only once for labels
    await gw.update_outputs()
    assert primelan.calls == ["update"]
