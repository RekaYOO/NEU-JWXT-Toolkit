from backend.app.routers import system_settings


class Storage:
    def __init__(self):
        self.config = {}
    def load_config(self):
        return dict(self.config)
    def save_config(self, value):
        self.config = dict(value)


class Spec:
    def __init__(self, minutes):
        from datetime import timedelta
        self.max_age = timedelta(minutes=minutes)


class Registry:
    def resources(self):
        return ("scores", "academic-report")
    def get(self, name):
        return Spec(5 if name == "scores" else 10)


class Coordinator:
    registry = Registry()
    def set_policies(self, policies):
        self.policies = policies


def test_cache_settings_round_trip_and_preserve_unrelated_config():
    storage = Storage()
    storage.config = {"other": {"kept": True}}
    coordinator = Coordinator()
    result = system_settings.update_cache_settings(
        system_settings.CacheSettingsUpdate(resources={
            "scores": {"enabled": False, "interval_minutes": 20},
        }), storage, coordinator,
    )
    assert result.cache["scores"].enabled is False
    assert storage.config["other"] == {"kept": True}
    assert coordinator.policies["scores"] == {"enabled": False, "interval_seconds": 1200}


def test_unknown_cache_resource_is_rejected():
    import pytest
    with pytest.raises(Exception, match="未知缓存资源"):
        system_settings.update_cache_settings(
            system_settings.CacheSettingsUpdate(resources={"missing": {}}), Storage(), Coordinator(),
        )


def test_long_default_interval_is_clamped_before_schema_validation():
    storage = Storage()

    class LongRegistry(Registry):
        def get(self, name):
            return Spec(43_200)

    class LongCoordinator(Coordinator):
        registry = LongRegistry()

    result = system_settings.get_cache_settings(storage, LongCoordinator())
    assert result.cache["scores"].interval_minutes == 43_200
