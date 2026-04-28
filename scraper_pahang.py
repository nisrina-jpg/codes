import asyncio
import json
from playwright.async_api import async_playwright, Frame, Page

PRIMARY_URL  = "https://zakatpahang.my/kalkulator-pendapatan/"
FALLBACK_URL = "https://new.zakatpahang.my/kira-zakat/zakat-pendapatan"

SCRAPE_JS = """
() => {
    // ── Visibility check ──────────────────────────────────────────────────────
    function isVisible(el) {
        if (!el) return false;
        if (el.offsetParent === null) return false;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        let p = el.parentElement;
        while (p && p !== document.body) {
            const ps = getComputedStyle(p);
            if (ps.display === 'none' || ps.visibility === 'hidden') return false;
            p = p.parentElement;
        }
        return true;
    }

    // ── Parse RM amounts from any string ─────────────────────────────────────
    function extractAmounts(text) {
        const amounts = [];
        const re = /RM\\s?([\\d,]+\\.?\\d*)/gi;
        let m;
        while ((m = re.exec(text)) !== null) {
            const v = parseFloat(m[1].replace(/,/g, ''));
            if (!isNaN(v) && v > 0) amounts.push(v);
        }
        return amounts;
    }

    function extractMultiplier(text) {
        const m = text.match(/x\\s*(\\d+)\\s*orang/i);
        return m ? parseInt(m[1]) : null;
    }

    // ── Get the full text of the container block around an input ─────────────
    // Climbs up until a block-level ancestor that holds the label + input together,
    // then returns its FULL innerText so RM amounts in sibling spans are included.
    function getContainerText(inp) {
        const BLOCK = new Set(['DIV','P','TR','TD','TH','LI','SECTION','ARTICLE','FORM','FIELDSET']);
        let curr = inp.parentElement;
        while (curr && curr !== document.body) {
            if (BLOCK.has(curr.tagName)) return (curr.innerText || '').trim().replace(/\\s+/g, ' ');
            curr = curr.parentElement;
        }
        return '';
    }

    // ── Get the label text for an input ──────────────────────────────────────
    function getLabelText(inp) {
        // 1. <label for="id">
        if (inp.id) {
            const lbl = document.querySelector('label[for="' + inp.id + '"]');
            if (lbl && isVisible(lbl)) return lbl.innerText.trim().replace(/\\s+/g, ' ');
        }
        // 2. Wrapping <label>
        const wrap = inp.closest('label');
        if (wrap) return wrap.innerText.trim().replace(/\\s+/g, ' ');

        // 3. <td> sibling / first cell in <tr>
        const cell = inp.closest('td, th');
        if (cell) {
            const prev = cell.previousElementSibling;
            if (prev) { const t = (prev.innerText||'').trim(); if (t.length > 1) return t; }
            const row = cell.closest('tr');
            if (row) {
                const first = row.querySelector('td, th');
                if (first && first !== cell) return (first.innerText||'').trim();
            }
        }

        // 4. Walk up siblings (up to 8 levels)
        let curr = inp;
        for (let i = 0; i < 8; i++) {
            const p = curr.parentElement;
            if (!p || p === document.body) break;
            for (const child of p.children) {
                if (child === curr || child.contains(inp)) continue;
                const t = (child.innerText||'').trim().replace(/\\s+/g,' ');
                if (t.length > 2) return t;
            }
            curr = p;
        }

        return inp.getAttribute('aria-label') || inp.placeholder || '';
    }

    // ── Find the nearest section heading above an element ────────────────────
    function getSectionHeading(el) {
        const HEADING = 'h2,h3,h4,h5';
        // walk backwards through siblings and up the DOM
        let curr = el;
        for (let depth = 0; depth < 12; depth++) {
            let sib = curr.previousElementSibling;
            while (sib) {
                if (sib.matches && sib.matches(HEADING)) {
                    const t = (sib.innerText||'').trim();
                    if (t.length > 3) return t;
                }
                // heading inside sib
                const inner = sib.querySelector && sib.querySelector(HEADING);
                if (inner) { const t = (inner.innerText||'').trim(); if (t.length>3) return t; }
                sib = sib.previousElementSibling;
            }
            curr = curr.parentElement;
            if (!curr || curr === document.body) break;
        }
        return '(Lain-lain)';
    }

    // ── Main scan ─────────────────────────────────────────────────────────────
    const fields = [];
    const seenKeys = new Set();

    for (const inp of document.querySelectorAll('input, select')) {
        if (!isVisible(inp)) continue;

        const labelText     = getLabelText(inp);
        const containerText = getContainerText(inp);   // ← full block text incl. RM siblings

        const key = labelText || inp.id || inp.name;
        if (!key || seenKeys.has(key)) continue;
        seenKeys.add(key);

        // Extract RM rate — prefer container text (wider net) over label alone
        const labelAmounts     = extractAmounts(labelText);
        const containerAmounts = extractAmounts(containerText);

        // Use container amounts if label has none (covers B2–B6 case where
        // "RM 3,000.00" sits in a sibling span, not in the <label> itself)
        const bestAmounts  = containerAmounts.length > 0 ? containerAmounts : labelAmounts;
        const ratePerUnit  = bestAmounts.length > 0 ? bestAmounts[0] : null;

        // Multiplier — check container text too
        const multiplier = extractMultiplier(containerText) ?? extractMultiplier(labelText);

        // Pre-filled value in the input box itself
        const rawValue    = (inp.value || '').trim();
        const inputNum    = parseFloat(rawValue.replace(/,/g,''));
        const inputValue  = !isNaN(inputNum) && inputNum !== 0 ? inputNum : null;

        const section = getSectionHeading(inp);

        fields.push({
            section,
            label:          labelText,
            container_text: containerText.slice(0, 200),   // for debugging
            rate_per_unit:  ratePerUnit,
            label_rm:       labelAmounts,
            container_rm:   containerAmounts,
            multiplier,
            input_value:    inputValue,
            input_raw:      rawValue,
            input_type:     inp.tagName === 'SELECT' ? 'select' : (inp.type || 'text'),
            input_id:       inp.id   || '',
            input_name:     inp.name || '',
            options:        inp.tagName === 'SELECT'
                ? Array.from(inp.options).map(o => ({ value: o.value, text: o.text.trim() }))
                : []
        });
    }

    return { fields };
}
"""

