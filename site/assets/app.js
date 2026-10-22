// Argus docs site behaviour: theme, tabs, copy buttons, TOC, step progress.
(function () {
  "use strict";
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) { /* private mode */ } }
  };

  // ---- theme ----
  var root = document.documentElement;
  function applyTheme(t) {
    root.setAttribute("data-theme", t);
    var b = document.getElementById("theme-btn");
    if (b) b.textContent = t === "dark" ? "☀" : "☾";
  }
  applyTheme(store.get("argus-docs-theme") ||
    (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(root.getAttribute("data-theme"));
    var themeBtn = document.getElementById("theme-btn");
    if (themeBtn) themeBtn.addEventListener("click", function () {
      var t = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      store.set("argus-docs-theme", t);
      applyTheme(t);
    });

    var menuBtn = document.getElementById("menu-btn");
    if (menuBtn) menuBtn.addEventListener("click", function () {
      document.body.classList.toggle("nav-open");
    });

    initTabs();
    initCodeBlocks();
    initToc();
    initSteps();
  });

  // ---- tabs: groups sharing data-group stay in sync and persist ----
  function initTabs() {
    var groups = document.querySelectorAll(".tabs");
    function select(group, key) {
      document.querySelectorAll('.tabs[data-group="' + group + '"]').forEach(function (tabs) {
        var has = tabs.querySelector('.tab-panel[data-key="' + key + '"]');
        if (!has) return;
        tabs.querySelectorAll(".tab-list button").forEach(function (b) {
          b.setAttribute("aria-selected", b.dataset.key === key ? "true" : "false");
        });
        tabs.querySelectorAll(".tab-panel").forEach(function (p) {
          p.classList.toggle("active", p.dataset.key === key);
        });
      });
    }
    groups.forEach(function (tabs) {
      var group = tabs.dataset.group || "";
      var list = document.createElement("div");
      list.className = "tab-list";
      list.setAttribute("role", "tablist");
      tabs.querySelectorAll(":scope > .tab-panel").forEach(function (p) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("role", "tab");
        b.dataset.key = p.dataset.key;
        b.textContent = p.dataset.label;
        b.addEventListener("click", function () {
          if (group) { store.set("argus-docs-tab-" + group, p.dataset.key); select(group, p.dataset.key); }
          else {
            list.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-selected", x === b ? "true" : "false"); });
            tabs.querySelectorAll(":scope > .tab-panel").forEach(function (x) { x.classList.toggle("active", x === p); });
          }
        });
        list.appendChild(b);
      });
      tabs.insertBefore(list, tabs.firstChild);
      var first = tabs.querySelector(":scope > .tab-panel");
      var recommended = first && first.dataset.key === "agent" ? "agent" : null;
      var key = (group && store.get("argus-docs-tab-" + group)) || recommended || guessOs(group) || (first && first.dataset.key);
      if (!tabs.querySelector(':scope > .tab-panel[data-key="' + key + '"]')) key = first && first.dataset.key;
      list.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-selected", b.dataset.key === key ? "true" : "false"); });
      tabs.querySelectorAll(":scope > .tab-panel").forEach(function (p) { p.classList.toggle("active", p.dataset.key === key); });
    });
  }
  function guessOs(group) {
    if (group !== "os") return null;
    var ua = navigator.userAgent || "";
    if (/Windows/i.test(ua)) return "windows";
    if (/Mac OS X|Macintosh/i.test(ua)) return "macos";
    if (/Linux/i.test(ua)) return "linux";
    return null;
  }

  // ---- code blocks: header + copy button (copies without prompts/comments) ----
  function initCodeBlocks() {
    document.querySelectorAll(".code").forEach(function (box) {
      var pre = box.querySelector("pre");
      if (!pre) return;
      var head = document.createElement("div");
      head.className = "code-head";
      var lang = document.createElement("span");
      lang.textContent = box.dataset.lang || "";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.textContent = "复制";
      btn.addEventListener("click", function () {
        var clone = pre.cloneNode(true);
        clone.querySelectorAll(".p, .c").forEach(function (n) { n.remove(); });
        var text = clone.textContent.replace(/[ \t]+$/gm, "").replace(/\n{3,}/g, "\n\n").trim() + "\n";
        var done = function () {
          btn.textContent = "已复制 ✓"; btn.classList.add("copied");
          setTimeout(function () { btn.textContent = "复制"; btn.classList.remove("copied"); }, 1600);
        };
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
        else fallback();
        function fallback() {
          var ta = document.createElement("textarea");
          ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
          document.body.appendChild(ta); ta.select();
          try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
          ta.remove();
        }
      });
      head.appendChild(lang);
      head.appendChild(btn);
      box.insertBefore(head, pre);
    });
  }

  // ---- right-hand TOC + heading anchors + scrollspy ----
  function initToc() {
    var toc = document.querySelector(".toc");
    var heads = document.querySelectorAll(".article h2[id], .article h3[id]");
    heads.forEach(function (h) {
      var a = document.createElement("a");
      a.className = "anchor"; a.href = "#" + h.id; a.textContent = "#";
      a.setAttribute("aria-label", "链接到本节");
      h.appendChild(a);
    });
    if (!toc) return;
    var title = document.createElement("div");
    title.className = "toc-title"; title.textContent = "本页目录";
    toc.appendChild(title);
    var links = [];
    heads.forEach(function (h) {
      var a = document.createElement("a");
      a.href = "#" + h.id;
      a.className = h.tagName.toLowerCase();
      a.textContent = h.dataset.toc || h.textContent.replace(/#$/, "").trim();
      toc.appendChild(a);
      links.push([h, a]);
    });
    function spy() {
      var cur = null;
      links.forEach(function (pair) {
        if (pair[0].getBoundingClientRect().top < 160) cur = pair[1];
      });
      links.forEach(function (pair) { pair[1].classList.toggle("active", pair[1] === cur); });
    }
    window.addEventListener("scroll", spy, { passive: true });
    spy();
  }

  // ---- step checklist with persisted progress ----
  function initSteps() {
    var steps = document.querySelectorAll(".step[data-step]");
    if (!steps.length) return;
    var page = document.body.dataset.page || "page";
    var bar = document.querySelector(".progress .bar i");
    var label = document.querySelector(".progress .count");
    function key(s) { return "argus-docs-step-" + page + "-" + s.dataset.step; }
    function refresh() {
      var n = 0;
      steps.forEach(function (s) {
        var on = store.get(key(s)) === "1";
        s.classList.toggle("done", on);
        var box = s.querySelector(".step-check input");
        if (box) box.checked = on;
        if (on) n++;
      });
      if (bar) bar.style.width = (100 * n / steps.length) + "%";
      if (label) label.textContent = n === steps.length
        ? "全部完成 🎉 你已经跑通了 Argus"
        : "已完成 " + n + " / " + steps.length + " 步";
    }
    steps.forEach(function (s) {
      var box = s.querySelector(".step-check input");
      if (!box) return;
      box.addEventListener("change", function () {
        if (box.checked) store.set(key(s), "1"); else store.del(key(s));
        refresh();
        if (box.checked) {
          var next = s.nextElementSibling;
          while (next && !(next.classList && next.classList.contains("step"))) next = next.nextElementSibling;
          if (next) setTimeout(function () { next.scrollIntoView({ behavior: "smooth", block: "start" }); }, 250);
        }
      });
    });
    var reset = document.querySelector(".progress .reset");
    if (reset) reset.addEventListener("click", function () {
      steps.forEach(function (s) { store.del(key(s)); });
      refresh();
    });
    refresh();
  }
})();
