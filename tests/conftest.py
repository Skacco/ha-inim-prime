import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    # the recorder has to be set up before hass: a test asking for it gets it
    # first, then custom integrations are enabled as for every other test
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield
