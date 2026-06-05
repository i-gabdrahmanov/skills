const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE";          // 13.33 x 7.5
p.author = "Конвейер разработки фич";
p.title = "Конвейер разработки фич под контролем качества";

const W = 13.33, H = 7.5, M = 0.7;
const C = {
  dark: "0E1B2A", navy: "13293D", panel: "16314A",
  light: "F4F6F9", card: "FFFFFF",
  teal: "1C7293", tealLt: "2E8BA6", ice: "CADCFC",
  amber: "E8A13A", red: "D7424A", green: "3FA796",
  ink: "16273A", mute: "5B6B7B", white: "FFFFFF", line: "DDE5EC",
};
const HF = "Georgia", BF = "Calibri", MF = "Calibri";
const shadow = () => ({ type: "outer", color: "0E1B2A", blur: 7, offset: 3, angle: 135, opacity: 0.12 });

function footer(s, n) {
  s.addText("Конвейер разработки фич · контроль качества встроен в систему", {
    x: M, y: H - 0.45, w: 10, h: 0.3, fontFace: BF, fontSize: 9.5, color: C.mute, align: "left", margin: 0 });
  s.addText(String(n).padStart(2, "0"), {
    x: W - 1.1, y: H - 0.45, w: 0.5, h: 0.3, fontFace: BF, fontSize: 9.5, color: C.mute, align: "right", margin: 0 });
}
function head(s, tag, title) {
  s.addShape(p.shapes.RECTANGLE, { x: M, y: 0.55, w: 0.16, h: 0.42, fill: { color: C.teal } });
  s.addText(tag.toUpperCase(), { x: M + 0.28, y: 0.55, w: 11, h: 0.3, fontFace: BF, fontSize: 12, color: C.teal, charSpacing: 2, bold: true, margin: 0 });
  s.addText(title, { x: M + 0.27, y: 0.86, w: W - 2 * M - 0.2, h: 0.75, fontFace: HF, fontSize: 28, bold: true, color: C.ink, margin: 0 });
}
function card(s, x, y, w, h, fill) {
  s.addShape(p.shapes.RECTANGLE, { x, y, w, h, fill: { color: fill || C.card }, line: { color: C.line, width: 1 }, shadow: shadow() });
}

// ───────────────────────── 1. ТИТУЛ ─────────────────────────
let s = p.addSlide(); s.background = { color: C.dark };
s.addShape(p.shapes.RECTANGLE, { x: 0, y: 0, w: 0.35, h: H, fill: { color: C.teal } });
s.addShape(p.shapes.RECTANGLE, { x: 0.35, y: 0, w: 0.08, h: H, fill: { color: C.amber } });
s.addText("АВТОМАТИЗАЦИЯ РАЗРАБОТКИ", { x: M + 0.2, y: 1.35, w: 11, h: 0.4, fontFace: BF, fontSize: 13, color: C.ice, charSpacing: 3, bold: true, margin: 0 });
s.addText("Конвейер разработки фич\nпод контролем качества", { x: M + 0.15, y: 1.85, w: 11.6, h: 1.9, fontFace: HF, fontSize: 46, bold: true, color: C.white, lineSpacingMultiple: 1.0, margin: 0 });
s.addText("Система сама проводит задачу от заявки до готового кода — и не даёт пропустить проверки качества.", {
  x: M + 0.2, y: 3.95, w: 11.2, h: 0.8, fontFace: BF, fontSize: 19, color: C.ice, margin: 0 });
[["Быстро — как ИИ", C.teal], ["Надёжно — как процесс", C.amber], ["Прозрачно и проверяемо", C.tealLt]].forEach((t, i) => {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M + 0.2 + i * 3.55, y: 5.1, w: 3.35, h: 0.6, fill: { color: C.panel }, line: { color: t[1], width: 1.25 }, rectRadius: 0.08 });
  s.addText(t[0], { x: M + 0.2 + i * 3.55, y: 5.1, w: 3.35, h: 0.6, fontFace: BF, fontSize: 14, color: C.white, align: "center", valign: "middle", margin: 0 });
});