DEBUG_JS = """
() => ({
    title:        document.title,
    url:          location.href,
    input_count:  document.querySelectorAll('input, select').length,
    iframe_count: document.querySelectorAll('iframe').length,
    iframe_srcs:  Array.from(document.querySelectorAll('iframe')).map(f => f.src),
    body_snippet: document.body.innerText.trim().slice(0, 800),
})
"""


async def find_best_frame(page: Page):
    async def count_inputs(frame: Frame):
        try:
            return await frame.evaluate("() => document.querySelectorAll('input, select').length")
        except Exception:
            return 0

    best, best_n = page.main_frame, await count_inputs(page.main_frame)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        n = await count_inputs(frame)
        print(f"    iframe  url={frame.url!r}  inputs={n}")
        if n > best_n:
            best_n, best = n, frame
    print(f"    → best frame: {best.url!r}  ({best_n} inputs)")
    return best


def build_clean(raw: dict) -> dict:
    fields = raw.get("fields", [])

    sections = {}
    for f in fields:
        sections.setdefault(f["section"], []).append(f)

    had_kifayah = [f for f in fields if f["rate_per_unit"] is not None]
    prefilled   = [f for f in fields if f["input_value"] is not None]

    return {
        "had_kifayah_rates":   had_kifayah,
        "prefilled_constants": prefilled,
        "sections":            sections,
    }


