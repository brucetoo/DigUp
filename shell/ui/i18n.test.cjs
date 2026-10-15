const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, "zh-Hans.json"), "utf8"));
const source = fs.readFileSync(path.join(__dirname, "i18n.js"), "utf8");

test("packaged content policy permits reading the local translation catalog", () => {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, "../src-tauri/tauri.conf.json"), "utf8"));
  const connect = config.app.security.csp.split(";").find((directive) => directive.trim().startsWith("connect-src"));
  assert.ok(connect.split(/\s+/).includes("'self'"), "local catalog fetch must be allowed on Windows WebView2");
});

function windowContext(bus, saved = "zh-Hans", failSave = false) {
  const text = { nodeValue: " General ", parentElement: { closest: () => null }, isConnected: true };
  let walked = false;
  const document = {
    documentElement: {},
    createTreeWalker: () => ({
      nextNode: () => { if (walked) return false; walked = true; return true; },
      currentNode: text,
    }),
    querySelectorAll: () => [],
    createElement: (tag) => ({ tagName: tag, textContent: "", className: "" }),
  };
  const calls = [];
  const context = vm.createContext({
    console, document, NodeFilter: { SHOW_TEXT: 4 }, navigator: { platform: "Win32" },
    fetch: async () => ({ ok: true, json: async () => catalog }),
    window: { __TAURI__: {
      core: { invoke: async (command, args) => {
        calls.push([command, args]);
        if (command === "get_language") return bus.saved ?? saved;
        if (command === "set_language") {
          if (failSave) throw new Error("disk full");
          bus.saved = args.language;
          for (const listener of bus.listeners) listener({ payload: args.language });
        }
      }, convertFileSrc: (value) => value },
      path: { homeDir: async () => "C:\\Users\\test" },
      event: { listen: async (_, callback) => bus.listeners.push(callback) },
    } },
  });
  vm.runInContext(source, context);
  return { context, document, text, calls, run: (code) => vm.runInContext(code, context) };
}

test("Chinese default, persisted English, and cross-window language events", async () => {
  const bus = { listeners: [] };
  const first = windowContext(bus);
  const second = windowContext(bus);
  await Promise.all([first.run("I18n.ready"), second.run("I18n.ready")]);
  assert.equal(first.document.documentElement.lang, "zh-Hans");
  assert.equal(first.text.nodeValue, " 常规 ");
  assert.equal(first.run('t("Indexing 42% · 129 of 306")'), "索引进度 42% · 129 / 306");
  assert.equal(first.run('t("5 of 12 files searchable")'), "5 / 12 个文件可搜索");
  await first.run('I18n.setLanguage("en")');
  assert.equal(second.document.documentElement.lang, "en");
  assert.equal(second.text.nodeValue, " General ");
  assert.equal(second.run('t("Starting…")'), "Starting…");
  const reopened = windowContext(bus);
  await reopened.run("I18n.ready");
  assert.equal(reopened.document.documentElement.lang, "en");
});

test("failed persistence cannot change the language or notify peers", async () => {
  const bus = { listeners: [] };
  const first = windowContext(bus, "zh-Hans", true);
  await first.run("I18n.ready");
  await assert.rejects(first.run('I18n.setLanguage("en")'), /disk full/);
  assert.equal(first.document.documentElement.lang, "zh-Hans");
  assert.equal(bus.saved, undefined);
  assert.equal(first.run('I18n.normalize("fr")'), "zh-Hans");
});

test("result labels localize without changing filenames, paths, or excerpts", async () => {
  const app = windowContext({ listeners: [] });
  await app.run("I18n.ready");
  vm.runInContext(fs.readFileSync(path.join(__dirname, "results.js"), "utf8"), app.context);
  assert.equal(app.run('element("span", "name", "Open").textContent'), "Open");
  assert.equal(app.run('element("div", "title", "Documents").textContent'), "Documents");
  assert.equal(app.run('element("div", "line", "C:\\\\Documents").textContent'), "C:\\Documents");
  assert.equal(app.run('element("button", "", "Open").textContent'), "打开");
  assert.equal(app.run('reasonOf({segment: "frame", loc: 6, z: 3})'), "0:06 处的视频画面匹配");
  assert.equal(app.run('marked("Open «Documents»")'), "Open <b>Documents</b>");
  assert.equal(app.run('dateOf(0).includes("年")'), true);
});

test("catalog placeholders preserve each source value and static pages have translations", () => {
  for (const [english, chinese] of Object.entries(catalog)) {
    const tokens = (value) => [...value.matchAll(/\{\d+\}/g)].map((match) => match[0]).sort();
    assert.deepEqual(tokens(chinese), tokens(english), english);
  }
  const exempt = new Set(["DigUp", "English", "Space", "Esc", "Ctrl+↩", "EmbeddingGemma 2", "Google · Apache 2.0 · 865 MB"]);
  for (const file of ["index.html", "window.html", "settings.html", "setup.html"]) {
    const html = fs.readFileSync(path.join(__dirname, file), "utf8");
    for (const [, raw] of html.matchAll(/>([^<>]+)</g)) {
      const text = raw.trim().replace(/\s+/g, " ");
      if (!/[a-zA-Z]/.test(text) || exempt.has(text) || text.includes("<!")) continue;
      assert.ok(catalog[text], `${file}: missing static translation: ${text}`);
    }
    assert.match(html, /i18n\.js/);
    assert.match(html, /language-toggle/);
  }
});
