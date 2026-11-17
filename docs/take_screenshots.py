"""Screenshots for the README from a local Odoo with demo data.

Usage (Odoo running via `docker compose up -d`, module installed with demo data):
    python docs/take_screenshots.py

Logs in with the local throwaway admin account and saves PNG files (and one sample PDF) next to this script.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8069"
OUT = Path(__file__).resolve().parent
ACTION = "/odoo/action-transport_order.transport_order_action"
ANALYSIS = "/odoo/action-transport_order.transport_order_action_analysis"
ORDER_ID = 3  # demo order in transit with three stops

SHOTS = [
    ("01_list.png", ACTION, ".o_list_table"),
    ("02_form.png", f"{ACTION}/{ORDER_ID}", ".o_form_sheet"),
    ("03_kanban.png", f"{ACTION}?view_type=kanban", ".o_kanban_renderer"),
    ("04_pivot.png", ANALYSIS, ".o_pivot"),
    ("05_carrier_order.png", f"/report/html/transport_order.report_carrier_order/{ORDER_ID}", ".page"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=1)
        page.goto(f"{BASE}/web/login")
        page.fill("input[name=login]", "admin")
        page.fill("input[name=password]", "admin")
        page.click("button[type=submit]")
        page.wait_for_url("**/odoo**")
        for name, url, selector in SHOTS:
            page.goto(BASE + url)
            page.wait_for_selector(selector)
            page.wait_for_timeout(1200)  # let fonts, avatars and animations settle
            page.screenshot(path=str(OUT / name))
            print("saved", name)
        pdf = page.request.get(f"{BASE}/report/pdf/transport_order.report_carrier_order/{ORDER_ID}")
        (OUT / "carrier_order_example.pdf").write_bytes(pdf.body())
        print("saved carrier_order_example.pdf", pdf.status, pdf.headers.get("content-type"))
        browser.close()


if __name__ == "__main__":
    main()
