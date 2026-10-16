import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from swing.engine import advance
from swing.server import LocalServer
from swing.store import Store


def test_responsive_browser_full_user_journey(tmp_path):
    server = LocalServer(Store(tmp_path / "browser.sqlite3"), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    captures = Path("docs/captures")
    captures.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.get_by_role("heading", name="Une séance à la fois.")).to_be_visible()
            expect(page.get_by_role("button", name="Traiter une séance")).to_be_disabled()
            page.locator('input[name="fee"]').fill("1.25")
            page.get_by_role("button", name="Enregistrer les hypothèses").click()
            expect(page.locator("#message")).to_contain_text("Hypothèses enregistrées")
            page.get_by_role("button", name="Charger l’exemple synthétique").click()
            expect(page.locator("#data-tag")).to_have_text("Données synthétiques")
            expect(page.locator("#data-details")).to_contain_text("300")
            page.get_by_role("button", name="Traiter une séance").click()
            expect(page.locator("#cursor")).to_have_text("2026-01-02")
            expect(page.locator('input[name="fee"]')).to_be_disabled()
            page.get_by_role("button", name="Démarrer le replay").click()
            expect(page.locator("#worker-status")).to_contain_text("Replay en cours")
            page.get_by_role("button", name="Mettre en pause").click()
            expect(page.locator("#worker-status")).to_contain_text("Moteur arrêté")
            with server.store.transaction() as db:
                cursor = Store.state(db)["cursor"]
            for _ in range(58):
                result = advance(server.store, cursor)
                cursor = result["cursor"]
            page.reload()
            expect(page.locator("#cursor")).to_have_text(cursor)
            expect(page.locator("#trades tbody tr").first).to_be_visible()
            expect(page.locator("#decisions article")).to_have_count(9)
            page.locator("#journal-filter").select_option("entry")
            expect(page.locator("#events")).to_contain_text("Entrée paper")
            page.locator("#events details").first.locator("summary").click()
            expect(page.locator("#events pre").first).to_contain_text("evidence_hash")
            with page.expect_download() as downloaded:
                page.get_by_role("link", name="Exporter tout en JSON").click()
            assert downloaded.value.suggested_filename == "journal-swing-local.json"
            page.locator("#journal-filter").select_option("all")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_function("window.scrollY === 0")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(captures / "ordinateur.png"))
            page.get_by_role("link", name="Portefeuille paper", exact=True).click()
            page.screenshot(path=str(captures / "portefeuille.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="Accueil & données", exact=True).click()
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_function("window.scrollY === 0")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            expect(page.get_by_role("button", name="Traiter une séance")).to_be_visible()
            page.screenshot(path=str(captures / "mobile.png"))
            page.get_by_role("link", name="Portefeuille paper", exact=True).click()
            expect(
                page.get_by_role("heading", name="Portefeuille paper", exact=True)
            ).to_be_in_viewport()
            page.screenshot(path=str(captures / "mobile-portefeuille.png"))
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
