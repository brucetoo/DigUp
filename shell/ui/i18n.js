"use strict";

// English source messages are stable catalog keys. Result content and paths are never translated.
const I18n = (() => {
  let language = "zh-Hans";
  let catalog = {};
  let patterns = [];
  let revision = 0;
  const callbacks = new Set();
  const attributes = ["title", "placeholder", "aria-label"];
  const staticText = [];
  const staticAttributes = [];
  const call = (command, args) => window.__TAURI__.core.invoke(command, args);
  const normalize = (value) => value === "en" ? "en" : "zh-Hans";

  function text(english) {
    if (english == null || language === "en") return english;
    english = String(english);
    if (catalog[english]) return catalog[english];
    for (const [expression, translated] of patterns) {
      const match = expression.exec(english);
      if (match) return translated.replace(/\{(\d+)\}/g, (_, index) => match[Number(index) + 1]);
    }
    return english;
  }

  function render() {
    document.documentElement.lang = language;
    for (const [node, english, original] of staticText) if (node.isConnected) {
      node.nodeValue = language === "en" ? original
        : original.match(/^\s*/)[0] + text(english) + original.match(/\s*$/)[0];
    }
    for (const [node, name, english] of staticAttributes) if (node.isConnected) node.setAttribute(name, text(english));
    for (const callback of callbacks) callback(language);
    document.querySelectorAll(".language-toggle").forEach((button) => {
      button.textContent = language === "en" ? "简体中文" : "English";
      button.title = language === "en" ? "Switch to Simplified Chinese" : "切换为英语";
    });
  }

  function capture() {
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.parentElement.closest("script, style, code")) continue;
      const source = node.nodeValue.trim().replace(/\s+/g, " ");
      if (source) staticText.push([node, source, node.nodeValue]);
    }
    document.querySelectorAll("*").forEach((node) => {
      for (const name of attributes) {
        const source = node.getAttribute(name);
        if (source) staticAttributes.push([node, name, source]);
      }
    });
  }

  async function setLanguage(value) {
    // Rust persists before broadcasting; a failed save leaves the current language intact.
    await call("set_language", { language: normalize(value) });
    language = normalize(value);
    render();
  }

  capture();
  const ready = (async () => {
    catalog = await fetch("zh-Hans.json").then((response) => {
      if (!response.ok) throw new Error("Translation catalog unavailable");
      return response.json();
    });
    const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    patterns = Object.entries(catalog).filter(([key]) => key.includes("{0}"))
      .sort(([a], [b]) => b.replace(/\{\d+\}/g, "").length - a.replace(/\{\d+\}/g, "").length)
      .map(([key, translated]) => [new RegExp("^" + key.split(/\{\d+\}/).map(escape).join("(.*?)") + "$", "s"), translated]);
    await window.__TAURI__.event.listen("language-changed", ({ payload }) => {
      revision += 1;
      language = normalize(payload);
      render();
    });
    const before = revision;
    const saved = await call("get_language");
    if (revision === before) language = normalize(saved);
    document.querySelectorAll(".language-toggle").forEach((button) => {
      button.onclick = () => setLanguage(language === "en" ? "zh-Hans" : "en").catch(console.error);
    });
    render();
  })();

  return { text, ready, setLanguage, normalize, onChange: (callback) => {
    callbacks.add(callback);
    return () => callbacks.delete(callback);
  },
           get language() { return language; } };
})();
const t = (english) => I18n.text(english);