// ───────────────────────── 2. ПРОБЛЕМА ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Проблема", "ИИ пишет код быстро — но без контроля это риск");
card(s, M, 1.85, 4.3, 4.55, C.dark);
s.addText("+25%", { x: M, y: 2.25, w: 4.3, h: 1.3, fontFace: HF, fontSize: 70, bold: true, color: C.amber, align: "center", margin: 0 });
s.addText("уязвимостей в коде, который ИИ написал быстро, но никто не проверил", {
  x: M + 0.35, y: 3.6, w: 3.6, h: 1.1, fontFace: BF, fontSize: 15, color: C.ice, align: "center", margin: 0 });
s.addText("Скорость без проверки — это технический долг с процентами", {
  x: M + 0.35, y: 5.4, w: 3.6, h: 0.8, fontFace: BF, fontSize: 12, italic: true, color: C.mute, align: "center", margin: 0 });
const probs = [
  ["Пропускает шаги", "ИИ может «забыть» написать тесты или прогнать проверки — текстовая инструкция его ни к чему не обязывает."],
  ["Тихие сбои", "Однажды система запустилась с отключённым контролем — и этого никто не заметил: качество держалось на честном слове."],
  ["Делает лишнее или опасное", "Может изменить то, о чём не просили, вплоть до необратимых действий с данными."],
];
let yy = 1.85;
probs.forEach((q) => {
  card(s, M + 4.7, yy, W - M - (M + 4.7), 1.42);
  s.addShape(p.shapes.RECTANGLE, { x: M + 4.7, y: yy, w: 0.1, h: 1.42, fill: { color: C.red } });
  s.addText(q[0], { x: M + 4.95, y: yy + 0.14, w: 7.3, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: C.ink, margin: 0 });
  s.addText(q[1], { x: M + 4.95, y: yy + 0.56, w: 7.3, h: 0.78, fontFace: BF, fontSize: 13, color: C.mute, margin: 0 });
  yy += 1.55;
});
footer(s, 2);

// ───────────────────────── 3. ИДЕЯ ─────────────────────────
s = p.addSlide(); s.background = { color: C.dark };
s.addShape(p.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.amber } });
s.addText("ГЛАВНАЯ ИДЕЯ", { x: M, y: 1.2, w: 10, h: 0.4, fontFace: BF, fontSize: 13, color: C.amber, charSpacing: 3, bold: true, margin: 0 });
s.addText("Правила встроены в систему,\nа не в просьбы к ИИ", {
  x: M, y: 1.7, w: 11.7, h: 1.9, fontFace: HF, fontSize: 40, bold: true, color: C.white, lineSpacingMultiple: 1.02, margin: 0 });
s.addText("Мы не надеемся, что ИИ сам соблюдёт правила. Система просто не даёт их нарушить — рискованный шаг останавливается автоматически.", {
  x: M, y: 3.85, w: 11.5, h: 1.1, fontFace: BF, fontSize: 19, color: C.ice, margin: 0 });
card(s, M, 5.2, W - 2 * M, 1.4, C.panel);
s.addShape(p.shapes.RECTANGLE, { x: M, y: 5.2, w: 0.12, h: 1.4, fill: { color: C.amber } });
s.addText([
  { text: "Аналогия:  ", options: { bold: true, color: C.amber } },
  { text: "это не «просим водителя пристегнуться», а ремень, без которого машина не трогается с места. И сами модели ИИ устаревают каждые полгода — ценность в управляемом процессе вокруг них, он остаётся.", options: { color: C.white } },
], { x: M + 0.35, y: 5.2, w: W - 2 * M - 0.6, h: 1.4, fontFace: BF, fontSize: 15, valign: "middle", margin: 0 });

