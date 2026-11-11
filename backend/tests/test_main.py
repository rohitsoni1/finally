from app import main


async def test_default_app_uses_simulator(tmp_path, monkeypatch):
    from app.db import connection

    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "f.db"))
    connection.reset_for_tests()
    app = main.create_app()
    async with app.router.lifespan_context(app):
        assert app.state.market.name == "simulator"
        assert len(app.state.price_cache) == 10
        task = app.state.market._task
        assert task is not None
    assert app.state.market._task is None
    assert app.state.snapshot_task.cancelled()
    connection.reset_for_tests()


def test_static_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path))
    assert main.static_dir() == tmp_path.resolve()
    monkeypatch.delenv("FINALLY_STATIC_DIR")
    assert main.static_dir() == (main.BACKEND_DIR / "static").resolve()


def test_chat_router_included():
    paths = set(main.app.openapi()["paths"])
    assert "/api/chat" in paths
