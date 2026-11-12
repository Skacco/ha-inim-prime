"""After a failed connection test the first-setup form keeps what was typed."""
from custom_components.inim_prime.config_flow.models import ConfigData, NativeConfig, PrimelanConfig
from custom_components.inim_prime.config_flow.schemas import build_connection_schema


def _defaults(schema: dict) -> dict:
    out = {}
    for key, value in schema.items():
        inner = getattr(value, "schema", None)
        if inner is not None:
            out[str(key)] = _defaults(inner.schema)
        else:
            default = key.default() if callable(getattr(key, "default", None)) else None
            out[str(key)] = default
    return out


def test_retry_keeps_password_and_api_key():
    data = ConfigData()
    data.use_native = True
    data.use_primelan = True
    data.host = "192.168.1.98"
    data.native = NativeConfig(port = 6004, password = "pass", use_outer_frame = True, pin = None)
    data.primelan = PrimelanConfig(api_key = "KEY", use_https = True)

    first_setup = _defaults(build_connection_schema(flow_data = data, prefill_secrets = True))
    assert first_setup["native"]["password"] == "pass"
    assert first_setup["primelan"]["api_key"] == "KEY"

    reconfigure = _defaults(build_connection_schema(flow_data = data))
    assert reconfigure["native"]["password"] is None
    assert reconfigure["primelan"]["api_key"] is None


async def test_setup_form_client_is_handed_over_and_never_leaked():
    """The Native client opened by the setup form is reused once, or closed."""
    import asyncio
    from custom_components.inim_prime import native_handoff

    class Client:
        closed = False

        def disconnect(self):
            self.closed = True

    first, second = Client(), Client()
    native_handoff.keep("192.0.2.1", 6004, first)
    assert native_handoff.take("192.0.2.1", 6004) is first      # picked up by setup
    assert native_handoff.take("192.0.2.1", 6004) is None       # only once
    assert not first.closed

    native_handoff.keep("192.0.2.1", 6004, second)
    native_handoff.drop("192.0.2.1", 6004)                      # setup did not happen
    assert second.closed and native_handoff.take("192.0.2.1", 6004) is None

    third = Client()
    native_handoff.KEEP_SECONDS, saved = 0.01, native_handoff.KEEP_SECONDS
    try:
        native_handoff.keep("192.0.2.1", 6004, third)
        await asyncio.sleep(0.05)                               # nobody came: closed
    finally:
        native_handoff.KEEP_SECONDS = saved
    assert third.closed


async def test_gateway_output_labels_can_come_from_the_saved_copy():
    """Saved PrimeLAN output names are used as they are; no PrimeLAN read then."""
    from custom_components.inim_prime.gateway import InimPrimeGateway

    class Native:
        outputs = {}

    class Primelan:
        reads = 0
        output_terminal_labels = {31: "Luce Portico"}

        async def update_outputs(self):
            self.reads += 1

    primelan = Primelan()
    gateway = InimPrimeGateway(native = Native(), primelan = primelan)
    assert gateway.uses_primelan_output_labels and gateway.primelan_output_labels is None

    gateway.set_primelan_output_labels({31: "Luce Portico", 32: "Vecchia"})
    await gateway.load_primelan_output_labels()        # already set: nothing to read
    assert primelan.reads == 0

    fresh = await gateway.fetch_primelan_output_labels()  # the background re-read
    assert primelan.reads == 1 and fresh == {31: "Luce Portico"}
    assert fresh != gateway.primelan_output_labels        # -> would save and reload

    assert not InimPrimeGateway(primelan = primelan).uses_primelan_output_labels


async def test_first_setup_reuses_the_client_parked_by_the_form():
    """The setup that follows the form must pick up the parked client.

    Regression: is_initialized is a property of the library's client; calling
    it raised TypeError and the very first setup after a fresh install failed
    with "Configurazione non riuscita".
    """
    import custom_components.inim_prime as integration
    from custom_components.inim_prime import native_handoff
    from inim.prime.native.client import Client

    client = Client(host = "192.0.2.1", password = "x", use_outer_frame = False, port = 6004, pin = None)
    client._initialized = True
    native_handoff.keep("192.0.2.1", 6004, client)

    gateway = integration._build_gateway({
        "serial_number": "1", "host": "192.0.2.1",
        "native": {"port": 6004, "password": "x", "use_outer_frame": False, "pin": None},
    })

    assert gateway._native._client is client
    assert native_handoff.take("192.0.2.1", 6004) is None
