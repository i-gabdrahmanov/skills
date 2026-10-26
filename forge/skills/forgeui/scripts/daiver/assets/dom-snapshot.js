/* dom-snapshot.js — снимок страницы для verify (спека §14.4.1). Исполняется ВНУТРИ страницы.
 *
 * Файл — одно выражение: функция без аргументов, возвращающая объект с методами. Хозяин (Python,
 * daiver/verify/capture.py) вызывает его так:  (<этот файл>)().snapshot({...})  и получает ТЕКСТ JSON
 * (CDP не носит глубоко вложенные графы по значению — отдаём строку).
 *
 * Что здесь решено, и почему так:
 *   • Снимок — ЕДИНСТВЕННЫЙ источник измеренного: у каждого узла box в координатах СТРАНИЦЫ (с учётом
 *     прокрутки), size — только числа (никаких fill/hug), цвета — `#RRGGBB[AA]` (нераспознанное — как
 *     есть), отсутствующая группа = дефолт (нулевые padding/gap/radius, нет тени, opacity 1 — ключ
 *     опущен, а не «неизвестно»).
 *   • id узла — путь по ЭЛЕМЕНТАМ-детям документа (`dom:0.1.2`), поэтому скрытый сосед ничего не
 *     сдвигает; текстовый кусок — `<путь элемента>#<номер дочернего узла>`.
 *   • stamps (`data-daiver-id`) лежат ВНЕ дерева — в `stamps: {id → значение}`: сопоставитель их не
 *     читает (иначе тесты с оракулом ничего не доказывали бы); их читает только отчёт.
 *   • svg не обходится внутрь (макет держит его одним узлом); пустая обёртка (нет ни содержимого, ни
 *     краски) выбрасывается, обёртка с видимым содержимым остаётся, даже если сама схлопнулась.
 *   • Компонент — из React-fiber: `components` = имена от внешнего к внутреннему для компонентов, у
 *     которых ЭТОТ элемент — корень. Минифицированное (≤ 2 символов) имя компонентом не считается;
 *     `react.found:false` = имён нет вовсе (прод-сборка или не React): гейт идентичности НЕ исполнялся.
 *   • `line-height: normal` не угадывается 1.2, а меряется (проба с теми же шрифтовыми свойствами).
 *   • Пароль читается как есть: это проверка вёрстки, а не утилита чтения секретов — но значение попадает
 *     в файл, поэтому хозяин не выкладывает снимок наружу (pii-boundary его всё равно охраняет).
 *   • Капы: узлы (≈8000: узлы-предки, закрываемые после детей, могут выйти за него на глубину дерева), глубина (элемент на
 *     границе остаётся пустым узлом с `cut: true`, чтобы цепочка предков не пропала как «пустая обёртка»), длина текста;
 *     сверх капа — флаг в `truncated`, не молчаливая обрезка.
 *   • Пустой корень — пустой документ (`root: null`), не исключение; NaN/Infinity невозможны (нет бокса —
 *     нули).
 *
 * Тот же файл несёт механику готовности (`install`/`poll`) и вспомогательные операции рецепта
 * (`locate`, `focus`, `selectOption`, `scrollTo`, `gone`) и проверку шрифтов (`fonts`).
 */
