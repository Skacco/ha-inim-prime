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
