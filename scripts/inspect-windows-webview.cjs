"use strict";

// Read-only diagnostics for the installed app's WebView2; enabled only by the package-check workflow.
const fs = require("node:fs");
const path = require("node:path");
const folder = process.env.DIGUP_PACKAGE_REPORT_DIR;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let next = 0;

async function inspect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const waiting = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      waiting.get(message.id)?.(message);
      waiting.delete(message.id);
    } else if (["Runtime.exceptionThrown", "Log.entryAdded", "Runtime.consoleAPICalled"].includes(message.method)) {
      console.log(target.id, JSON.stringify(message));
    }
  });
  async function call(method, params = {}) {
    const id = ++next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        waiting.delete(id);
        reject(new Error(`${method} timed out`));
      }, 10000);
      waiting.set(id, (value) => { clearTimeout(timer); resolve(value); });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  try {
    await call("Runtime.enable");
    await call("Log.enable");
    const dom = await call("Runtime.evaluate", {
      expression: `JSON.stringify({url:location.href,title:document.title,language:document.documentElement.lang,
        text:document.body?.innerText.slice(0,3000),scripts:Array.from(document.scripts,s=>s.src),
        tauri:typeof window.__TAURI__,i18n:typeof I18n})`,
      returnByValue: true,
    });
    console.log("DOM", target.id, JSON.stringify(dom));
    const language = await call("Runtime.evaluate", {
      expression: `typeof I18n==="undefined"?"missing":I18n.ready.then(()=>({ready:true,language:I18n.language}))
        .catch(error=>({ready:false,error:String(error),stack:error.stack}))`,
      returnByValue: true,
      awaitPromise: true,
    });
    console.log("I18n", target.id, JSON.stringify(language));
    const shot = await call("Page.captureScreenshot");
    if (shot.result?.data && folder) {
      fs.writeFileSync(path.join(folder, `webview-${target.id}.png`), Buffer.from(shot.result.data, "base64"));
    }
  } finally {
    socket.close();
  }
}

(async () => {
  fs.mkdirSync(folder, { recursive: true });
  const deadline = Date.now() + 240000;
  let round = 0;
  while (Date.now() < deadline) {
    try {
      const targets = await fetch("http://127.0.0.1:9222/json/list", {
        signal: AbortSignal.timeout(3000),
      }).then((response) => response.json());
      console.log("TARGETS", JSON.stringify(targets.map(({ id, type, title, url }) => ({ id, type, title, url }))));
      for (const target of targets.filter((target) => target.type === "page" && target.webSocketDebuggerUrl)) {
        await inspect(target).catch((error) => console.error(target.id, String(error)));
      }
      if (++round >= 4) break;
      await pause(30000);
    } catch (error) {
      console.log("WebView not reachable:", String(error));
      await pause(2000);
    }
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
