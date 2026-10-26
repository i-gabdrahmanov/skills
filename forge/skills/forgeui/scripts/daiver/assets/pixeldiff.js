/* pixeldiff.js — попиксельный дифф двух PNG в Chromium (canvas): порт алгоритма pixelmatch v5 (задача 5.1, спека §14.6).
 *
 * Зачем в браузере: у Python-стороны нет PNG-декодера, а npm не ставим. Картинки приходят base64-строками, считает canvas, наружу — числа,
 * зоны и два PNG (diff и полоса «макет | реализация | дифф»).
 *
 * Алгоритм (как в pixelmatch): дельта цвета в пространстве YIQ с учётом альфы (полупрозрачное смешивается с белым), порог `maxDelta = 35215 ·
 * threshold²`; пиксель, чья дельта выше порога, но который по соседям похож на сглаживание края (antialiased + hasManySiblings), в счёт не идёт и
 * красится жёлтым; остальные — красным и в счёт. Одинаковые пиксели рисуются бледно-серыми (alpha 0.1). Разные размеры — обе картинки кладутся на
 * белый холст максимального размера (левый верхний угол). Процент — ПРОЦЕНТ (3.18 = 3.18 %), 0 для идентичных.
 *
 * Зоны: счётчики по ячейкам REGION_CELL×REGION_CELL, связные группы (8 соседей) → прямоугольники, не больше MAX_ZONES самых тяжёлых.
 *
 * Вход: {a, b, mimeA?, mimeB?, threshold?, includeAA?, crop?: {x,y,w,h}, cell?}; a — «макет» (goal image / reference; crop режет ТОЛЬКО a), b — реализация.
 * Выход: {width, height, sizeA, sizeB, diffPixels, aaPixels, percent, zones[], diffPng, stripPng} (PNG — base64 без префикса data:).
 */
