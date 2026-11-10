from pathlib import Path
from homeassistant.util.yaml import load_yaml
from custom_components.flinq_orion import SERVICES

SERVICES_YAML = Path(__file__).parents[1] / "custom_components" / "flinq_orion" / "services.yaml"


def test_services_yaml_parses_and_describes_every_action():
    # Home Assistant loads this file the same way; a parse error hides all descriptions.
    services = load_yaml(SERVICES_YAML)
    assert set(services) == set(SERVICES)
    for service in services.values():
        assert isinstance(service["name"], str) and isinstance(service["description"], str)
