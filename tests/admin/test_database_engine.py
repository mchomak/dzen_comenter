import dzen_commenter.db as db


def test_create_database_engine_enables_connection_health_checks():
    factory = getattr(db, "create_database_engine", None)
    assert callable(factory), "dzen_commenter.db must expose create_database_engine"

    engine = factory("sqlite:///database-engine-options.db")
    try:
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 1800
    finally:
        engine.dispose()
