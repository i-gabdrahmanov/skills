"use strict";

const byId = id => document.getElementById(id);
const draft = new Map();
const draftRevisions = new Map();
const fieldErrors = new Map();
let catalog;
let confirmation;

function node(tag, value) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  return element;
}

function option(value, label = value) {
  return new Option(label, value);
}

function display(value) {
  if (value === undefined || value === null) return "Не задано";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function report(message, error = false) {
  byId("status").textContent = message;
  byId("status").classList.toggle("error", error);
}

async function request(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Forge-Session": catalog.session},
    body: JSON.stringify(payload)
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error);
    error.field = data.field;
    throw error;
  }
  return data;
}

async function load(feature = byId("feature").value) {
  const data = await request("/api/catalog?feature=" + encodeURIComponent(feature));
  catalog = data;
  byId("project").textContent = data.project;
  byId("feature").replaceChildren(option("", "Выберите фичу"),
    ...data.features.map(key => option(key)));
  byId("feature").value = feature;
  byId("live").textContent = data.live
    ? "Текущий прогон " + data.live.join("/") + " использует снимок политики. Настройки проекта применятся при следующем запуске."
    : "";
  render();
}

const categories = {
  quality: "Качество", gates: "Гейты", project: "Проект", conventions: "Соглашения",
  docs: "Документация", risk: "Риски", sources: "Источники", spec: "Спецификация",
  inputs: "Входные данные", decisions: "Решения", autonomy: "Автономность"
};

let helpId = 0;
function tooltip(text) {
  const wrapper = node("span");
  wrapper.className = "help";
  const button = node("button", "?");
  const hint = node("span", text);
  hint.id = "help-" + ++helpId;
  hint.hidden = true;
  hint.setAttribute("role", "tooltip");
  button.setAttribute("aria-label", "Подсказка");
  button.setAttribute("aria-describedby", hint.id);
  const show = () => { hint.hidden = false; };
  const hide = () => { hint.hidden = true; };
  button.addEventListener("focus", show);
  button.addEventListener("blur", hide);
  wrapper.addEventListener("mouseenter", show);
  wrapper.addEventListener("mouseleave", () => {
    if (document.activeElement !== button) hide();
  });
  button.addEventListener("click", show);
  button.addEventListener("keydown", event => {
    if (event.key === "Escape" && !hint.hidden) {
      event.stopPropagation();
      hide();
    }
  });
  wrapper.append(button, hint);
  return wrapper;
}

function nullable(row) {
  return row.type === "bool" || row.type === "string" || row.default === null;
}

function formats(row) {
  if (row.enum) return "Варианты: " + row.enum.join(", ") + ".";
  if (row.type === "bool") return "Выберите «Включено» или «Выключено».";
  if (row.type === "list") return 'Строки через запятую или JSON-массив, например ["a", "b"].';
  if (row.type === "int" || row.type === "float") {
    const range = row.min === undefined && row.max === undefined ? "" :
      " Диапазон: " + (row.min ?? "−∞") + "–" + (row.max ?? "∞") + ".";
    return (row.type === "int" ? "Целое число." : "Дробное число, например 0,85.") + range;
  }
  return "Текст. Формат зависит от назначения поля.";
}

function strValue(value) {
  return value === null || value === undefined ? "" :
    Array.isArray(value) ? JSON.stringify(value) : String(value);
}

function rowElement(row) {
  const wrapper = node("article");
  wrapper.className = "row";
  const identity = node("div");
  identity.append(node("strong", row.title), tooltip(
    [row.description, "Область: " + row.scope + ".", formats(row),
      row.default === null ? "" : "По умолчанию: " + display(row.default) + "."].filter(Boolean).join(" ")));
  const key = node("div", row.id);
  key.className = "meta";
  const source = row.source === "default" ? "По умолчанию" : row.source.replace(/^file:/, "");
  const current = node("div", display(row.value) + " · " + source);
  current.className = "meta";
  identity.append(key, current);
  const label = node("label", "Новое значение");
  const stamp = row.target + "/" + row.id;
  const input = row.enum || row.type === "bool" ? node("select") : node("input");
  if (input.tagName === "SELECT") {
    input.append(option("", "Выберите значение"));
    if (nullable(row) && !row.none_is_value) input.append(option("null", "Не задано (null)"));
    if (row.enum) input.append(...row.enum.map(value => {
      const reason = row.unavailable?.[value];
      const item = option(value, reason ? value + " — " + reason : value);
      item.disabled = Boolean(reason);
      return item;
    }));
    else input.append(option("true", "Включено"), option("false", "Выключено"));
  } else {
    input.type = row.sensitive ? "password" : "text";
    input.autocomplete = "off";
    if (row.type === "int" || row.type === "float") input.inputMode = "decimal";
    if (row.sensitive) input.placeholder = "Введите новое значение";
    else if (nullable(row)) input.placeholder = "null — явное пустое значение";
  }
  const original = row.sensitive ? "" : row.value === null && input.tagName === "SELECT"
    ? "null" : strValue(row.value);
  input.value = draft.get(stamp)?.value ?? original;
  input.disabled = !row.editable;
  input.setAttribute("aria-label", "Новое значение — " + row.title);
  const fieldError = node("span", fieldErrors.get(row.id) || "");
  fieldError.className = "error";
  fieldError.id = "error-" + row.id;
  fieldError.setAttribute("role", "alert");
  input.setAttribute("aria-describedby", fieldError.id);
  input.setAttribute("aria-invalid", fieldErrors.has(row.id) ? "true" : "false");
  input.addEventListener("input", () => {
    fieldErrors.delete(row.id);
    fieldError.textContent = "";
    input.setAttribute("aria-invalid", "false");
    const value = input.value;
    if (value === original || (!value && input.tagName === "SELECT")) {
      draft.delete(stamp);
    } else {
      if (![...draft.values()].some(change => change.file === row.file)) {
        draftRevisions.set(row.file, catalog.revisions[row.file]);
      }
      draft.set(stamp, {op: "set", id: row.id, value, file: row.file});
    }
    updateFooter();
  });
  label.append(input, fieldError);
  if (row.error || (!row.editable && row.target)) {
    const issue = node("span", row.error || "Сначала инициализируйте файл штатной командой Forge: " + row.target);
    issue.className = "error";
    label.append(issue);
  }
  wrapper.append(identity, label);
  return wrapper;
}

