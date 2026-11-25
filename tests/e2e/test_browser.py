import os
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    if os.getenv("RUN_E2E") != "1":
        pytest.skip("Use make test-integration for real browser tests")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e.local_app:app" if os.getenv("LOCAL_BROWSER") == "1" else "tests.e2e.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8090",
            "--no-access-log",
        ]
    )
    try:
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8090/health/ready", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Browser test server did not become ready")
        yield "http://127.0.0.1:8090"
    finally:
        process.terminate()
        process.wait(timeout=15)


def test_register_poll_filter_and_support(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 1080})
    page.goto(base_url)
    expect(page.locator("#parcel-list .empty")).to_be_visible()
    if preview := os.getenv("PREVIEW_IMAGE"):
        page.screenshot(path=preview, full_page=True)
    page.get_by_label("Название", exact=True).fill("Browser parcel")
    page.get_by_label("Вес, кг").fill("2")
    page.get_by_label("Стоимость, USD").fill("100")
    page.get_by_label("Тип посылки", exact=True).select_option("2")
    page.get_by_role("button", name="Зарегистрировать посылку").click()
    expect(page.locator("#parcel-list")).to_contain_text("Browser parcel", timeout=30000)
    expect(page.locator("#parcel-list")).to_contain_text("180.00", timeout=30000)
    page.get_by_label("Фильтр по типу").select_option("1")
    expect(page.locator("#parcel-list")).not_to_contain_text("Browser parcel")
    page.get_by_label("Фильтр по типу").select_option("2")
    expect(page.locator("#parcel-list")).to_contain_text("Browser parcel")
    page.get_by_label("Ваш вопрос").fill("Можно отправить ноутбук?")
    page.get_by_role("button", name="Задать вопрос").click()
    expect(page.locator("#conversation")).to_contain_text("rules.md#2")
    page.get_by_role("link", name="Browser parcel").first.click()
    page.get_by_role("button", name="Получить ключ").click()
    expect(page.locator("#assignment-output")).not_to_be_empty()


def test_registration_during_status_poll(page: Page, base_url: str) -> None:
    # Start with a parcel already tracked in this tab.
    first_id = "11111111-1111-4111-8111-111111111111"
    page.add_init_script(f"sessionStorage.setItem('pending', JSON.stringify(['{first_id}']))")
    first_status = f"**/api/v1/registrations/{first_id}"
    delayed: list[Route] = []
    page.route(first_status, lambda route: delayed.append(route))
    with page.expect_request(first_status):
        page.goto(base_url)

    page.get_by_label("Название", exact=True).fill("Second parcel")
    page.get_by_label("Вес, кг").fill("2")
    page.get_by_label("Стоимость, USD").fill("100")
    page.get_by_label("Тип посылки", exact=True).select_option("2")
    with page.expect_response("**/api/v1/parcels") as registered:
        page.get_by_role("button", name="Зарегистрировать посылку").click()
    assert registered.value.status == 202
    second_id = registered.value.json()["id"]
    page.wait_for_function("id => JSON.parse(sessionStorage.pending).includes(id)", arg=second_id)

    delayed[0].fulfill(json={"status": "calculated"})
    page.wait_for_function("id => !JSON.parse(sessionStorage.pending).includes(id)", arg=first_id)
    expect(page.locator(f'#pending a[href="/parcels/{second_id}"]')).to_have_text(
        f"{second_id[:8]} · calculated", timeout=30000
    )
    assert page.evaluate("JSON.parse(sessionStorage.pending)") == []


def test_mobile_layout(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url)
    expect(page.get_by_role("button", name="Зарегистрировать посылку")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
