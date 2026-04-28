from fastapi import FastAPI
import asyncio
import json
import os
from playwright.async_api import async_playwright
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException

app = FastAPI()

TARGET_URL = "https://www.zakatselangor.com.my/kira-zakat/"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to your domain later
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# JS: scrape only VISIBLE h3 sections and their VISIBLE inputs
# ---------------------------------------------------------------------------

SCRAPE_VISIBLE_JS = """
() => {
    function isVisible(el) {
        if (!el) return false;
        if (el.offsetParent === null) return false;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        // Walk up and check parents too
        let parent = el.parentElement;
        while (parent && parent !== document.body) {
            const ps = getComputedStyle(parent);
            if (ps.display === 'none' || ps.visibility === 'hidden') return false;
            parent = parent.parentElement;
        }
        return true;
    }

    function getLabelForInput(inp) {
        // 1. Explicit <label for="id">
        if (inp.id) {
            const lbl = document.querySelector('label[for="' + inp.id + '"]');
            if (lbl && isVisible(lbl)) return lbl.innerText.trim();
        }
        // 2. Wrapping <label>
        const wrap = inp.closest('label');
        if (wrap) {
            return wrap.innerText.replace(/[\\d.,]+/g, '').trim(); 
        }

        // 3. Walk up the tree to find text-bearing siblings (Fixes layout divs)
        let curr = inp;
        for (let i = 0; i < 5; i++) { // Climb up to 5 levels
            let p = curr.parentElement;
            if (!p) break;
            
            for (let child of p.children) {
                // Skip the branch containing the input
                if (child === curr || child.contains(inp)) continue;
                
                let t = child.innerText ? child.innerText.trim() : '';
                // Ignore generic symbols or default zeros
                if (t && t !== 'RM' && t !== '00.00' && t !== '0.00' && t !== '0' && t.length > 2) {
                    return t.replace(/\\s+/g, ' ').trim(); // Flatten spaces/newlines
                }
            }
            curr = p;
        }

        // 4. Fallback to aria-label or placeholder
        let fallback = inp.getAttribute('aria-label') || inp.placeholder || '';
        // Don't treat zeros as valid text labels
        if (fallback === '00.00' || fallback === '0.00' || fallback === '0') fallback = '';
        
        return fallback;
    }

    const result = {};
    const seen_sections = new Set();

    const allH3 = Array.from(document.querySelectorAll('h3'));
    const visibleH3 = allH3.filter(isVisible);

    for (const h3 of visibleH3) {
        const heading = h3.innerText.trim();
        if (!heading || seen_sections.has(heading)) continue;
        seen_sections.add(heading);

        const fields = [];
        const seen_labels = new Set();

        let el = h3.nextElementSibling;
        while (el && el.tagName !== 'H3') {
            if (isVisible(el)) {
                const inputs = el.querySelectorAll(
                    'input[type="number"], input[type="text"], input:not([type]), select'
                );
                for (const inp of inputs) {
                    if (!isVisible(inp)) continue;   
                    
                    let label = getLabelForInput(inp);
                    
                    // If we STILL have no label, use the ID so it doesn't get discarded
                    if (!label && inp.id) {
                        label = "ID: " + inp.id;
                    }
                    
                    if (!label || seen_labels.has(label)) { continue; }
                    seen_labels.add(label);
                    
                    fields.push({
                        label:      label,
                        input_id:   inp.id   || '',
                        input_name: inp.name || '',
                        input_type: inp.type || 'text',
                        input_value: inp.value || ''  //
                    });
                }
            }
            el = el.nextElementSibling;
        }

        if (fields.length > 0) {
            result[heading] = fields;
        }
    }

    return result;
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def open_dropdown_and_pick(page, item_text: str):
    """Open the custom dropdown and click the matching zakat type."""
    # 1. Determine what the dropdown is currently displaying to click it
    current_text = "Zakat Pendapatan" if item_text == "Zakat Perniagaan" else "Zakat Perniagaan"

    # 2. Click the dropdown trigger
    opened = False
    triggers = [
        f"text=\"{current_text}\"", # Strict exact text match (quotes matter)
        "[role='combobox']",
        ".vs__dropdown-toggle",
        "[class*='dropdown-toggle']"
    ]
    
    for sel in triggers:
        try:
            # Gather all matches and click the last visible one 
            # (bypasses hidden duplicate menus often built for mobile)
            elements = await page.locator(sel).all()
            for el in reversed(elements):
                if await el.is_visible():
                    await el.click(timeout=1500)
                    await page.wait_for_timeout(800)
                    opened = True
                    break
            if opened:
                break
        except Exception:
            continue

    # 3. Click the target option
    clicked = False
    options = [
        f"[role='option']:has-text('{item_text}')",
        f"li:has-text('{item_text}')",
        f".dropdown-item:has-text('{item_text}')",
        f"text=\"{item_text}\"" # Strict exact text match
    ]
    
    for sel in options:
        try:
            elements = await page.locator(sel).all()
            for el in reversed(elements):
                if await el.is_visible():
                    await el.click(timeout=1500)
                    # CRITICAL: Give the DOM enough time to swap out the old form fields
                    await page.wait_for_timeout(2000) 
                    clicked = True
                    break
            if clicked:
                break
        except Exception:
            continue

    return clicked


async def click_subtab(page, text: str):
    """Click a radio-style sub-tab (Tanpa Tolakan / Dengan Tolakan)."""
    for sel in [
        f"label:has-text('{text}')",
        f"[type='radio'] + label:has-text('{text}')",
        f"button:has-text('{text}')",
        f"[role='tab']:has-text('{text}')",
        f"text={text}",
    ]:
        try:
            await page.locator(sel).first.click(timeout=2000)
            await page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


async def scrape_visible(page) -> dict:
    return await page.evaluate(SCRAPE_VISIBLE_JS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def scrape_zakat_calculator():
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print(f"[*] Loading {TARGET_URL} ...")
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2500)
        print(f"[*] Page loaded: {await page.title()}")

        # ── 1. Zakat Pendapatan — Tanpa Tolakan ──────────────────────────────
        print("\n[1] Zakat Pendapatan → Tanpa Tolakan")
        await open_dropdown_and_pick(page, "Zakat Pendapatan")
        await click_subtab(page, "Tanpa Tolakan")

        data = await scrape_visible(page)
        results["zakat_pendapatan_tanpa_tolakan"] = data
        print(f"    {len(data)} sections, {sum(len(v) for v in data.values())} fields")

        # ── 2. Zakat Pendapatan — Dengan Tolakan ─────────────────────────────
        print("\n[2] Zakat Pendapatan → Dengan Tolakan")
        await click_subtab(page, "Dengan Tolakan")

        data = await scrape_visible(page)
        results["zakat_pendapatan_dengan_tolakan"] = data
        print(f"    {len(data)} sections, {sum(len(v) for v in data.values())} fields")

        # ── 3. Zakat Perniagaan ───────────────────────────────────────────────
        print("\n[3] Zakat Perniagaan")
        await open_dropdown_and_pick(page, "Zakat Perniagaan")

        data = await scrape_visible(page)
        results["zakat_perniagaan"] = data
        print(f"    {len(data)} sections, {sum(len(v) for v in data.values())} fields")

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(data: dict):
    titles = {
        "zakat_pendapatan_tanpa_tolakan":  "ZAKAT PENDAPATAN — TANPA TOLAKAN",
        "zakat_pendapatan_dengan_tolakan": "ZAKAT PENDAPATAN — DENGAN TOLAKAN",
        "zakat_perniagaan":                "ZAKAT PERNIAGAAN",
    }
    print("\n" + "=" * 65)
    print("  LEMBAGA ZAKAT SELANGOR — CALCULATOR FIELDS")
    print("=" * 65)
    for key, title in titles.items():
        sections = data.get(key, {})
        print(f"\n{'─' * 65}")
        print(f"  {title}")
        print(f"{'─' * 65}")
        if not sections:
            print("  (no data scraped)")
            continue
        for heading, fields in sections.items():
            print(f"\n  ▌ {heading}")
            for f in fields:
                id_hint = f"  (id={f['input_id']})" if f["input_id"] else ""
                print(f"      • {f['label']}{id_hint}")
    print("\n" + "=" * 65)


async def main():
    data = await scrape_zakat_calculator()

    out = "zakat_data_selangor.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"\n[✓] Saved → {out}")
    print_report(data)

# ---------------- API ROUTES ---------------- #

@app.get("/")
def root():
    return {"message": "Zakat Scraper API is running"}

@app.get("/scrape")
async def scrape():
    try:
        data = await scrape_zakat_calculator()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Optional cache
cache = {}

CACHE_FILE = "zakat_data_selangor.json"

@app.get("/scrape-cached")
async def scrape_cached():
    # Return from file if exists
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    
    # Otherwise scrape and save
    data = await scrape_zakat_calculator()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data

@app.get("/refresh")
async def refresh():
    """Force re-scrape and update cache."""
    data = await scrape_zakat_calculator()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "refreshed", "sections": list(data.keys())}

if __name__ == "__main__":
    import sys
    if "api" in sys.argv:
        import uvicorn
        uvicorn.run("scraper_selangor:app", host="0.0.0.0", port=8001, reload=False)
    else:
        asyncio.run(main())