function updateFooter() {
  const select = byId("files");
  const previous = select.value;
  const files = new Set([...draft.values()].map(change => change.file));
  select.replaceChildren(...[...files].map(file => option(file, catalog.revisions[file]?.[0] || file)));
  if (files.has(previous)) select.value = previous;
  byId("preview").disabled = !files.size;
  byId("discard").disabled = !draft.size;
}

function field(parent, title, values, initial) {
  const label = node("label", title);
  const input = values ? node("select") : node("input");
  if (values) input.append(...values.map(value => option(value)));
  if (initial !== undefined) input.value = initial;
  label.append(input);
  parent.append(label);
  return input;
}

function structure(title, file, ready, build) {
  const panel = node("details");
  panel.className = "panel";
  panel.append(node("summary", title));
  const fileName = catalog.revisions[file]?.[0];
  if (fileName) panel.append(node("p", fileName));
  if (!ready) {
    panel.append(node("p", catalog.errors[file] || "Сначала инициализируйте конфигурацию."));
    return panel;
  }
  const box = node("div");
  box.className = "toolbar";
  const describe = build(box, panel);
  const button = node("button", "Предпросмотр");
  box.append(button);
  const issue = node("p", "");
  issue.setAttribute("role", "alert");
  button.addEventListener("click", () => preview(file, [describe()], issue));
  panel.append(box, issue);
  return panel;
}

function renderStructures() {
  const host = byId("structures");
  host.replaceChildren();
  if (byId("search").value || byId("changed").checked) return;
  const scope = byId("scope").value;
  if (!scope || scope === "Проект") {
    host.append(structure("Фазы пайплайна", "pipeline", catalog.policy_ready, (box, panel) => {
      const action = field(box, "Действие", ["enable", "disable", "add"]);
      const existing = field(box, "Фаза", catalog.phases.map(phase => phase.id));
      const phase = field(box, "ID новой фазы");
      const enabled = field(box, "Условие включения");
      const skill = field(box, "Скилл");
      const gates = field(box, "Гейты через пробел");
      const description = field(box, "Описание");
      const after = field(box, "После фазы", ["", ...catalog.phases.map(item => item.id)]);
      const info = node("p");
      panel.append(info, tooltip("enable включает фазу, disable выключает, add добавляет фазу. Условие — true, false или ключ политики, например jira.enabled. Для изменения обязательной фазы действует подтверждение Forge."));
      const update = () => {
        const adding = action.value === "add";
        existing.parentElement.hidden = adding;
        for (const input of [phase, skill, gates, description, after]) input.parentElement.hidden = !adding;
        enabled.parentElement.hidden = action.value === "disable";
        const current = catalog.phases.find(item => item.id === existing.value);
        info.textContent = !adding && current ?
          current.description + " · условие: " + display(current.enabled_by ?? true) : "";
      };
      action.addEventListener("change", update);
      existing.addEventListener("change", update);
      update();
      return () => ({op: "phase", action: action.value, phase_id: action.value === "add" ? phase.value : existing.value,
        enabled_by: enabled.parentElement.hidden ? "" : enabled.value, skill: skill.value,
        gates: gates.value, desc: description.value, after: after.value});
    }));
  }
  if (!scope || scope === "Установка") {
    host.append(structure("Списки рисков и лимиты ролей", "risk", catalog.risk_ready, (box, panel) => {
      const action = field(box, "Действие", ["list-add", "list-remove", "cap-set"]);
      const list = field(box, "Список", Object.keys(catalog.risk_lists));
      const entry = field(box, "Паттерн");
      const item = field(box, "Запись списка", []);
      const role = field(box, "Паттерн роли");
      const roles = node("datalist");
      roles.id = "roles";
      roles.append(...Object.keys(catalog.caps).filter(key => catalog.levels.includes(catalog.caps[key]))
        .map(key => option(key, catalog.caps[key])));
      role.setAttribute("list", roles.id);
      box.append(roles);
      const level = field(box, "Лимит роли", catalog.levels);
      panel.append(tooltip("list-add добавляет паттерн. list-remove убирает выбранную запись. cap-set задаёт лимит роли от R0 до R5. Для удаления или изменения лимита действует подтверждение Forge."));
      const update = () => {
        const caps = action.value === "cap-set";
        const removing = action.value === "list-remove";
        list.parentElement.hidden = caps;
        entry.parentElement.hidden = caps || removing;
        item.parentElement.hidden = !removing;
        role.parentElement.hidden = level.parentElement.hidden = !caps;
        item.replaceChildren(...(catalog.risk_lists[list.value] || []).map(value => option(value)));
      };
      action.addEventListener("change", update);
      list.addEventListener("change", update);
      update();
      return () => ({op: "risk", action: action.value,
        key: action.value === "cap-set" ? role.value : list.value,
        value: action.value === "cap-set" ? level.value : action.value === "list-remove" ? item.value : entry.value});
    }));
  }
}

