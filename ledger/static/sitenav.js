/* The one navigation bar of the whole site: the ledger pages and the holdings pages under /lab/ load this same file, so
   they look and behave like one program. To add or rename a page, change ITEMS below and nothing else. */
(function () {
  "use strict";
  var ITEMS = [
    { label: "持仓账本", href: "/", exact: true },
    { label: "持仓规则", href: "/lab/rules", lab: true },
    { label: "个股分析", href: "/lab/analyze", lab: true },
    { label: "AI 研报", href: "/lab/ai", lab: true },
    { label: "股票看板", href: "/dashboard" },
    { label: "读书清单", href: "/reading" },
    { label: "账号", href: "/account" },
  ];
  var path = location.pathname.replace(/\/index\.html$/, "/");
  var inLab = path.indexOf("/lab") === 0;

  function active(it) {
    return it.exact ? path === it.href : path === it.href || path.indexOf(it.href + "/") === 0;
  }

  var css = [
    "#sitenav{--sn-bg:#fff;--sn-ink:#3b3b3b;--sn-mute:#6b6b6b;--sn-line:#e4e4e0;--sn-accent:#1a56db;--sn-wash:#f3f3f0;",
    "position:sticky;top:0;z-index:50;display:flex;align-items:stretch;gap:2px;padding:0 12px;background:var(--sn-bg);",
    "border-bottom:1px solid var(--sn-line);font:14px/1.2 system-ui,-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;",
    "overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;scrollbar-width:none}",
    "#sitenav::-webkit-scrollbar{display:none}",
    "@media (prefers-color-scheme:dark){#sitenav{--sn-bg:#16181d;--sn-ink:#d6d8dc;--sn-mute:#9aa0a8;--sn-line:#2a2e36;--sn-accent:#7aa2ff;--sn-wash:#20242c}}",
    "#sitenav a,#sitenav button{display:inline-flex;align-items:center;padding:13px 12px;color:var(--sn-ink);text-decoration:none;",
    "border:0;background:none;font:inherit;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}",
    "#sitenav a:hover,#sitenav button:hover{background:var(--sn-wash)}",
    "#sitenav a[aria-current=page]{color:var(--sn-accent);font-weight:600;border-bottom-color:var(--sn-accent)}",
    "#sitenav a:focus-visible,#sitenav button:focus-visible{outline:2px solid var(--sn-accent);outline-offset:-2px}",
    "#sitenav .sn-brand{font-weight:700;padding-right:16px}",
    "#sitenav .sn-gap{flex:1 1 auto}",
    "#sitenav [hidden]{display:none}",
    "@media (max-width:640px){#sitenav{padding:0 6px}#sitenav a,#sitenav button{padding:12px 10px}#sitenav .sn-brand{display:none}}",
  ].join("");

  function build(showLab) {
    var nav = document.createElement("nav");
    nav.id = "sitenav";
    nav.setAttribute("aria-label", "主导航");
    var brand = document.createElement("a");
    brand.className = "sn-brand";
    brand.href = "/";
    brand.textContent = "测试";
    nav.appendChild(brand);
    ITEMS.forEach(function (it) {
      if (it.lab && !showLab) return;
      var a = document.createElement("a");
      a.href = it.href;
      a.textContent = it.label;
      if (active(it)) a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    });
    var gap = document.createElement("span");
    gap.className = "sn-gap";
    nav.appendChild(gap);
    var out = document.createElement("button");
    out.type = "button";
    out.textContent = "退出登录";
    out.addEventListener("click", function () {
      fetch("/api/logout", { method: "POST", credentials: "same-origin" }).finally(function () { location.href = "/"; });
    });
    nav.appendChild(out);
    return nav;
  }

  function mount(showLab) {
    var old = document.getElementById("sitenav");
    if (old) old.remove();
    var nav = build(showLab);
    document.body.insertBefore(nav, document.body.firstChild);
    var cur = nav.querySelector("[aria-current=page]");   // on a phone the bar scrolls: bring the current page into view
    if (cur) nav.scrollLeft = cur.offsetLeft - (nav.clientWidth - cur.offsetWidth) / 2;
  }

  function start() {
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    mount(inLab || !!window.__sitenavLab);
    if (inLab) return;
    // on the ledger pages the holdings pages are only listed for an account that may open them
    fetch("/api/auth", { credentials: "same-origin" }).then(function (r) { return r.json(); })
      .then(function (a) { if (a && a.platform) mount(true); }).catch(function () {});
  }

  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
