"""Minimal Chrome DevTools Protocol helper (stdlib + websocket-client).

Usage examples:
    python tools/cdp.py open https://example.com
    python tools/cdp.py eval "document.title"
    python tools/cdp.py text              # visible innerText of body
    python tools/cdp.py click "button.primary"
    python tools/cdp.py type "input#user_code" "ABCD-1234"
"""
import json
import os
import sys
import time
import urllib.request

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_v, None)

import websocket  # noqa: E402

DEBUG_PORT = int(os.environ.get("CDP_PORT", "9222"))


def _http(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}{path}", timeout=8) as r:
        return json.loads(r.read())


def page_target():
    targets = [t for t in _http("/json/list") if t.get("type") == "page"]
    if not targets:
        raise RuntimeError("no page target")
    return targets[0]


class CDP:
    def __init__(self, url=None):
        self.ws_url = url or page_target()["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(self.ws_url, timeout=40,
                                              suppress_origin=True, max_size=64 * 1024 * 1024)
        self._id = 0

    def call(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + 45
        while time.time() < deadline:
            raw = self.ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def eval(self, expr, await_promise=True):
        res = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                        awaitPromise=await_promise, userGesture=True)
        if "exceptionDetails" in res:
            raise RuntimeError(res["exceptionDetails"].get("text", "js error"))
        return res.get("result", {}).get("value")

    def goto(self, url, wait=2.5):
        self.call("Page.enable")
        self.call("Page.navigate", url=url)
        time.sleep(wait)
        for _ in range(40):
            state = self.eval("document.readyState")
            if state == "complete":
                break
            time.sleep(0.25)
        return self.eval("location.href")

    def text(self):
        return self.eval("document.body ? document.body.innerText.slice(0,4000) : ''") or ""

    def type(self, selector, value):
        ok = self.eval(
            "(()=>{const e=document.querySelector(%s);if(!e)return false;"
            "e.focus();e.select&&e.select();return true})()" % json.dumps(selector))
        if not ok:
            raise RuntimeError(f"selector not found: {selector}")
        self.call("Input.insertText", text=value)
        time.sleep(0.4)
        return self.eval("document.querySelector(%s).value" % json.dumps(selector))

    def click(self, selector):
        ok = self.eval(
            "(()=>{const e=document.querySelector(%s);if(!e)return false;"
            "e.scrollIntoView({block:'center'});e.click();return true})()" % json.dumps(selector))
        if not ok:
            raise RuntimeError(f"selector not found: {selector}")
        time.sleep(0.4)
        return True

    def set_files(self, selector, paths):
        doc = self.call("DOM.getDocument", depth=1)["root"]["nodeId"]
        node = self.call("DOM.querySelector", nodeId=doc, selector=selector)["nodeId"]
        if not node:
            raise RuntimeError(f"selector not found: {selector}")
        self.call("DOM.setFileInputFiles", files=paths, nodeId=node)
        return True

    def shot(self, path):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        import base64
        open(path, "wb").write(base64.b64decode(data))
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "url"
    c = CDP()
    try:
        if cmd == "open":
            print(c.goto(sys.argv[2]))
        elif cmd == "url":
            print(c.eval("location.href"))
        elif cmd == "eval":
            print(json.dumps(c.eval(sys.argv[2]), ensure_ascii=False)[:4000])
        elif cmd == "text":
            print(c.text())
        elif cmd == "click":
            print("clicked:", c.click(sys.argv[2]))
        elif cmd == "type":
            print("value:", c.type(sys.argv[2], sys.argv[3]))
        elif cmd == "shot":
            print(c.shot(sys.argv[2]))
        else:
            print(__doc__)
    finally:
        c.close()