function render() {
  const query = byId("search").value.trim().toLowerCase();
  const scope = byId("scope").value;
  const groups = new Map();
  for (const row of catalog.rows) {
    if (scope && row.scope !== scope) continue;
    if (byId("changed").checked && !draft.has(row.target + "/" + row.id)) continue;
    if (query && ![row.title, row.id, ...(row.aliases || []), ...(row.deprecated_aliases || [])]
      .some(term => term.toLowerCase().includes(query))) continue;
    if (!groups.has(row.category)) groups.set(row.category, []);
    groups.get(row.category).push(rowElement(row));
  }
  const settings = byId("settings");
  settings.replaceChildren(...[...groups].flatMap(([group, rows]) =>
    [node("h2", categories[group] || group), ...rows]));
  if (!groups.size) settings.append(node("p", "Нет подходящих настроек."));
  renderStructures();
  updateFooter();
}

async function preview(file, changes, issue) {
  if (!file) return;
  issue.textContent = "";
  try {
    const revision = changes[0].op === "set" ? draftRevisions.get(file) : catalog.revisions[file];
    confirmation = await request("/api/preview", {changes, revision, feature: byId("feature").value});
    confirmation.fileKey = changes[0].op === "set" ? file : null;
    byId("target").textContent = confirmation.file;
    byId("diff").replaceChildren(...confirmation.diff.map(change => {
      const block = node("section");
      block.append(node("h3", change.path), node("pre", "Было\n" + display(change.old) +
        "\n\nСтанет\n" + display(change.new)));
      return block;
    }));
    byId("save-error").textContent = "";
    byId("confirmation").showModal();
  } catch (error) {
    if (error.field) {
      fieldErrors.set(error.field, error.message);
      render();
    }
    issue.textContent = error.field ? "Проверьте «" + error.field + "». " + error.message : error.message;
  }
}

byId("preview").addEventListener("click", () => {
  const file = byId("files").value;
  preview(file, [...draft.values()].filter(change => change.file === file), byId("status"));
});
byId("close").addEventListener("click", () => byId("confirmation").close());
byId("save").addEventListener("click", async () => {
  byId("save").disabled = true;
  try {
    await request("/api/save", {token: confirmation.token});
    for (const [key, change] of draft) if (change.file === confirmation.fileKey) draft.delete(key);
    byId("confirmation").close();
    fieldErrors.clear();
    report("Изменения сохранены");
    await load();
  } catch (error) {
    if (byId("confirmation").open) byId("save-error").textContent = error.message;
    else report(error.message, true);
  } finally {
    byId("save").disabled = false;
  }
});
byId("discard").addEventListener("click", () => {
  draft.clear();
  draftRevisions.clear();
  fieldErrors.clear();
  report("");
  render();
});
byId("reload").addEventListener("click", async () => {
  try {
    await load();
    for (const [file, old] of draftRevisions) {
      const current = catalog.revisions[file];
      if (current && old[0] === current[0]) draftRevisions.set(file, current);
    }
    report(draft.size ? "Файлы перечитаны. Проверьте черновик перед сохранением." : "Файлы перечитаны");
  } catch (error) {
    report(error.message, true);
  }
});
byId("feature").addEventListener("change", async () => {
  const current = catalog.selected || "";
  if ([...draft.values()].some(change => change.file === "manifest")) {
    byId("feature").value = current;
    report("Сохраните или отмените правки фичи перед переключением.");
    return;
  }
  try {
    await load();
  } catch (error) {
    byId("feature").value = current;
    report(error.message, true);
  }
});
for (const id of ["scope", "changed", "search"]) byId(id).addEventListener("input", () => {
  if (catalog) render();
});
load().catch(error => report(error.message, true));
