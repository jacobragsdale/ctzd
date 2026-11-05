from pathlib import Path

from streamlit.testing.v1 import AppTest

PAGE = Path(__file__).parents[1] / "src" / "app" / "ui" / "main.py"


def test_page_renders_without_errors() -> None:
    page = AppTest.from_file(str(PAGE)).run(timeout=30)
    assert not page.exception
    assert page.title[0].value == "{{title}}"