(function () {
  "use strict";

  var VERSION = 1;
  var MAX_NODES = 8000;       // узлов в дереве
  var MAX_VISITED = 20000;    // элементов, которые мы вообще трогаем
  var MAX_DEPTH = 120;
  var MAX_TEXT = 2000;
  var MAX_CHAIN = 6;
  var SKIP = { script: 1, style: 1, link: 1, meta: 1, title: 1, noscript: 1, template: 1, head: 1, base: 1, datalist: 1 };
  var FORM_LEAF = { input: 1, textarea: 1, select: 1 };
  var REPLACED = { img: 1, canvas: 1, video: 1, audio: 1, object: 1, embed: 1, iframe: 1 };
  var HOST_TAGS = { 3: 1, 4: 1, 5: 1, 6: 1, 26: 1, 27: 1 };         // HostRoot, Portal, HostComponent, HostText, Hoistable, Singleton
  var COMPONENT_TAGS = { 0: 1, 1: 1, 2: 1, 11: 1, 15: 1 };          // Function, Class, Indeterminate, ForwardRef, SimpleMemo
  var LOADER_WORDS = { loader: 1, loading: 1, spinner: 1, skeleton: 1, shimmer: 1, throbber: 1, preloader: 1, busy: 1 };

  // ── числа ──────────────────────────────────────────────────────────────────
  function fin(v, d) { v = +v; return isFinite(v) ? v : (d === undefined ? 0 : d); }
  function r2(v) { return Math.round(fin(v) * 100) / 100; }

  // ── цвета ──────────────────────────────────────────────────────────────────
  function hex2(n) { var s = Math.max(0, Math.min(255, Math.round(n))).toString(16).toUpperCase(); return s.length < 2 ? "0" + s : s; }
  var RGB_RE = /^rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)$/i;
  var COLOR_FN_RE = /^color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+%?)\s*)?\)$/i;
  function alphaOf(raw) {
    if (raw === undefined || raw === null || raw === "") return 1;
    var a = /%$/.test(raw) ? parseFloat(raw) / 100 : parseFloat(raw);
    return isFinite(a) ? Math.max(0, Math.min(1, a)) : 1;
  }
  /** → {hex, alpha, raw}: hex — `#RRGGBB[AA]` либо исходная строка, если формат не узнан. */
  function color(str) {
    str = String(str === undefined || str === null ? "" : str).trim();
    if (!str) return { hex: "", alpha: 0, raw: true };
    if (str.toLowerCase() === "transparent") return { hex: "#00000000", alpha: 0, raw: false };
    var m = RGB_RE.exec(str), k = 1;
    if (!m) { m = COLOR_FN_RE.exec(str); k = 255; }
    if (!m) return { hex: str, alpha: 1, raw: true };
    var a = alphaOf(m[4]);
    var hex = "#" + hex2(m[1] * k) + hex2(m[2] * k) + hex2(m[3] * k);
    if (a < 1) hex += hex2(a * 255);
    return { hex: hex, alpha: a, raw: false };
  }
  /** Все rgb()/rgba()/color(srgb) внутри строки (градиент, тень) → hex; остальное не трогаем. */
  function colorsIn(str) {
    return String(str).replace(/rgba?\([^)]*\)|color\(\s*srgb[^)]*\)/gi, function (m) { return color(m).hex; });
  }

  // ── шрифты, строка высоты ──────────────────────────────────────────────────
  function firstFamily(list) {
    var s = String(list || "").trim(), out = "", q = "";
    for (var i = 0; i < s.length; i++) {
      var c = s.charAt(i);
      if (q) { if (c === q) q = ""; else out += c; continue; }
      if (c === '"' || c === "'") { q = c; continue; }
      if (c === ",") break;
      out += c;
    }
    return out.trim();
  }
  var lhCache = {};
  function measuredLineHeight(cs) {
    var key = [cs.fontFamily, cs.fontSize, cs.fontWeight, cs.fontStyle, cs.fontStretch, cs.fontVariant].join("|");
    if (lhCache.hasOwnProperty(key)) return lhCache[key];
    var host = document.body || document.documentElement, probe = null, out = 0;
    try {
      probe = document.createElement("span");
      probe.style.cssText = "position:absolute;left:-99999px;top:0;visibility:hidden;white-space:nowrap;margin:0;padding:0;border:0;" +
        "line-height:normal;letter-spacing:normal;text-transform:none";
      probe.style.fontFamily = cs.fontFamily; probe.style.fontSize = cs.fontSize; probe.style.fontWeight = cs.fontWeight;
      probe.style.fontStyle = cs.fontStyle; probe.style.fontStretch = cs.fontStretch;
      probe.textContent = "Hg";
      host.appendChild(probe);
      out = probe.getBoundingClientRect().height;
    } catch (e) { out = 0; }
    finally { try { if (probe && probe.parentNode) probe.parentNode.removeChild(probe); } catch (e2) { /* ничего */ } }
    if (!(out > 0)) out = fin(parseFloat(cs.fontSize)) * 1.2;      // проба не удалась: честный запасной вариант, но это уже оценка
    lhCache[key] = r2(out);
    return lhCache[key];
  }
  function lineHeightPx(cs) {
    if (cs.lineHeight === "normal") return measuredLineHeight(cs);
    var v = parseFloat(cs.lineHeight);
    return isFinite(v) ? r2(v) : measuredLineHeight(cs);
  }

  // ── стили ──────────────────────────────────────────────────────────────────
  function px(v) { return r2(parseFloat(v)); }
  function radiusOf(cs, w, h) {
    var cap = Math.min(w, h) / 2;                               // браузер рисует не больше половины стороны (9999px = «таблетка»)
    var names = ["borderTopLeftRadius", "borderTopRightRadius", "borderBottomRightRadius", "borderBottomLeftRadius"], out = [];
    for (var i = 0; i < 4; i++) {
      var first = String(cs[names[i]] || "0").split(/\s+/)[0], num = parseFloat(first);
      if (!isFinite(num)) num = 0;
      if (/%$/.test(first)) num = (num / 100) * Math.min(w, h);          // «50%» — круг/таблетка; эллиптические углы не различаем
      out.push(r2(Math.max(0, Math.min(num, cap > 0 ? cap : num))));
    }
    if (!out[0] && !out[1] && !out[2] && !out[3]) return null;
    return (out[0] === out[1] && out[1] === out[2] && out[2] === out[3]) ? out[0] : out;
  }
  function splitTop(str) {
    var parts = [], depth = 0, cur = "";
    for (var i = 0; i < str.length; i++) {
      var c = str.charAt(i);
      if (c === "(") depth++;
      else if (c === ")") depth--;
      if (c === "," && depth === 0) { parts.push(cur); cur = ""; } else cur += c;
    }
    if (cur.trim()) parts.push(cur);
    return parts;
  }
  function shadowOf(cs) {
    var raw = cs.boxShadow;
    if (!raw || raw === "none") return null;
    var out = [];
    splitTop(raw).forEach(function (part) {
      part = part.trim();
      var cm = /rgba?\([^)]*\)|color\([^)]*\)|#[0-9a-f]{3,8}\b/i.exec(part), col = cm ? color(cm[0]).hex : "";
      var rest = cm ? part.replace(cm[0], " ") : part, inset = /\binset\b/i.test(rest);
      var nums = (rest.replace(/\binset\b/i, " ").match(/-?[\d.]+(?:px)?/g) || []).map(function (t) { return r2(parseFloat(t)); });
      if (nums.length < 2) return;
      out.push({ x: nums[0], y: nums[1], blur: nums[2] || 0, spread: nums[3] || 0, color: col, inset: inset });
    });
    return out.length ? out : null;
  }
  function borderOf(cs) {
    var sides = ["Top", "Right", "Bottom", "Left"], w = {}, shown = null;
    sides.forEach(function (s) {
      var st = cs["border" + s + "Style"], width = (st === "none" || st === "hidden") ? 0 : px(cs["border" + s + "Width"]);
      w[s.toLowerCase()] = width;
      if (width > 0 && !shown) shown = { color: color(cs["border" + s + "Color"]), style: st };
    });
    if (!shown) return null;
    var same = w.top === w.right && w.right === w.bottom && w.bottom === w.left;
    var out = { width: same ? w.top : w };
    if (shown.color.alpha > 0) out.color = shown.color.hex;
    if (shown.style && shown.style !== "solid") out.style = shown.style;
    return out;
  }
  /** Краска элемента: {style-группа, есть ли краска вообще, фоновое изображение}. Дефолты опущены. */
  function styleOf(cs, w, h) {
    var st = {}, paint = false, bgImage = false;
    var bg = color(cs.backgroundColor);
    if (bg.alpha > 0) { st.background = bg.hex; paint = true; }
    var bi = cs.backgroundImage;
    if (bi && bi !== "none") {
      if (/gradient\(/i.test(bi)) { st.gradient = colorsIn(bi); paint = true; } else { bgImage = true; paint = true; }
    }
    var rad = radiusOf(cs, w, h); if (rad !== null) st.radius = rad;
    var br = borderOf(cs); if (br) { st.border = br; paint = true; }
    var sh = shadowOf(cs); if (sh) { st.shadow = sh; paint = true; }
    var op = fin(cs.opacity, 1); if (op < 1) st.opacity = Math.round(op * 1000) / 1000;
    return { style: Object.keys(st).length ? st : null, paint: paint, bgImage: bgImage };
  }
  function fontOf(cs) {
    var f = { family: firstFamily(cs.fontFamily), size: px(cs.fontSize), weight: Math.round(fin(cs.fontWeight, 400)) || 400,
              lineHeight: lineHeightPx(cs) };
    var ta = cs.textAlign;
    if (ta === "start") ta = "left"; else if (ta === "end") ta = "right";
    if (ta === "left" || ta === "center" || ta === "right" || ta === "justify") f.align = ta;
    var ls = parseFloat(cs.letterSpacing);
    if (isFinite(ls) && ls !== 0) f.letterSpacing = r2(ls);
    if (cs.fontStyle === "italic" || cs.fontStyle === "oblique") f.italic = true;
    var tt = cs.textTransform;
    if (tt === "uppercase" || tt === "lowercase" || tt === "capitalize") f.textTransform = tt;
    if (!f.family) delete f.family;
    return f;
  }

  // ── раскладка ──────────────────────────────────────────────────────────────
  function paddingOf(cs) {
    var p = { top: px(cs.paddingTop), right: px(cs.paddingRight), bottom: px(cs.paddingBottom), left: px(cs.paddingLeft) };
    return (p.top || p.right || p.bottom || p.left) ? p : null;
  }
  var JUSTIFY = { "normal": null, "flex-start": "start", "start": "start", "left": "start", "center": "center", "flex-end": "end",
                  "end": "end", "right": "end", "space-between": "space-between" };
  var ALIGN = { "normal": null, "stretch": null, "flex-start": "start", "start": "start", "self-start": "start", "center": "center",
                "flex-end": "end", "end": "end", "self-end": "end", "baseline": "baseline" };
  function gapPx(v) { var n = parseFloat(v); return isFinite(n) ? r2(n) : 0; }
  /** Заявленная раскладка (flex/grid) — из computed; для блочной — пусто (выводится по боксам детей позже). */
  function statedLayout(cs) {
    var d = cs.display || "";
    var flex = d === "flex" || d === "inline-flex", grid = d === "grid" || d === "inline-grid";
    if (!flex && !grid) return null;
    var lay = { stated: true }, pad = paddingOf(cs);
    if (flex) {
      var col = /^column/.test(cs.flexDirection);
      lay.direction = col ? "column" : "row";
      var g = col ? gapPx(cs.rowGap) : gapPx(cs.columnGap), cg = col ? gapPx(cs.columnGap) : gapPx(cs.rowGap);
      if (g) lay.gap = g;
      lay.wrap = cs.flexWrap === "wrap" || cs.flexWrap === "wrap-reverse";
      if (lay.wrap && cg) lay.crossGap = cg;
      if (!lay.wrap) delete lay.wrap;
    } else {
      lay.direction = "row"; lay.wrap = true;
      var cg2 = gapPx(cs.columnGap), rg = gapPx(cs.rowGap);
      if (cg2) lay.gap = cg2;
      if (rg) lay.crossGap = rg;
    }
    if (pad) lay.padding = pad;
    var al = {}, jm = JUSTIFY[cs.justifyContent], ac = ALIGN[cs.alignItems];
    if (jm) al.main = jm;
    if (ac) al.cross = ac;
    if (al.main || al.cross) lay.align = al;
    if (grid) {
      var cols = String(cs.gridTemplateColumns || "").trim();
      var n = (cols && cols !== "none") ? cols.split(/\s+(?![^(]*\))/).length : 0;
      lay._grid = n;
    }
    return lay;
  }
  /** Блочная раскладка по боксам детей: направление и равномерный зазор. Помечена inferred. */
  function inferredLayout(kids, cs) {
    var lay = { inferred: true }, pad = paddingOf(cs);
    var i, stacked = true, rowed = true;
    for (i = 1; i < kids.length; i++) {
      var a = kids[i - 1].box, b = kids[i].box;
      if (!(b.y >= a.y + a.h - 1)) stacked = false;
      if (!(b.x >= a.x + a.w - 1)) rowed = false;
    }
    lay.direction = (kids.length > 1 && !stacked && rowed) ? "row" : "column";
    if (kids.length > 1 && (stacked || rowed)) {
      var gaps = [];
      for (i = 1; i < kids.length; i++) {
        var p = kids[i - 1].box, q = kids[i].box;
        gaps.push(lay.direction === "row" ? q.x - (p.x + p.w) : q.y - (p.y + p.h));
      }
      var mn = Math.min.apply(null, gaps), mx = Math.max.apply(null, gaps);
      if (mx - mn <= 0.5 && mn > 0) lay.gap = r2(mn);
    }
    if (pad) lay.padding = pad;
    return lay;
  }

  // ── React fiber ────────────────────────────────────────────────────────────
  function fiberKey(el) {
    var keys = Object.keys(el);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf("__reactFiber$") === 0 || keys[i].indexOf("__reactInternalInstance$") === 0) return keys[i];
    }
    return null;
  }
  function fiberName(f) {
    var t = f.type, n;
    if (!t) return undefined;
    if (f.tag === 11) n = t.displayName || (t.render && (t.render.displayName || t.render.name));
    else n = t.displayName || t.name;
    return typeof n === "string" ? n : undefined;
  }
  function isMinified(name) { return name.length <= 2; }
  /** → {names:[внешний … внутренний], minified:n, fiber:bool} для компонентов, чей корневой хост — этот элемент. */
  function componentsOf(el, ctx) {
    var key = fiberKey(el);
    if (!key) return null;
    ctx.react.fibers++;
    var f = el[key], names = [], p = f && f.return, guard = 0;
    while (p && guard++ < 400 && !HOST_TAGS[p.tag]) {
      if (COMPONENT_TAGS[p.tag]) {
        var n = fiberName(p);
        if (n) {
          if (isMinified(n)) ctx.react.minified++;
          else if (names[names.length - 1] !== n) { names.push(n); ctx.react.named++; }
        }
      }
      p = p.return;
    }
    names.reverse();
    return names.length ? names.slice(0, MAX_CHAIN) : null;
  }

  // ── текст ──────────────────────────────────────────────────────────────────
  function cleanText(raw, cs) {
    var ws = cs.whiteSpace || "normal";
    if (ws === "pre" || ws === "pre-wrap" || ws === "break-spaces") return raw;
    if (ws === "pre-line") return raw.replace(/[ \t\f\r]+/g, " ").replace(/ ?\n ?/g, "\n").trim();
    return raw.replace(/[ \t\n\r\f]+/g, " ").trim();
  }
  function capText(s, ctx) {
    if (s.length > MAX_TEXT) { ctx.truncated.text++; return s.slice(0, MAX_TEXT); }
    return s;
  }
  function runBox(node, sx, sy) {
    var range = document.createRange(), x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity, any = false;
    range.selectNodeContents(node);
    var rects = range.getClientRects();
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      if (!(r.width > 0 || r.height > 0)) continue;
      any = true;
      x1 = Math.min(x1, r.left); y1 = Math.min(y1, r.top); x2 = Math.max(x2, r.right); y2 = Math.max(y2, r.bottom);
    }
    if (!any) return null;
    return { x: r2(x1 + sx), y: r2(y1 + sy), w: r2(x2 - x1), h: r2(y2 - y1) };
  }

  // ── узлы ───────────────────────────────────────────────────────────────────
  function elementBox(el, sx, sy) {
    var r = el.getBoundingClientRect();
    return { x: r2(r.left + sx), y: r2(r.top + sy), w: r2(Math.max(0, r.width)), h: r2(Math.max(0, r.height)) };
  }
  function nameOf(el) {
    var n = el.localName;
    if (el.id && /^[\w-]{1,40}$/.test(el.id)) return n + "#" + el.id;
    return n;
  }
  function attrsOf(el) {
    var a = {}, v;
    if ((v = el.getAttribute("role"))) a.role = v;
    if ((v = el.getAttribute("aria-label"))) a.ariaLabel = v.slice(0, 120);
    if ((v = el.getAttribute("data-testid") || el.getAttribute("data-test-id") || el.getAttribute("data-qa"))) a.testid = v.slice(0, 120);
    if ((v = el.getAttribute("alt"))) a.alt = v.slice(0, 120);
    if ((v = el.getAttribute("placeholder"))) a.placeholder = v.slice(0, 120);
    if ((v = el.getAttribute("type")) && FORM_LEAF[el.localName]) a.type = v;
    if (el.disabled === true) a.disabled = true;
    if (el.checked === true) a.checked = true;
    return Object.keys(a).length ? a : null;
  }
  function formText(el) {
    var t = el.localName, type = (el.getAttribute("type") || "text").toLowerCase();
    if (t === "select") {
      var o = el.options && el.options[el.selectedIndex];
      return o ? String(o.text || "").trim() : "";
    }
    if (t === "input" && (type === "checkbox" || type === "radio" || type === "range" || type === "file" || type === "image")) return "";
    return String(el.value === undefined || el.value === null ? "" : el.value);
  }

  function build(sx, sy) {
    return function (rootEl, opts) {
      var ctx = {
        count: 0, visited: 0, stamps: {}, bgImages: 0, hiddenText: 0,
        truncated: { nodes: false, depth: false, text: 0 },
        react: { fibers: 0, named: 0, minified: 0 }, families: {}
      };

      function elementIndexPath(el) {
        var idx = [], cur = el;
        while (cur && cur !== document.documentElement && cur.parentElement) {
          idx.push(Array.prototype.indexOf.call(cur.parentElement.children, cur));
          cur = cur.parentElement;
        }
        idx.push(0);                                   // <html> — единственный элемент-корень документа
        return idx.reverse().join(".");
      }
      function stamp(el, id) {
        var s = el.getAttribute && el.getAttribute("data-daiver-id");
        if (s) ctx.stamps[id] = s;
      }
      function mk(id, kind, el, box) {
        ctx.count++;
        var n = { id: "dom:" + id, kind: kind, tag: el.localName, name: nameOf(el), box: box, size: { w: box.w, h: box.h } };
        return n;
      }

      function textNodeFor(run, parentCs, parentPath, childIdx, hidden) {
        if (hidden) return null;
        var text = cleanText(run.data, parentCs);
        if (!text) return null;
        var box = runBox(run, sx, sy);
        if (!box) { ctx.hiddenText++; return null; }
        if (ctx.count >= MAX_NODES) { ctx.truncated.nodes = true; return null; }
        ctx.count++;
        var f = fontOf(parentCs);
        if (f.family) ctx.families[f.family] = (ctx.families[f.family] || 0) + text.length;
        var n = { id: "dom:" + parentPath + "#" + childIdx, kind: "text", tag: "#text", box: box, size: { w: box.w, h: box.h },
                  text: capText(text, ctx), font: f, color: color(parentCs.color).hex };
        return n;
      }

      function visit(el, path, depth) {
        if (ctx.visited >= MAX_VISITED) { ctx.truncated.nodes = true; return null; }
        ctx.visited++;
        var tag = el.localName;
        if (SKIP[tag]) return null;
        var cs = getComputedStyle(el);
        if (cs.display === "none") return null;
        if (ctx.count >= MAX_NODES) { ctx.truncated.nodes = true; return null; }
        var hidden = cs.visibility !== "visible";
        var box = elementBox(el, sx, sy);
        var comps = componentsOf(el, ctx);
        var node;

        // svg — одной нодой, внутрь не идём
        if (tag === "svg" && el.namespaceURI === "http://www.w3.org/2000/svg") {
          if (hidden || (!box.w && !box.h)) return null;
          node = mk(path, "vector", el, box);
          var sv = styleOf(cs, box.w, box.h); if (sv.style && sv.style.opacity !== undefined) node.style = { opacity: sv.style.opacity };
          if (comps) node.components = comps;
          stamp(el, node.id);
          return node;
        }
        if (el.namespaceURI === "http://www.w3.org/2000/svg") return null;   // потомки svg сюда не попадают; чужой svg-фрагмент — тоже

        if (REPLACED[tag]) {
          if (hidden || (!box.w && !box.h)) return null;
          node = mk(path, tag === "iframe" ? "frame" : "image", el, box);
          var sr = styleOf(cs, box.w, box.h); if (sr.style) node.style = sr.style;
          var ar = attrsOf(el); if (ar) node.attrs = ar;
          if (comps) node.components = comps;
          stamp(el, node.id);
          return node;
        }

        var st = hidden ? { style: null, paint: false, bgImage: false } : styleOf(cs, box.w, box.h);

        if (FORM_LEAF[tag]) {
          if (hidden && !box.w && !box.h) return null;
          node = mk(path, "frame", el, box);
          if (st.style) node.style = st.style;
          var ft = capText(formText(el), ctx);
          if (ft) { node.text = ft; node.font = fontOf(cs); node.color = color(cs.color).hex; }
          var af = attrsOf(el); if (af) node.attrs = af;
          if (comps) node.components = comps;
          stamp(el, node.id);
          return node;
        }

        // обычный контейнер: дети — элементы и куски текста
        var kids = [], cn = el.childNodes, rawElems = 0, runs = 0, i;
        if (depth >= MAX_DEPTH) {
          ctx.truncated.depth = true;
        } else {
          for (i = 0; i < cn.length; i++) {
            var c = cn[i];
            if (c.nodeType === 1) {
              rawElems++;
              var elIdx = Array.prototype.indexOf.call(el.children, c);
              var k = visit(c, path + "." + elIdx, depth + 1);
              if (k) kids.push(k);
            } else if (c.nodeType === 3) {
              var tn = textNodeFor(c, cs, path, i, hidden);
              if (tn) { kids.push(tn); runs++; }
            }
          }
        }
        var elemKids = kids.length - runs;

        // лист-текст: единственный кусок текста, ни одного элемента-ребёнка и своей краски нет → сам узел и есть текст
        if (elemKids === 0 && runs === 1 && !st.paint && !hidden) {
          var run = kids[0];
          ctx.count--;                                        // кусок текста свернулся в узел элемента: mk() посчитает его заново
          node = mk(path, "text", el, box);
          node.text = run.text; node.font = run.font; node.color = run.color;
          if (st.style && st.style.opacity !== undefined) node.style = { opacity: st.style.opacity };
          var at = attrsOf(el); if (at) node.attrs = at;
          if (comps) node.components = comps;
          node.textBox = run.box;
          stamp(el, node.id);
          return node;
        }

        // пустая обёртка (ни детей, ни краски) выбрасывается; обёртка с видимым содержимым остаётся, даже схлопнувшись
        var capped = depth >= MAX_DEPTH && kids.length === 0;      // обрезано по глубине: цепочка предков не должна исчезнуть как «пустая»
        if (kids.length === 0 && !st.paint && !capped) return null;

        node = mk(path, kids.length === 0 ? "rect" : "frame", el, box);
        if (capped && !st.paint) node.cut = true;
        if (st.style) node.style = st.style;
        if (st.bgImage) { node.bgImage = true; ctx.bgImages++; }
        if (kids.length) {
          var lay = statedLayout(cs);
          if (lay) {
            if (lay._grid) { node.layoutHints = { grid: { confidence: "high", columns: lay._grid } }; }
            delete lay._grid;
            node.layout = lay;
          } else {
            var il = inferredLayout(kids, cs);
            node.layout = il;
          }
          node.children = kids;
        }
        var an = attrsOf(el); if (an) node.attrs = an;
        if (comps) node.components = comps;
        if (cs.overflowX !== "visible" || cs.overflowY !== "visible") {
          if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) node.scrolls = true;
        } else if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
          node.overflowing = true;
        }
        stamp(el, node.id);
        return node;
      }

      var root = null;
      if (rootEl) {
        var tree = visit(rootEl, elementIndexPath(rootEl), 0);
        root = tree;
      }
      return { root: root, ctx: ctx };
    };
  }

  // ── публичное: snapshot ────────────────────────────────────────────────────
  function snapshot(opts) {
    opts = opts || {};
    var rootEl = null, rootMissing = false;
    if (opts.rootSelector) {
      try { rootEl = document.querySelector(opts.rootSelector); } catch (e) { rootEl = null; }
      if (!rootEl) rootMissing = true;
    } else {
      rootEl = document.body;
    }
    var sx = fin(window.pageXOffset), sy = fin(window.pageYOffset);
    var built;
    mut.muted = true;                                  // проба line-height временно кладёт элемент в документ — это не «DOM меняется»
    try { built = build(sx, sy)(rootEl, opts); }
    finally { if (mut.observer) mut.observer.takeRecords(); mut.muted = false; }
    var ctx = built.ctx;
    var de = document.documentElement;
    var react = { found: ctx.react.named > 0, fibers: ctx.react.fibers, named: ctx.react.named, minified: ctx.react.minified };
    var fams = Object.keys(ctx.families).sort(function (a, b) { return ctx.families[b] - ctx.families[a] || (a < b ? -1 : 1); });
    var out = {
      v: VERSION,
      url: String(location.href).slice(0, 2000),
      title: String(document.title || "").slice(0, 200),
      viewport: { w: fin(window.innerWidth), h: fin(window.innerHeight) },
      page: { w: r2(Math.max(fin(de && de.scrollWidth), fin(window.innerWidth))), h: r2(Math.max(fin(de && de.scrollHeight), fin(window.innerHeight))),
              scrollX: r2(sx), scrollY: r2(sy) },
      elements: ctx.count,
      domElements: document.getElementsByTagName("*").length,
      truncated: ctx.truncated,
      react: react,
      fonts: { used: fams.slice(0, 12), status: document.fonts ? String(document.fonts.status) : "unsupported" },
      stamps: ctx.stamps,
      bgImages: ctx.bgImages,
      hiddenText: ctx.hiddenText,
      rootMissing: rootMissing,
      root: built.root
    };
    return JSON.stringify(out);
  }

  // ── готовность ─────────────────────────────────────────────────────────────
  var mut = { count: 0, last: 0, observer: null, muted: false };
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function install() {
    if (mut.observer || typeof MutationObserver === "undefined") return;
    mut.last = now();
    mut.observer = new MutationObserver(function (recs) {
      if (mut.muted) return;
      mut.count += recs.length;
      mut.last = now();
    });
    mut.observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  }
  function visibleBox(el, cs) {
    if (cs.display === "none" || cs.visibility === "hidden" || fin(cs.opacity, 1) < 0.01) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function words(token) {
    return token.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9]+/);
  }
  function hasLoaderWord(str) {
    var toks = String(str || "").split(/\s+/);
    for (var i = 0; i < toks.length; i++) {
      var ws = words(toks[i]);
      for (var j = 0; j < ws.length; j++) if (LOADER_WORDS[ws[j]] === 1) return true;
    }
    return false;
  }
  function describe(el) {
    var d = el.localName;
    if (el.id) d += "#" + String(el.id).slice(0, 40);
    var c = el.getAttribute && el.getAttribute("class");
    if (c) d += "." + String(c).trim().split(/\s+/).slice(0, 2).join(".").slice(0, 60);
    return d;
  }
  function detectLoading() {
    var out = [], seen = [];
    function add(reason, el) { if (out.length < 5 && seen.indexOf(el) < 0) { seen.push(el); out.push({ reason: reason, element: describe(el) }); } }
    var cand;
    try {
      cand = document.querySelectorAll('[aria-busy="true"],[role="progressbar"],progress,[class*="oad"],[class*="pin"],[class*="keleton"],' +
        '[class*="himmer"],[class*="hrobber"],[class*="usy"],[data-testid*="oad"],[data-testid*="pin"],[data-testid*="keleton"],' +
        '[id*="oad"],[id*="pin"],[id*="keleton"]');
    } catch (e) { cand = []; }
    for (var i = 0; i < cand.length && out.length < 5; i++) {
      var el = cand[i], cs = getComputedStyle(el);
      if (el.getAttribute("aria-busy") === "true" && cs.display !== "none") { add("aria-busy", el); continue; }
      var isBar = el.localName === "progress" || el.getAttribute("role") === "progressbar";
      if (isBar) {
        var determinate = el.localName === "progress" ? el.hasAttribute("value") : el.hasAttribute("aria-valuenow");
        if (!determinate && visibleBox(el, cs)) add("progressbar", el);
        continue;
      }
      if (!visibleBox(el, cs)) continue;
      var cls = el.getAttribute("class"), tid = el.getAttribute("data-testid"), id = el.id;
      if (hasLoaderWord(cls) || hasLoaderWord(tid) || hasLoaderWord(id)) add("loader", el);
    }
    var imgs = document.images;
    for (var k = 0; k < imgs.length && out.length < 5; k++) {
      var im = imgs[k];
      if (!im.complete && im.getAttribute("src") && visibleBox(im, getComputedStyle(im))) add("image-loading", im);
    }
    if (document.readyState !== "complete") out.push({ reason: "document", element: "readyState=" + document.readyState });
    if (document.fonts && document.fonts.status === "loading") out.push({ reason: "fonts", element: "document.fonts.status=loading" });
    return out;
  }
  function signature() {
    var body = document.body, text = body ? (body.textContent || "") : "", h = 2166136261 >>> 0;
    var lim = Math.min(text.length, 200000);
    for (var i = 0; i < lim; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    var de = document.documentElement, pending = 0, imgs = document.images;
    for (var k = 0; k < imgs.length; k++) if (!imgs[k].complete) pending++;
    return [document.getElementsByTagName("*").length, text.length, h, de ? de.scrollHeight : 0, de ? de.scrollWidth : 0, pending].join(":");
  }
  function poll() {
    install();
    return { sig: signature(), loading: detectLoading(), sinceMutationMs: Math.round(now() - mut.last), mutations: mut.count,
             readyState: document.readyState };
  }

  // ── рецепт: помощники ──────────────────────────────────────────────────────
  function find(sel) {
    try { return { el: document.querySelector(sel), bad: false }; } catch (e) { return { el: null, bad: true, message: String(e.message || e) }; }
  }
  function locate(sel, scroll) {
    var f = find(sel);
    if (f.bad) return { bad: true, message: f.message };
    if (!f.el) return { found: false };
    var el = f.el, cs = getComputedStyle(el);
    if (scroll && el.scrollIntoView) { try { el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" }); } catch (e) { /* старый браузер */ } }
    var r = el.getBoundingClientRect();
    var visible = cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
    return { found: true, visible: visible, x: r2(r.left + r.width / 2), y: r2(r.top + r.height / 2),
             box: { x: r2(r.left + window.pageXOffset), y: r2(r.top + window.pageYOffset), w: r2(r.width), h: r2(r.height) } };
  }
  function gone(sel) {
    var f = find(sel);
    if (f.bad) return { bad: true, message: f.message };
    if (!f.el) return { gone: true };
    var cs = getComputedStyle(f.el), r = f.el.getBoundingClientRect();
    return { gone: cs.display === "none" || cs.visibility === "hidden" || !(r.width > 0 && r.height > 0) };
  }
  function focus(sel, selectAll) {
    var f = find(sel);
    if (f.bad) return { bad: true, message: f.message };
    if (!f.el) return { found: false };
    var el = f.el;
    try { el.focus(); } catch (e) { /* не фокусируется */ }
    if (selectAll) {
      if (typeof el.select === "function") { try { el.select(); } catch (e2) { /* input без select */ } }
      else if (el.isContentEditable) {
        var range = document.createRange(); range.selectNodeContents(el);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(range);
      }
    }
    var editable = (el.localName === "textarea" || (el.localName === "input" && !/^(checkbox|radio|button|submit|reset|file|image|range|color)$/i.test(el.type)) ||
                    el.isContentEditable === true) && !el.disabled && !el.readOnly;
    return { found: true, focused: document.activeElement === el, editable: editable };
  }
  function selectOption(sel, value) {
    var f = find(sel);
    if (f.bad) return { bad: true, message: f.message };
    if (!f.el) return { found: false };
    var el = f.el;
    if (el.localName !== "select") return { found: true, done: false, reason: "не <select>: открой список кликом и выбери пункт кликом" };
    var idx = -1, i;
    for (i = 0; i < el.options.length; i++) if (el.options[i].value === value) { idx = i; break; }
    if (idx < 0) for (i = 0; i < el.options.length; i++) if (String(el.options[i].text).trim() === value) { idx = i; break; }
    if (idx < 0) return { found: true, done: false, reason: "нет пункта «" + value + "»" };
    el.selectedIndex = idx;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { found: true, done: true };
  }
  function scrollToSel(sel) { return locate(sel, true); }
  function html() { return "<!doctype html>\n" + document.documentElement.outerHTML; }

  // ── шрифты ─────────────────────────────────────────────────────────────────
  function canvasHas(name) {
    try {
      var c = document.createElement("canvas").getContext("2d"), probe = "mmmmmmmmmmlli";
      var widths = function (fallback) { c.font = "72px " + fallback; var a = c.measureText(probe).width; c.font = '72px "' + name + '", ' + fallback; return [a, c.measureText(probe).width]; };
      var m = widths("monospace"), s = widths("serif");
      return m[0] !== m[1] || s[0] !== s[1];
    } catch (e) { return false; }
  }
  /** → Promise<{имя: bool}>: объявлен и загружен через @font-face, либо стоит в системе. */
  function fonts(names) {
    var set = document.fonts, result = {}, pending = [];
    names.forEach(function (name) {
      var declared = false, loaded = false;
      if (set && set.forEach) {
        set.forEach(function (face) {
          var fam = String(face.family).replace(/^["']|["']$/g, "");
          if (fam === name) { declared = true; if (face.status === "loaded") loaded = true; }
        });
      }
      if (loaded) { result[name] = true; return; }
      if (declared && set && set.load) {
        pending.push(Promise.race([set.load('16px "' + name + '"'), new Promise(function (res) { setTimeout(function () { res(null); }, 2500); })])
          .then(function (faces) { result[name] = !!(faces && faces.length); }, function () { result[name] = false; }));
        return;
      }
      result[name] = canvasHas(name);
    });
    return Promise.all(pending).then(function () { return result; });
  }

  return {
    version: VERSION, install: install, poll: poll, snapshot: snapshot, locate: locate, gone: gone, focus: focus,
    selectOption: selectOption, scrollTo: scrollToSel, html: html, fonts: fonts
  };
})
