"""Headless Chrome über das DevTools-Protokoll, ohne Zusatzpakete (nur websockets).

Chrome rendert eine HTML-Seite Bild für Bild: SEEK(t) setzt jedes Element auf den Zeitpunkt t,
danach wird ein Screenshot gemacht. So entsteht Motion Design mit echtem CSS (Schatten, Rundungen,
Variable Fonts), wiederholbar und unabhängig von der Rechenzeit.
"""
import base64, itertools, json, os, shutil, subprocess, tempfile, time

import httpx
from websockets.sync.client import connect

KANDIDATEN = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]


def chrome_pfad():
    for p in KANDIDATEN:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("Chrome wurde nicht gefunden (Umgebungsvariable CHROME setzen)")


class Chrome:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.profil = tempfile.mkdtemp(prefix="cf-chrome-")
        self.proc = subprocess.Popen(
            [chrome_pfad(), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profil}",
             "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--mute-audio",
             "--force-color-profile=srgb", "--font-render-hinting=none", "--disable-gpu",
             "--disable-extensions", "--disable-background-networking", "--disable-sync",
             f"--window-size={w},{h}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(self.profil, "DevToolsActivePort")
        for _ in range(200):
            if os.path.exists(port_file) and open(port_file).read().count("\n") >= 1:
                break
            time.sleep(0.05)
        else:
            self.close()
            raise RuntimeError("Chrome startet nicht")
        port = int(open(port_file).read().split("\n")[0])
        page = next(t for t in httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=10).json() if t["type"] == "page")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None, open_timeout=15)
        self._ids = itertools.count(1)
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        i = next(self._ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=120))
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def js(self, expr, warten=True):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=warten, returnByValue=True)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError("Seite: " + (d.get("exception", {}).get("description") or d.get("text", "Fehler"))[:300])
        return r.get("result", {}).get("value")

    def oeffnen(self, pfad):
        self.call("Page.navigate", url="file://" + os.path.abspath(pfad))
        for _ in range(300):
            try:
                if self.js("typeof window.SEEK === 'function' && document.readyState === 'complete'", warten=False):
                    break
            except RuntimeError:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("Seite lädt nicht")
        self.js("window.READY")

    def bild(self, t, qualitaet=92):
        self.js(f"SEEK({t:.4f})")
        r = self.call("Page.captureScreenshot", format="jpeg", quality=qualitaet, optimizeForSpeed=True,
                      captureBeyondViewport=False)
        return base64.b64decode(r["data"])

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.terminate(); self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profil, ignore_errors=True)