(function () {
  var MAX_DELTA_BASE = 35215;
  var MAX_ZONES = 12;
  var GUTTER = 8;

  function rgb2y(r, g, b) { return r * 0.29889531 + g * 0.58662247 + b * 0.11448223; }
  function rgb2i(r, g, b) { return r * 0.59597799 - g * 0.27417610 - b * 0.32180189; }
  function rgb2q(r, g, b) { return r * 0.21147017 - g * 0.52261711 + b * 0.31114694; }
  function blend(c, a) { return 255 + (c - 255) * a; }

  function colorDelta(img1, img2, k, m, yOnly) {
    var r1 = img1[k], g1 = img1[k + 1], b1 = img1[k + 2], a1 = img1[k + 3];
    var r2 = img2[m], g2 = img2[m + 1], b2 = img2[m + 2], a2 = img2[m + 3];
    if (a1 === a2 && r1 === r2 && g1 === g2 && b1 === b2) return 0;
    if (a1 < 255) { a1 /= 255; r1 = blend(r1, a1); g1 = blend(g1, a1); b1 = blend(b1, a1); }
    if (a2 < 255) { a2 /= 255; r2 = blend(r2, a2); g2 = blend(g2, a2); b2 = blend(b2, a2); }
    var y1 = rgb2y(r1, g1, b1), y2 = rgb2y(r2, g2, b2), y = y1 - y2;
    if (yOnly) return y;
    var i = rgb2i(r1, g1, b1) - rgb2i(r2, g2, b2), q = rgb2q(r1, g1, b1) - rgb2q(r2, g2, b2);
    var delta = 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q;
    return y1 > y2 ? -delta : delta;
  }

  function hasManySiblings(img, x1, y1, width, height) {
    var x0 = Math.max(x1 - 1, 0), y0 = Math.max(y1 - 1, 0), x2 = Math.min(x1 + 1, width - 1), y2 = Math.min(y1 + 1, height - 1);
    var pos = (y1 * width + x1) * 4;
    var zeroes = (x1 === x0 || x1 === x2 || y1 === y0 || y1 === y2) ? 1 : 0;
    for (var x = x0; x <= x2; x++) {
      for (var y = y0; y <= y2; y++) {
        if (x === x1 && y === y1) continue;
        var pos2 = (y * width + x) * 4;
        if (img[pos] === img[pos2] && img[pos + 1] === img[pos2 + 1] && img[pos + 2] === img[pos2 + 2] && img[pos + 3] === img[pos2 + 3]) zeroes++;
        if (zeroes > 2) return true;
      }
    }
    return false;
  }

  function antialiased(img, x1, y1, width, height, img2) {
    var x0 = Math.max(x1 - 1, 0), y0 = Math.max(y1 - 1, 0), x2 = Math.min(x1 + 1, width - 1), y2 = Math.min(y1 + 1, height - 1);
    var pos = (y1 * width + x1) * 4;
    var zeroes = (x1 === x0 || x1 === x2 || y1 === y0 || y1 === y2) ? 1 : 0;
    var min = 0, max = 0, minX = 0, minY = 0, maxX = 0, maxY = 0;
    for (var x = x0; x <= x2; x++) {
      for (var y = y0; y <= y2; y++) {
        if (x === x1 && y === y1) continue;
        var delta = colorDelta(img, img, pos, (y * width + x) * 4, true);
        if (delta === 0) {
          zeroes++;
          if (zeroes > 2) return false;
        } else if (delta < min) {
          min = delta; minX = x; minY = y;
        } else if (delta > max) {
          max = delta; maxX = x; maxY = y;
        }
      }
    }
    if (min === 0 || max === 0) return false;
    return (hasManySiblings(img, minX, minY, width, height) && hasManySiblings(img, maxX, maxY, width, height)) ||
           (hasManySiblings(img2, minX, minY, width, height) && hasManySiblings(img2, maxX, maxY, width, height));
  }

  function drawPixel(out, pos, r, g, b) { out[pos] = r; out[pos + 1] = g; out[pos + 2] = b; out[pos + 3] = 255; }
  function drawGray(img, i, alpha, out) {
    var v = blend(rgb2y(img[i], img[i + 1], img[i + 2]), alpha * img[i + 3] / 255);
    drawPixel(out, i, v, v, v);
  }

  /** Чистое ядро: два RGBA-массива одного размера → {diff, aa, mask}. Нужно ядру и тесту алгоритма. */
  function pixelmatch(img1, img2, out, width, height, threshold, includeAA) {
    var maxDelta = MAX_DELTA_BASE * threshold * threshold;
    var diff = 0, aa = 0, mask = new Uint8Array(width * height);
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        var pos = (y * width + x) * 4;
        var delta = colorDelta(img1, img2, pos, pos, false);
        if (Math.abs(delta) > maxDelta) {
          if (!includeAA && (antialiased(img1, x, y, width, height, img2) || antialiased(img2, x, y, width, height, img1))) {
            aa++;
            if (out) drawPixel(out, pos, 255, 255, 0);
          } else {
            diff++;
            mask[y * width + x] = 1;
            if (out) drawPixel(out, pos, 255, 0, 0);
          }
        } else if (out) {
          drawGray(img1, pos, 0.1, out);
        }
      }
    }
    return { diff: diff, aa: aa, mask: mask };
  }

  function zonesOf(mask, width, height, cell) {
    var cols = Math.ceil(width / cell), rows = Math.ceil(height / cell);
    var counts = new Uint32Array(cols * rows);
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        if (mask[y * width + x]) counts[Math.floor(y / cell) * cols + Math.floor(x / cell)]++;
      }
    }
    var seen = new Uint8Array(cols * rows), zones = [];
    for (var c = 0; c < counts.length; c++) {
      if (!counts[c] || seen[c]) continue;
      var stack = [c], minC = cols, maxC = -1, minR = rows, maxR = -1, pixels = 0;
      seen[c] = 1;
      while (stack.length) {
        var cur = stack.pop(), cc = cur % cols, rr = Math.floor(cur / cols);
        pixels += counts[cur];
        if (cc < minC) minC = cc; if (cc > maxC) maxC = cc; if (rr < minR) minR = rr; if (rr > maxR) maxR = rr;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            var nc = cc + dx, nr = rr + dy;
            if (nc < 0 || nr < 0 || nc >= cols || nr >= rows) continue;
            var ni = nr * cols + nc;
            if (counts[ni] && !seen[ni]) { seen[ni] = 1; stack.push(ni); }
          }
        }
      }
      var x0 = minC * cell, y0 = minR * cell, x1 = Math.min((maxC + 1) * cell, width), y1 = Math.min((maxR + 1) * cell, height);
      zones.push({ x: x0, y: y0, w: x1 - x0, h: y1 - y0, pixels: pixels, percent: Math.round(pixels / ((x1 - x0) * (y1 - y0)) * 10000) / 100 });
    }
    zones.sort(function (a, b) { return b.pixels - a.pixels || a.y - b.y || a.x - b.x; });
    return zones.slice(0, MAX_ZONES);
  }

  function loadImage(b64, mime) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error("картинка не декодируется")); };
      img.src = "data:" + (mime || "image/png") + ";base64," + b64;
    });
  }

  function canvas(w, h) { var c = document.createElement("canvas"); c.width = w; c.height = h; return c; }

  function padded(img, W, H, crop) {
    var c = canvas(W, H), ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, W, H);
    if (crop) ctx.drawImage(img, crop.x, crop.y, crop.w, crop.h, 0, 0, crop.w, crop.h);
    else ctx.drawImage(img, 0, 0);
    return c;
  }

  function b64(c) { return c.toDataURL("image/png").split(",", 2)[1]; }

  async function run(args) {
    var threshold = typeof args.threshold === "number" ? args.threshold : 0.1;
    var imgA = await loadImage(args.a, args.mimeA), imgB = await loadImage(args.b, args.mimeB);
    var crop = args.crop || null;
    var sizeA = crop ? { w: crop.w, h: crop.h } : { w: imgA.naturalWidth, h: imgA.naturalHeight };
    var sizeB = { w: imgB.naturalWidth, h: imgB.naturalHeight };
    var W = Math.max(sizeA.w, sizeB.w), H = Math.max(sizeA.h, sizeB.h);
    var ca = padded(imgA, W, H, crop), cb = padded(imgB, W, H, null);
    var da = ca.getContext("2d").getImageData(0, 0, W, H), db = cb.getContext("2d").getImageData(0, 0, W, H);
    var out = new ImageData(W, H);
    var res = pixelmatch(da.data, db.data, out.data, W, H, threshold, !!args.includeAA);
    var cd = canvas(W, H);
    cd.getContext("2d").putImageData(out, 0, 0);
    var strip = canvas(W * 3 + GUTTER * 2, H), sctx = strip.getContext("2d");
    sctx.fillStyle = "#c8c8c8";
    sctx.fillRect(0, 0, strip.width, strip.height);
    sctx.drawImage(ca, 0, 0);
    sctx.drawImage(cb, W + GUTTER, 0);
    sctx.drawImage(cd, (W + GUTTER) * 2, 0);
    return {
      width: W, height: H, sizeA: sizeA, sizeB: sizeB, diffPixels: res.diff, aaPixels: res.aa,
      percent: res.diff ? Math.round(res.diff / (W * H) * 100000) / 1000 : 0,
      zones: zonesOf(res.mask, W, H, args.cell || 16), diffPng: b64(cd), stripPng: b64(strip)
    };
  }

  window.__pixeldiff = { run: run, pixelmatch: pixelmatch, colorDelta: colorDelta, antialiased: antialiased, zonesOf: zonesOf };
})();
