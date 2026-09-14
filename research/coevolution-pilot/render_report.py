from paths import ROOT
from playwright.sync_api import sync_playwright

root = ROOT
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1440, 'height': 1050}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto((root / 'report.html').as_uri(), wait_until='load')
    assert page.locator('#cards .card').count() == 4
    assert page.locator('#docs details').count() >= 4
    page.screenshot(path=str(root / 'report-preview.png'), full_page=True)
    assert not errors, errors
    browser.close()
print(root / 'report-preview.png')