def print_report(clean: dict):
    SEP = "─" * 72

    print("\n" + "=" * 72)
    print("  PUSAT KUTIPAN ZAKAT PAHANG — RATES & CONSTANTS")
    print("=" * 72)

    # ── Had Kifayah ──────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  HAD KIFAYAH (kadar per orang dari teks label / container)")
    print(SEP)
    hk = clean.get("had_kifayah_rates", [])
    if hk:
        print(f"  {'LABEL':<52} {'KADAR (RM)':>12}  MUL")
        print(f"  {'─'*52} {'─'*12}  {'─'*5}")
        for f in hk:
            lbl = f["label"][:51]
            amt = f"RM {f['rate_per_unit']:>10,.2f}"
            mul = str(f["multiplier"]) if f["multiplier"] is not None else "—"
            print(f"  {lbl:<52} {amt}  {mul}")
    else:
        print("  (tiada)")

    # ── Pre-filled constants ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  NILAI TETAP (pre-filled dalam kotak input — e.g. Nisab)")
    print(SEP)
    for f in clean.get("prefilled_constants", []):
        lbl = (f["label"] or f["input_id"])[:65]
        print(f"  • {lbl}")
        print(f"      RM {f['input_value']:,.2f}")

    # ── All fields by section ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  SEMUA MEDAN MENGIKUT BAHAGIAN")
    print(SEP)
    for section, fields in clean.get("sections", {}).items():
        print(f"\n  ▌ {section}")
        for f in fields:
            lbl      = (f["label"] or f["input_id"])[:60]
            rate_str = f"  → RM {f['rate_per_unit']:,.2f}/orang" if f["rate_per_unit"] else ""
            val_str  = f"  [nilai={f['input_value']:,.2f}]"      if f["input_value"]  else ""
            typ      = f"[{f['input_type']}]"
            print(f"      {typ} {lbl}{rate_str}{val_str}")
            if f["options"]:
                opts = ", ".join(o["text"] for o in f["options"][:8])
                if len(f["options"]) > 8:
                    opts += f" … (+{len(f['options'])-8})"
                print(f"           ↳ {opts}")

    print("\n" + "=" * 72)


async def main():
    raw = None

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

        for url in (PRIMARY_URL, FALLBACK_URL):
            page = await context.new_page()
            print(f"\n[*] Loading {url} …")
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"    ⚠ {e}")
                await page.close()
                continue

            await page.wait_for_timeout(3500)

            for sel in ["button:has-text('Accept')", "button:has-text('Terima')",
                        "button:has-text('OK')", "[class*='cookie'] button"]:
                try:
                    if await page.locator(sel).first.is_visible(timeout=500):
                        await page.locator(sel).first.click()
                        break
                except Exception:
                    pass

            dbg = await page.evaluate(DEBUG_JS)
            print(f"    title   : {dbg['title']}")
            print(f"    inputs  : {dbg['input_count']}")
            print(f"    iframes : {dbg['iframe_count']}")
            for src in dbg['iframe_srcs']:
                print(f"    ↳ iframe: {src}")
            print(f"    body    :\n{dbg['body_snippet']}\n")

            if dbg['iframe_count'] > 0:
                await page.wait_for_timeout(3000)

            frame = await find_best_frame(page)
            raw   = await frame.evaluate(SCRAPE_JS)

            n = len(raw.get("fields", []))
            print(f"    fields found: {n}")
            if n > 0:
                break
            else:
                print("    ✗ Nothing — trying next URL …")
                await page.close()

        await browser.close()

    if not raw or not raw.get("fields"):
        print("\n[✗] Nothing scraped. Check debug output above.")
        return

    with open("zakat_pahang_raw.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print("\n[✓] Raw  → zakat_pahang_raw.json")

    clean = build_clean(raw)
    with open("zakat_pahang_rates.json", "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    print("[✓] Clean → zakat_pahang_rates.json")

    print_report(clean)


if __name__ == "__main__":
    asyncio.run(main())