// ───────────────────────── 4. ЭТАПЫ ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Как идёт работа", "Один конвейер: от заявки до готового кода");
const phases = [
  ["1", "Требования", "Понимаем, что нужно бизнесу, на его языке"],
  ["2", "Изучение системы", "Смотрим, что в проекте уже есть"],
  ["3", "Проектирование", "Решаем, как сделать, до написания кода"],
  ["4", "Постановка задач", "Заводим задачи в трекер (Jira)"],
  ["5", "Код с тестами", "Пишем код — обязательно с тестами"],
  ["6", "Проверка качества", "Тесты зелёные, покрытие подтверждено"],
  ["7", "Документация", "Обновляем описание системы"],
  ["8", "Готово к выкладке", "Собираем pull request на ревью"],
];
const cols = 4, cw = 2.85, gx = 0.15, ch = 1.85, gy = 0.35, x0 = M, y0 = 1.95;
phases.forEach((ph, i) => {
  const cx = x0 + (i % cols) * (cw + gx), cy = y0 + Math.floor(i / cols) * (ch + gy);
  card(s, cx, cy, cw, ch);
  s.addShape(p.shapes.OVAL, { x: cx + 0.22, y: cy + 0.22, w: 0.55, h: 0.55, fill: { color: C.teal } });
  s.addText(ph[0], { x: cx + 0.22, y: cy + 0.22, w: 0.55, h: 0.55, fontFace: HF, fontSize: 18, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
  s.addText(ph[1], { x: cx + 0.22, y: cy + 0.92, w: cw - 0.44, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: C.ink, margin: 0 });
  s.addText(ph[2], { x: cx + 0.22, y: cy + 1.3, w: cw - 0.44, h: 0.5, fontFace: BF, fontSize: 11.5, color: C.mute, margin: 0 });
});
s.addText("На каждом этапе — автоматическая проверка: пока она не пройдена, дальше конвейер не пускает.", {
  x: M, y: 6.55, w: 12, h: 0.4, fontFace: BF, fontSize: 13, italic: true, color: C.teal, margin: 0 });
footer(s, 4);

// ───────────────────────── 5. ВСТРОЕННЫЕ ПРОВЕРКИ ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Контроль качества", "Что система не даёт сделать неправильно");
const rules = [
  ["Нельзя сдать без качества", "Фича не уходит дальше, пока тесты не зелёные, а покрытие кода не подтверждено."],
  ["Сначала тесты, потом код", "Система не примет рабочий код, пока заранее не написаны тесты на него."],
  ["Рискованное — с подтверждением", "Чем опаснее действие, тем строже: правка платежей или персональных данных требует согласия человека."],
  ["Контроль расходов", "У каждого запуска есть бюджет: при приближении к лимиту — предупреждение, при превышении — стоп."],
  ["Защита от опасного", "Блокируются разрушительные команды, утечка персональных данных и попытки подменить инструкции."],
];
let ry = 1.95;
rules.forEach((r, i) => {
  card(s, M, ry, W - 2 * M, 0.84);
  s.addShape(p.shapes.OVAL, { x: M + 0.22, y: ry + 0.22, w: 0.4, h: 0.4, fill: { color: C.green } });
  s.addText("✓", { x: M + 0.22, y: ry + 0.22, w: 0.4, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
  s.addText(r[0], { x: M + 0.8, y: ry, w: 4.0, h: 0.84, fontFace: BF, fontSize: 15, bold: true, color: C.ink, valign: "middle", margin: 0 });
  s.addText(r[1], { x: M + 4.9, y: ry, w: W - 2 * M - 5.1, h: 0.84, fontFace: BF, fontSize: 13, color: C.mute, valign: "middle", margin: 0 });
  ry += 0.92;
});
footer(s, 5);

// ───────────────────────── 6. КРИТИЧНОСТЬ ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Уровень риска", "Строгость зависит от важности фичи");
s.addText("В начале система спрашивает, насколько фича критична, и подстраивает строгость проверок. Безопасное — проходит само, рискованное — требует подтверждения человека.", {
  x: M, y: 1.95, w: W - 2 * M, h: 0.8, fontFace: BF, fontSize: 16, color: C.ink, margin: 0 });
const lv = [
  ["Низкая важность", "Например: правка документации или текстов", "Проходит автоматически", C.green],
  ["Обычная фича", "Например: новая функция в продукте", "Код проходит через ревью и тесты", C.teal],
  ["Высокая важность", "Например: платежи, персональные данные, доступы", "Требуется явное подтверждение человека", C.red],
];
let ly = 2.95;
lv.forEach((l) => {
  card(s, M, ly, W - 2 * M, 1.1);
  s.addShape(p.shapes.RECTANGLE, { x: M, y: ly, w: 0.14, h: 1.1, fill: { color: l[3] } });
  s.addText(l[0], { x: M + 0.4, y: ly, w: 3.6, h: 1.1, fontFace: BF, fontSize: 17, bold: true, color: C.ink, valign: "middle", margin: 0 });
  s.addText(l[1], { x: M + 4.1, y: ly, w: 4.6, h: 1.1, fontFace: BF, fontSize: 13, italic: true, color: C.mute, valign: "middle", margin: 0 });
  s.addShape(p.shapes.RECTANGLE, { x: M + 8.8, y: ly + 0.22, w: W - 2 * M - 8.95, h: 0.66, fill: { color: C.card }, line: { color: l[3], width: 1.25 } });
  s.addText(l[2], { x: M + 8.95, y: ly + 0.22, w: W - 2 * M - 9.25, h: 0.66, fontFace: BF, fontSize: 12, bold: true, color: C.ink, valign: "middle", margin: 0 });
  ly += 1.22;
});
footer(s, 6);

// ───────────────────────── 7. ТЕСТЫ ВПЕРЁД ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Качество кода", "Сначала тесты — потом код");
s.addText("Код не принимается без тестов, написанных заранее. Это гарантирует, что код реально решает задачу, а не просто «компилируется».", {
  x: M, y: 1.95, w: 6.6, h: 1.4, fontFace: BF, fontSize: 17, color: C.ink, margin: 0 });
const tdd = [
  ["1", "Пишем тест на нужное поведение — он пока не проходит (кода ещё нет)."],
  ["2", "Пишем код ровно столько, чтобы тест стал зелёным."],
  ["3", "Готово: поведение подтверждено тестом, лишнего не написано."],
];
let ty = 3.5;
tdd.forEach((t) => {
  s.addShape(p.shapes.OVAL, { x: M, y: ty, w: 0.5, h: 0.5, fill: { color: C.teal } });
  s.addText(t[0], { x: M, y: ty, w: 0.5, h: 0.5, fontFace: HF, fontSize: 16, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
  s.addText(t[1], { x: M + 0.7, y: ty - 0.04, w: 6.0, h: 0.6, fontFace: BF, fontSize: 13.5, color: C.ink, valign: "middle", margin: 0 });
  ty += 0.85;
});
// example panel
card(s, M + 7.2, 1.95, W - M - (M + 7.2), 4.3, C.dark);
s.addText("Пример", { x: M + 7.5, y: 2.2, w: 4, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: C.amber, margin: 0 });
s.addText([
  { text: "Задача: ", options: { bold: true, color: C.white } },
  { text: "автоматически закрывать пустые заявки.", options: { color: C.ice, breakLine: true } },
  { text: "\n", options: { breakLine: true, fontSize: 8 } },
  { text: "1.  ", options: { bold: true, color: C.amber } },
  { text: "Сначала тест: «пустая заявка закрылась» — он падает, потому что кода ещё нет.", options: { color: C.white, breakLine: true } },
  { text: "\n", options: { breakLine: true, fontSize: 8 } },
  { text: "2.  ", options: { bold: true, color: C.amber } },
  { text: "Затем код — пока тест не станет зелёным.", options: { color: C.white, breakLine: true } },
  { text: "\n", options: { breakLine: true, fontSize: 8 } },
  { text: "Результат: ", options: { bold: true, color: C.green } },
  { text: "функция гарантированно делает то, что заявлено.", options: { color: C.ice } },
], { x: M + 7.5, y: 2.65, w: W - M - (M + 7.2) - 0.6, h: 3.4, fontFace: BF, fontSize: 14, lineSpacingMultiple: 1.08, margin: 0 });
footer(s, 7);

// ───────────────────────── 8. НАДЁЖНОСТЬ И ПРОЗРАЧНОСТЬ ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Надёжность", "Контроль включён — и это проверяется");
card(s, M, 1.95, 3.6, 3.1, C.dark);
s.addText("27", { x: M, y: 2.3, w: 3.6, h: 1.15, fontFace: HF, fontSize: 72, bold: true, color: C.green, align: "center", margin: 0 });
s.addText("автоматических проверок\nследят, что все правила\nработают как задумано", { x: M, y: 3.55, w: 3.6, h: 1.2, fontFace: BF, fontSize: 13.5, color: C.ice, align: "center", margin: 0 });
const rel = [
  ["Журнал всех действий", "Всё, что делает ИИ, записывается — можно разобрать любой шаг постфактум."],
  ["Проверка перед запуском", "Система сама убеждается, что контроль включён, и громко предупреждает, если нет."],
  ["Один источник правил", "Правила живут в системе, а не в чьей-то памяти — работают одинаково на любой машине."],
];
let rey = 1.95;
rel.forEach((r) => {
  card(s, M + 3.95, rey, W - M - (M + 3.95), 0.95);
  s.addShape(p.shapes.RECTANGLE, { x: M + 3.95, y: rey, w: 0.1, h: 0.95, fill: { color: C.teal } });
  s.addText(r[0], { x: M + 4.2, y: rey + 0.12, w: 7.5, h: 0.38, fontFace: BF, fontSize: 15, bold: true, color: C.ink, margin: 0 });
  s.addText(r[1], { x: M + 4.2, y: rey + 0.5, w: 7.5, h: 0.4, fontFace: BF, fontSize: 12.5, color: C.mute, margin: 0 });
  rey += 1.06;
});
footer(s, 8);

// ───────────────────────── 9. ЦЕННОСТЬ ─────────────────────────
s = p.addSlide(); s.background = { color: C.light };
head(s, "Что это даёт", "Скорость ИИ и надёжность процесса вместе");
const val = [
  ["Быстро без потери качества", "Скорость ИИ остаётся, но каждый результат проходит контроль."],
  ["Предсказуемый результат", "Качество одинаково независимо от того, какая модель ИИ под капотом."],
  ["Меньше дефектов в проде", "Тесты, ревью и защита данных встроены, а не «когда вспомним»."],
  ["Полная прозрачность", "Каждый шаг записан и проверяем — доверие и аудит из коробки."],
];
const vc = 2, vw = (W - 2 * M - 0.3) / 2, vh = 1.9;
val.forEach((v, i) => {
  const cx = M + (i % vc) * (vw + 0.3), cy = 2.0 + Math.floor(i / vc) * (vh + 0.25);
  card(s, cx, cy, vw, vh);
  s.addShape(p.shapes.OVAL, { x: cx + 0.3, y: cy + 0.3, w: 0.55, h: 0.55, fill: { color: C.green } });
  s.addText("✓", { x: cx + 0.3, y: cy + 0.3, w: 0.55, h: 0.55, fontFace: BF, fontSize: 19, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
  s.addText(v[0], { x: cx + 1.05, y: cy + 0.32, w: vw - 1.3, h: 0.55, fontFace: BF, fontSize: 16, bold: true, color: C.ink, valign: "middle", margin: 0 });
  s.addText(v[1], { x: cx + 0.35, y: cy + 1.0, w: vw - 0.7, h: 0.8, fontFace: BF, fontSize: 13, color: C.mute, margin: 0 });
});
footer(s, 9);

// ───────────────────────── 10. ИТОГ ─────────────────────────
s = p.addSlide(); s.background = { color: C.dark };
s.addShape(p.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.teal } });
s.addText("ИТОГ", { x: M, y: 1.5, w: 10, h: 0.4, fontFace: BF, fontSize: 13, color: C.amber, charSpacing: 3, bold: true, margin: 0 });
s.addText("Модель можно заменить —\nпроцесс остаётся", {
  x: M, y: 2.0, w: 11.7, h: 1.9, fontFace: HF, fontSize: 42, bold: true, color: C.white, lineSpacingMultiple: 1.02, margin: 0 });
s.addText("Мы построили управляемый конвейер, который превращает быстрый ИИ-код в надёжный продукт: тесты, ревью, защита данных и прозрачность встроены в систему и не зависят от настроения модели.", {
  x: M, y: 4.1, w: 11.6, h: 1.4, fontFace: BF, fontSize: 18, color: C.ice, margin: 0 });
s.addText([
  { text: "Статус:  ", options: { bold: true, color: C.green } },
  { text: "работает, проверено автотестами, готово к развёртыванию на проектах.", options: { color: C.white } },
], { x: M, y: 5.9, w: 11.6, h: 0.6, fontFace: BF, fontSize: 16, margin: 0 });

p.writeFile({ fileName: process.env.HOME + "/.gigacode3/docs/forge.pptx" }).then((f) => console.log("WROTE", f));
