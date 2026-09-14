def test_import_app():
    from app.main import app
    assert app.title == "AI Habit Tracker API"
