"""
Single-file Browser Agent Server
=================================
Launches a visible Chromium browser AND an HTTP API server in one process.
Your local model/agent calls the endpoints below to see and control the browser.

Install:
    pip install fastapi uvicorn playwright
    playwright install chromium

Run:
    python browser_agent_server.py

Server runs at: http://localhost:5000
Interactive API docs: http://localhost:5000/docs
"""

import asyncio
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright

# ----------------------------------------------------------------------
# Global browser state
# ----------------------------------------------------------------------
_state = {"playwright": None, "browser": None, "context": None, "page": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch browser
    pw = await async_playwright().start()
    browser = await pw.firefox.launch(headless=False)
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()

    _state["playwright"] = pw
    _state["browser"] = browser
    _state["context"] = context
    _state["page"] = page

    print("[browser_agent_server] Browser launched, server ready at http://localhost:5000")
    yield

    # Shutdown: clean up
    await context.close()
    await browser.close()
    await pw.stop()


app = FastAPI(title="Browser Agent Server", lifespan=lifespan)


def get_page():
    page = _state["page"]
    if page is None:
        raise HTTPException(status_code=503, detail="Browser not ready yet")
    return page


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------
class NavigateRequest(BaseModel):
    url: str


class SelectorRequest(BaseModel):
    selector: str


class TypeRequest(BaseModel):
    selector: str
    text: str
    clear_first: bool = True


class ScrollRequest(BaseModel):
    direction: str = "down"   # "down" | "up"
    amount: int = 3           # multiplier, ~200px per unit


class WaitRequest(BaseModel):
    selector: str
    timeout_ms: int = 5000


class EvalRequest(BaseModel):
    script: str  # raw JS expression, executed via page.evaluate


# ----------------------------------------------------------------------
# Navigation
# ----------------------------------------------------------------------
@app.post("/navigate")
async def navigate(req: NavigateRequest):
    page = get_page()
    try:
        await page.goto(req.url, wait_until="domcontentloaded", timeout=15000)
        return {"success": True, "url": page.url, "title": await page.title()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/back")
async def go_back():
    page = get_page()
    await page.go_back()
    return {"success": True, "url": page.url}


@app.post("/forward")
async def go_forward():
    page = get_page()
    await page.go_forward()
    return {"success": True, "url": page.url}


@app.post("/reload")
async def reload_page():
    page = get_page()
    await page.reload()
    return {"success": True, "url": page.url}


# ----------------------------------------------------------------------
# Vision
# ----------------------------------------------------------------------
@app.get("/screenshot")
async def screenshot(full_page: bool = False):
    page = get_page()
    try:
        img_bytes = await page.screenshot(full_page=full_page)
        return {
            "image_base64": base64.b64encode(img_bytes).decode(),
            "url": page.url,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------
# Reading content
# ----------------------------------------------------------------------
@app.get("/content")
async def content():
    """Full HTML + plain visible text of the page."""
    page = get_page()
    try:
        html = await page.content()
        text = await page.inner_text("body")
        return {"url": page.url, "title": await page.title(), "html": html, "text": text}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/text")
async def text_of(selector: str):
    """Text of a specific element by CSS selector."""
    page = get_page()
    try:
        el = page.locator(selector).first
        return {"selector": selector, "text": await el.inner_text()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/elements")
async def list_elements(selector: str = "a, button, input, textarea, select, [role=button]"):
    """
    List interactive elements matching `selector`, each with a precise,
    ready-to-use unique CSS selector (nth-of-type based), so the model
    doesn't have to guess selectors blindly.
    """
    page = get_page()
    try:
        elements = await page.eval_on_selector_all(
            selector,
            """
            (els) => els.map((el) => {
                function cssPath(el) {
                    if (!(el instanceof Element)) return '';
                    const path = [];
                    while (el.nodeType === Node.ELEMENT_NODE) {
                        let selector = el.nodeName.toLowerCase();
                        if (el.id) {
                            selector += '#' + el.id;
                            path.unshift(selector);
                            break;
                        } else {
                            let sib = el, nth = 1;
                            while (sib.previousElementSibling) {
                                sib = sib.previousElementSibling;
                                if (sib.nodeName.toLowerCase() === selector) nth++;
                            }
                            selector += `:nth-of-type(${nth})`;
                        }
                        path.unshift(selector);
                        el = el.parentNode;
                    }
                    return path.join(' > ');
                }
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 100),
                    selector: cssPath(el),
                    visible: rect.width > 0 && rect.height > 0,
                    type: el.getAttribute('type') || null,
                    href: el.getAttribute('href') || null,
                };
            }).filter(e => e.visible)
            """,
        )
        return {"count": len(elements), "elements": elements}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------
# Interaction
# ----------------------------------------------------------------------
@app.post("/click")
async def click(req: SelectorRequest):
    page = get_page()
    try:
        await page.locator(req.selector).first.click(timeout=5000)
        return {"success": True, "action": f"clicked '{req.selector}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/type")
async def type_text(req: TypeRequest):
    page = get_page()
    try:
        locator = page.locator(req.selector).first
        if req.clear_first:
            await locator.fill(req.text, timeout=5000)
        else:
            await locator.type(req.text, timeout=5000)
        return {"success": True, "action": f"typed into '{req.selector}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/press")
async def press_key(key: str, selector: str = None):
    """Press a keyboard key, e.g. 'Enter', 'Tab'. Optionally scoped to a selector."""
    page = get_page()
    try:
        if selector:
            await page.locator(selector).first.press(key)
        else:
            await page.keyboard.press(key)
        return {"success": True, "action": f"pressed '{key}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/scroll")
async def scroll(req: ScrollRequest):
    page = get_page()
    try:
        delta = req.amount * 200 * (1 if req.direction == "down" else -1)
        await page.evaluate(f"window.scrollBy(0, {delta})")
        return {"success": True, "action": f"scrolled {req.direction} by {req.amount}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/wait")
async def wait_for(req: WaitRequest):
    page = get_page()
    try:
        await page.wait_for_selector(req.selector, timeout=req.timeout_ms)
        return {"success": True, "action": f"waited for '{req.selector}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/evaluate")
async def evaluate(req: EvalRequest):
    """Run arbitrary JS in the page context and return the result. Use with care."""
    page = get_page()
    try:
        result = await page.evaluate(req.script)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------
@app.get("/status")
async def status():
    page = get_page()
    return {"ready": True, "url": page.url, "title": await page.title()}


# ----------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)