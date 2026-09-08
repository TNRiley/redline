#!/usr/bin/env node
// Run the built page's script against a DOM shim and assert it actually rendered.
//
// A page that parses is not a page that runs. `node --check` proves the script is
// syntactically valid and proves nothing about whether it throws on the first line,
// and a page whose script dies still renders its static header -- so it looks fine in
// a screenshot and is empty underneath. This executes the real script against the
// real payload and fails the build if the page comes out blank.
//
//     node src/smoke.js ../index.html
//
// The shim is deliberately dumb: enough DOM for this page, nothing more. If the page
// grows something the shim cannot fake, widen the shim -- do not delete the check.

const fs = require("fs");
const path = require("process");

const file = process.argv[2] || "index.html";
const html = fs.readFileSync(file, "utf8");

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const payload = (html.match(/<script id="payload"[^>]*>([\s\S]*?)<\/script>/) || [])[1];
if (!payload) fail("no payload script found in " + file);
if (!scripts.length) fail("no inline script found in " + file);

function fail(msg) { console.error("SMOKE FAIL: " + msg); process.exit(1); }

// ---- the shim ---------------------------------------------------------------
let nodeCount = 0;
const listeners = [];

function makeEl(tag) {
  nodeCount++;
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [], className: "", id: "", style: {}, dataset: {},
    _text: "", innerHTML: "", value: "", selectedIndex: 0, options: [],
    hidden: false, title: "",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
      contains(c) { return this._s.has(c); },
    },
    get textContent() {
      return this._text + this.children.map(c => c.textContent).join("");
    },
    set textContent(v) { this._text = String(v); this.children = []; },
    append(...kids) { kids.forEach(k => this.children.push(typeof k === "object" ? k : makeText(k))); },
    appendChild(k) { this.children.push(k); return k; },
    remove() {},
    addEventListener(t, fn) { listeners.push([this, t, fn]); },
    dispatchEvent() { return true; },
    setPointerCapture() {}, releasePointerCapture() {},
    scrollIntoView() {},
    getBoundingClientRect: () => ({ x: 0, y: 0, top: 0, left: 0, width: 900, height: 150 }),
    insertAdjacentHTML(_, s) { this.innerHTML += s; },
    querySelector: () => null,
    querySelectorAll: () => [],
    // canvas
    width: 900, height: 150, clientWidth: 900, clientHeight: 150,
    getContext: () => ctx2d(),
  };
  return el;
}
function makeText(s) { return { textContent: String(s), children: [], tagName: "#text" }; }
function ctx2d() {
  const noop = () => {};
  return new Proxy({}, {
    get: (_, k) => (k === "measureText" ? () => ({ width: 10 }) : noop),
    set: () => true,
  });
}

const registry = new Map();
function reg(sel) {
  if (!registry.has(sel)) registry.set(sel, makeEl(sel.replace(/^[#.]/, "")));
  return registry.get(sel);
}

const document = {
  createElement: makeEl,
  querySelector: sel => reg(sel),
  querySelectorAll: () => [],
  getElementById: id => reg("#" + id),
  body: makeEl("body"),
  documentElement: makeEl("html"),
  addEventListener() {},
};
// The payload script tag has to return the real base64 or nothing else can run.
registry.set("#payload", Object.assign(makeEl("script"), { _text: payload }));

const window = {
  addEventListener() {}, devicePixelRatio: 1, scrollTo() {}, scrollY: 0,
  getComputedStyle: () => ({ getPropertyValue: () => "#000000" }),
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};

// ---- run it -----------------------------------------------------------------
const sandbox = {
  document, window, console, makeEl,
  atob: s => Buffer.from(s, "base64").toString("binary"),
  DecompressionStream, Blob, Response, TextDecoder, Uint8Array,
  devicePixelRatio: 1,
  addEventListener() {},
  getComputedStyle: window.getComputedStyle,
  PointerEvent: function () {},
  // Browser globals the page leans on. Option is the one that matters: filling the
  // agency <select> with `new Option(...)` throws without it, which aborts render()
  // immediately after the stat figures -- so the page looks half-built rather than
  // broken, which is exactly the failure this whole script exists to catch.
  Option: function (text, value) { return Object.assign(makeEl("option"), { text, value }); },
  Event: function (t) { return { type: t }; },
  setTimeout, clearTimeout, Promise, Math, JSON, Object, Array, Map, Set, Number, String,
};

const vm = require("vm");
const ctx = vm.createContext(sandbox);
let threw = null;
try {
  vm.runInContext(scripts[scripts.length - 1], ctx, { timeout: 60000 });
} catch (e) {
  threw = e;
}
if (threw) fail("script threw synchronously: " + threw);

// boot() is async, so give the microtask queue and the gzip stream a chance.
setTimeout(() => {
  if (sandbox.__smokeError) fail("boot() rejected: " + sandbox.__smokeError);
  const figs = registry.get("#figs");
  const list = registry.get("#list");
  const methods = registry.get("#methods");
  const agchart = registry.get("#agchart");
  const checks = [
    ["stat figures rendered", figs && figs.children.length >= 5],
    ["rule list rendered", list && list.children.length > 0],
    ["methods rendered", methods && methods.children.length >= 4],
    ["agency chart rendered", agchart && agchart.children.length > 0],
    ["no NaN in figures", figs && !/NaN|undefined/.test(figs.textContent)],
    ["no NaN in methods", methods && !/NaN|undefined/.test(methods.textContent)],
  ];
  const bad = checks.filter(c => !c[1]).map(c => c[0]);
  if (bad.length) fail("page ran but did not render: " + bad.join("; "));
  console.log("SMOKE OK — " + figs.children.length + " figures, "
    + list.children.length + " list rows, " + agchart.children.length + " agency rows, "
    + methods.children.length + " methods sections");
}, 8000);
