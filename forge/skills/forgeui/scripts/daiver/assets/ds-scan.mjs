#!/usr/bin/env node
// ds-scan.mjs — НЕОБЯЗАТЕЛЬНЫЙ движок разбора stories/деклараций для daiver_ds_requirements (задача 2.2, engine: "node").
//
// Основной путь сканера — чистый Python (daiver/ds/*), этот хелпер ему не нужен. Он даёт те же ФАКТЫ (схема —
// daiver/ds/facts.py), но разбирает файлы настоящим парсером `typescript` ФРОНТ-РЕПО: его берёт
// createRequire(<repo>/package.json), своего typescript здесь нет. Нет node или typescript в репо — Python-обёртка
// (daiver/ds_node.py) отвечает именованным отказом, а не молчаливым откатом на другой движок.
//
// Вход (stdin, JSON):  {"root": "<абсолютный путь репо>", "files": [{"path": "rel/posix", "kind": "stories|types|source"}]}
// Выход (stdout, JSON): {"ts": "<версия typescript>", "files": [{"path", "sha256", "facts": FileFacts}]}
//   ошибка: {"error": "<код>", "message": "<текст>"} и код выхода 3 (нет typescript) / 4 (сбой файла/входа).
// MDX сюда не приходит: TypeScript его не разбирает, MDX всегда читает Python.
//
// Правила разбора ТЕ ЖЕ, что у Python-движка (см. docstring daiver/ds/{csf,decl,typeast,value}.py); отличия
// названы в «Результате» задачи 2.2. Проверено паритетом на фикстуре fixtures/fake-ds (test_ds_requirements.py,
// NodeEngineTests: документы побайтно совпадают, кроме source.scanner), не на боевой библиотеке.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

let ts = null;
let K = null;          // ts.SyntaxKind

function fail(code, error, message) {
  process.exitCode = code;
  process.stdout.write(JSON.stringify({ error, message }));
}

// ── общие мелочи ─────────────────────────────────────────────────────────────

const COMPONENT_WRAPPERS = new Set(['FC', 'FunctionComponent', 'VFC', 'VoidFunctionComponent', 'SFC',
  'StatelessComponent', 'ComponentType', 'ComponentClass', 'ForwardRefExoticComponent', 'ExoticComponent',
  'NamedExoticComponent', 'MemoExoticComponent', 'LazyExoticComponent', 'JSXElementConstructor']);
const ELEMENT_NAMES = new Set(['Element', 'ReactElement', 'ReactNode', 'ReactChild', 'ReactPortal']);
const CLASS_BASES = new Set(['Component', 'PureComponent']);
const HOC_CALLS = new Set(['memo', 'forwardRef', 'observer']);
const MAX_TEXT = 120;
const MAX_USAGE_ELEMENTS = 400;
const ALWAYS_EXCLUDED = new Set(['__namedExportsOrder']);

const OPAQUE = () => ({ $: 'opaque' });
const REF = (name) => ({ $: 'ref', name });
const JSXV = (text) => ({ $: 'jsx', text });
const isMarker = (v, k) => v !== null && typeof v === 'object' && !Array.isArray(v) && v.$ === k;
const isScalar = (v) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean';
const last = (name) => name.split('.').pop();
const upperFirst = (s) => s.length > 0 && s[0] !== s[0].toLowerCase();

function cleanText(s) {
  s = s.replace(/\{@(?:link|linkcode|linkplain)\s+([^}|\s]+)(?:[\s|]+([^}]*))?\}/g, (_, a, b) => (b || a).trim());
  return s.replace(/\s+/g, ' ').trim();
}

function commentText(c) {
  if (c === undefined || c === null) return '';
  if (typeof c === 'string') return c;
  return ts.getTextOfJSDocComment(c) || '';
}

// JSDoc ближайшего блока перед узлом → {text, deprecated, default} | null (как facts.doc_fact)
function docFact(node) {
  const blocks = node && node.jsDoc;
  if (!blocks || blocks.length === 0) return null;
  const d = blocks[blocks.length - 1];
  const text = cleanText(commentText(d.comment));
  let deprecated = null;
  let dflt = null;
  for (const tag of d.tags || []) {
    const name = tag.tagName.text;
    const body = cleanText(commentText(tag.comment));
    if (name === 'deprecated' && deprecated === null) deprecated = body;
    if ((name === 'default' || name === 'defaultValue') && dflt === null) dflt = body;
  }
  if (!text && deprecated === null && dflt === null) return null;
  return { text, deprecated, default: dflt };
}

function emptyFacts(file, kind) {
  return { file, kind, imports: {}, groups: [], prose: null, docOf: null, types: {}, components: [], warnings: [] };
}

function nameOf(n) {
  if (!n) return null;
  if (n.kind === K.Identifier || n.kind === K.StringLiteral || n.kind === K.NoSubstitutionTemplateLiteral) return n.text;
  if (n.kind === K.NumericLiteral) return String(Number(n.text));
  return null;
}

function entityText(n) {
  if (n.kind === K.Identifier) return n.text;
  if (n.kind === K.PropertyAccessExpression) return entityText(n.expression) + '.' + n.name.text;
  if (n.kind === K.QualifiedName) return entityText(n.left) + '.' + n.right.text;
  return null;
}

function skipOuter(n) {
  while (n && (n.kind === K.ParenthesizedExpression || n.kind === K.AsExpression || n.kind === K.SatisfiesExpression
      || n.kind === K.NonNullExpression || n.kind === K.TypeAssertionExpression)) n = n.expression;
  return n;
}

function hasMod(node, kind) {
  return !!(node.modifiers && node.modifiers.some((m) => m.kind === kind));
}

function isJsx(n) {
  return n.kind === K.JsxElement || n.kind === K.JsxSelfClosingElement || n.kind === K.JsxFragment;
}

// ── типы: TypeNode → упрощённое дерево (typeast.py) ─────────────────────────

const KW = new Map();
function initKinds() {
  K = ts.SyntaxKind;
  for (const [k, v] of [[K.StringKeyword, 'string'], [K.NumberKeyword, 'number'], [K.BooleanKeyword, 'boolean'],
    [K.AnyKeyword, 'any'], [K.UnknownKeyword, 'unknown'], [K.NeverKeyword, 'never'], [K.VoidKeyword, 'void'],
    [K.UndefinedKeyword, 'undefined'], [K.ObjectKeyword, 'object'], [K.SymbolKeyword, 'symbol'],
    [K.BigIntKeyword, 'bigint']]) KW.set(k, v);
}

function otherType(sf, n) {
  const text = n.getText(sf).replace(/\s+/g, ' ').trim();
  return { k: 'other', text: text.length <= MAX_TEXT ? text : text.slice(0, MAX_TEXT - 1) + '…' };
}

function numberOf(text) {
  return Number(text.replace(/_/g, ''));
}

function toType(sf, n) {
  if (!n) return null;
  if (KW.has(n.kind)) return { k: 'kw', v: KW.get(n.kind) };
  switch (n.kind) {
    case K.LiteralType: {
      const l = n.literal;
      if (l.kind === K.StringLiteral || l.kind === K.NoSubstitutionTemplateLiteral) return { k: 'lit', v: l.text };
      if (l.kind === K.NumericLiteral) return { k: 'lit', v: numberOf(l.text) };
      if (l.kind === K.TrueKeyword) return { k: 'lit', v: true };
      if (l.kind === K.FalseKeyword) return { k: 'lit', v: false };
      if (l.kind === K.NullKeyword) return { k: 'kw', v: 'null' };
      if (l.kind === K.PrefixUnaryExpression && l.operator === K.MinusToken && l.operand.kind === K.NumericLiteral) {
        return { k: 'lit', v: -numberOf(l.operand.text) };
      }
      return otherType(sf, n);
    }
    case K.TypeReference:
      return { k: 'ref', name: entityText(n.typeName), args: (n.typeArguments || []).map((a) => toType(sf, a)) };
    case K.ExpressionWithTypeArguments:
      return { k: 'ref', name: entityText(n.expression) || n.expression.getText(sf),
        args: (n.typeArguments || []).map((a) => toType(sf, a)) };
    case K.UnionType:
      return { k: 'union', items: n.types.map((t) => toType(sf, t)) };
    case K.IntersectionType:
      return { k: 'inter', items: n.types.map((t) => toType(sf, t)) };
    case K.ParenthesizedType:
      return toType(sf, n.type);
    case K.ArrayType:
      return { k: 'arr', item: toType(sf, n.elementType) };
    case K.TupleType:
      return { k: 'tuple' };
    case K.TemplateLiteralType:
      return { k: 'tpl' };
    case K.FunctionType:
    case K.ConstructorType:
      return { k: 'fn', params: n.kind === K.FunctionType ? n.parameters.map((p) => (p.type ? toType(sf, p.type) : null)) : [],
        ret: n.kind === K.FunctionType && n.type ? toType(sf, n.type) : null };
    case K.TypeLiteral:
      return Object.assign({ k: 'obj' }, membersOf(sf, n.members));
    case K.TypeOperator:
      if (n.operator === K.ReadonlyKeyword) return toType(sf, n.type);
      return otherType(sf, n);
    default:
      return otherType(sf, n);
  }
}

// члены интерфейса / объектного типа → {members, open}
function membersOf(sf, list) {
  const out = [];
  let open = false;
  for (const m of list) {
    if (m.kind === K.IndexSignature) { open = true; continue; }
    if (m.kind !== K.PropertySignature && m.kind !== K.MethodSignature && m.kind !== K.PropertyDeclaration
        && m.kind !== K.MethodDeclaration && m.kind !== K.GetAccessor) continue;
    const name = nameOf(m.name);
    if (name === null) continue;
    const method = m.kind === K.MethodSignature || m.kind === K.MethodDeclaration;
    out.push({ name, optional: !!m.questionToken, method,
      type: method ? { k: 'fn', params: [], ret: null } : (m.type ? toType(sf, m.type) : null), doc: docFact(m) });
  }
  return { members: out, open };
}

// ── декларации: interface / type / enum / компоненты (decl.py) ───────────────

function isElementType(a) {
  if (!a) return false;
  if (a.k === 'ref') return ELEMENT_NAMES.has(last(a.name));
  if (a.k === 'union') return a.items.some(isElementType);
  return false;
}

function componentType(a, depth = 0) {
  if (!a || depth > 4) return [false, null];
  if (a.k === 'ref') {
    const l = last(a.name);
    if (!COMPONENT_WRAPPERS.has(l)) return [false, null];
    if (!a.args.length) return [true, null];
    const inner = a.args[0];
    if (l === 'MemoExoticComponent' || l === 'LazyExoticComponent') {
      const r = componentType(inner, depth + 1);
      if (r[0]) return r;
    }
    return [true, inner];
  }
  if (a.k === 'fn') {
    const params = a.params || [];
    if (params.length <= 1 && isElementType(a.ret)) return [true, params.length ? params[0] : null];
  }
  if (a.k === 'union') {
    for (const it of a.items) {
      const r = componentType(it, depth + 1);
      if (r[0]) return r;
    }
  }
  return [false, null];
}

function containsJsx(node) {
  let found = false;
  (function walk(n) {
    if (found) return;
    if (isJsx(n)) { found = true; return; }
    ts.forEachChild(n, walk);
  })(node);
  return found;
}

function looksLikeComponent(props, ret, kind, node) {
  if (isElementType(ret)) return true;
  if (props && props.k === 'ref' && last(props.name).endsWith('Props')) return true;
  return kind === 'source' && containsJsx(node);
}

function sigOf(sf, fn) {
  const p = fn.parameters && fn.parameters[0];
  return { props: p && p.type ? toType(sf, p.type) : null, ret: fn.type ? toType(sf, fn.type) : null };
}

function initComponent(sf, init, kind, depth = 0) {
  if (!init || depth > 3) return [false, null];
  init = skipOuter(init);
  if (init.kind === K.CallExpression) {
    const callee = entityText(init.expression);
    if (!callee || !HOC_CALLS.has(last(callee))) return [false, null];
    const l = last(callee);
    const targs = (init.typeArguments || []).map((t) => toType(sf, t));
    if (l === 'forwardRef' && targs.length >= 2) return [true, targs[1]];
    if ((l === 'memo' || l === 'observer') && targs.length) return [true, targs[0]];
    const arg = init.arguments[0];
    if (arg) {
      const r = initComponent(sf, arg, kind, depth + 1);
      if (r[0]) return r;
      if (kind === 'source' && containsJsx(init)) return [true, null];
    }
    return [false, null];
  }
  if (init.kind === K.ArrowFunction || init.kind === K.FunctionExpression) {
    const { props, ret } = sigOf(sf, init);
    return looksLikeComponent(props, ret, kind, init) ? [true, props] : [false, null];
  }
  return [false, null];
}

function binding(name, props, doc, module) {
  return { name, props, doc, module };
}

function laterExports(sf) {
  const names = new Set();
  for (const s of sf.statements) {
    if (s.kind === K.ExportDeclaration && !s.moduleSpecifier && s.exportClause && s.exportClause.elements) {
      for (const e of s.exportClause.elements) names.add((e.propertyName || e.name).text);
    } else if (s.kind === K.ExportAssignment && !s.isExportEquals && s.expression.kind === K.Identifier) {
      names.add(s.expression.text);
    }
  }
  return names;
}

function addInterface(sf, n, facts) {
  const ext = (n.heritageClauses || []).filter((h) => h.token === K.ExtendsKeyword)
    .flatMap((h) => h.types.map((t) => toType(sf, t)));
  const m = membersOf(sf, n.members);
  const decl = { kind: 'interface', extends: ext, members: m.members, open: m.open, type: null, enum: null, doc: docFact(n) };
  const old = facts.types[n.name.text];
  if (old && old.kind === 'interface') {
    old.members.push(...decl.members);
    old.extends.push(...decl.extends);
    old.open = old.open || decl.open;
    old.doc = old.doc || decl.doc;
  } else {
    facts.types[n.name.text] = decl;
  }
}

function addEnum(n, facts, env) {
  const out = {};
  let nxt = 0;
  for (const m of n.members) {
    const name = nameOf(m.name);
    if (name === null) continue;
    const v = m.initializer ? env.ev(m.initializer) : nxt;
    out[name] = v;
    nxt = typeof v === 'number' && Number.isInteger(v) ? v + 1 : 0;
  }
  facts.types[n.name.text] = { kind: 'enum', extends: [], members: [], open: false, type: null, enum: out, doc: docFact(n) };
}

function walkDecls(sf, list, facts, module, ambient, later, kind, env) {
  for (const s of list) {
    const exported = hasMod(s, K.ExportKeyword) || hasMod(s, K.DefaultKeyword);
    const declared = hasMod(s, K.DeclareKeyword);
    const visible = (name) => ambient || module !== null || exported || declared || later.has(name);
    if (s.kind === K.InterfaceDeclaration) {
      addInterface(sf, s, facts);
    } else if (s.kind === K.TypeAliasDeclaration) {
      facts.types[s.name.text] = { kind: 'alias', extends: [], members: [], open: false, type: toType(sf, s.type),
        enum: null, doc: docFact(s) };
    } else if (s.kind === K.EnumDeclaration) {
      addEnum(s, facts, env);
    } else if (s.kind === K.VariableStatement) {
      s.declarationList.declarations.forEach((d, i) => {
        if (d.name.kind !== K.Identifier) return;
        const name = d.name.text;
        if (!upperFirst(name) || !visible(name)) return;
        let found = false;
        let props = null;
        if (d.type) [found, props] = componentType(toType(sf, d.type));
        if (!found && d.initializer) [found, props] = initComponent(sf, d.initializer, kind);
        if (found) facts.components.push(binding(name, props, docFact(i === 0 ? s : d), module));
      });
    } else if (s.kind === K.FunctionDeclaration) {
      if (!s.name) continue;
      const name = s.name.text;
      if (!upperFirst(name) || !visible(name)) continue;
      const { props, ret } = sigOf(sf, s);
      if (looksLikeComponent(props, ret, kind, s)) facts.components.push(binding(name, props, docFact(s), module));
    } else if (s.kind === K.ClassDeclaration) {
      if (!s.name) continue;
      const name = s.name.text;
      if (!upperFirst(name) || !visible(name)) continue;
      const ext = (s.heritageClauses || []).find((h) => h.token === K.ExtendsKeyword);
      if (!ext || !ext.types.length) continue;
      const base = toType(sf, ext.types[0]);
      if (base.k === 'ref' && CLASS_BASES.has(last(base.name))) {
        facts.components.push(binding(name, base.args.length ? base.args[0] : null, docFact(s), module));
      }
    } else if (s.kind === K.ModuleDeclaration) {
      const mod = s.name.kind === K.StringLiteral ? s.name.text : module;
      let body = s.body;
      while (body && body.kind === K.ModuleDeclaration) body = body.body;     // namespace A.B { … }
      if (body && body.statements) walkDecls(sf, body.statements, facts, mod, ambient, later, kind, env);
    }
  }
}

function scanDecls(sf, file, kind, env) {
  const facts = emptyFacts(file, kind);
  walkDecls(sf, sf.statements, facts, null, kind === 'types', laterExports(sf), kind, env);
  return facts;
}

// ── значения (value.py): окружение файла и статическое вычисление ────────────

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v) && v.$ === undefined;

function makeEnv(sf) {
  const env = { sf, consts: new Map(), enums: new Map(), funcs: new Map(), assigns: new Map(), memo: new Map(), busy: [] };
  env.ev = (n) => evalNode(env, n, 0);
  for (const s of sf.statements) {
    if (s.kind === K.VariableStatement) {
      for (const d of s.declarationList.declarations) {
        if (d.name.kind === K.Identifier && d.initializer) env.consts.set(d.name.text, d.initializer);
      }
    } else if (s.kind === K.FunctionDeclaration && s.name) {
      env.funcs.set(s.name.text, s);
    } else if (s.kind === K.ExpressionStatement && s.expression.kind === K.BinaryExpression
        && s.expression.operatorToken.kind === K.EqualsToken) {
      const l = s.expression.left;                                   // `Primary.args = {…}` (CSF2)
      if (l.kind === K.PropertyAccessExpression && l.expression.kind === K.Identifier) {
        if (!env.assigns.has(l.expression.text)) env.assigns.set(l.expression.text, new Map());
        env.assigns.get(l.expression.text).set(l.name.text, s.expression.right);
      }
    }
  }
  for (const s of sf.statements) {
    if (s.kind === K.EnumDeclaration) env.enums.set(s.name.text, enumMembers(env, s));
  }
  return env;
}

function enumMembers(env, n) {
  const out = {};
  let nxt = 0;
  for (const m of n.members) {
    const name = nameOf(m.name);
    if (name === null) continue;
    const v = m.initializer ? env.ev(m.initializer) : nxt;
    out[name] = v;
    nxt = typeof v === 'number' && Number.isInteger(v) ? v + 1 : 0;
  }
  return out;
}

function constValue(env, name) {
  if (env.memo.has(name)) return env.memo.get(name);
  if (env.busy.includes(name) || env.busy.length > 6) return OPAQUE();
  env.busy.push(name);
  let v;
  try { v = env.ev(env.consts.get(name)); } finally { env.busy.pop(); }
  env.memo.set(name, v);
  return v;
}

function chainNames(n) {
  const names = [];
  while (n.kind === K.PropertyAccessExpression) {
    names.unshift(n.name.text);
    n = n.expression;
  }
  if (n.kind !== K.Identifier) return null;
  names.unshift(n.text);
  return names;
}

function chain(env, names) {
  const root = names[0];
  if (names.length === 1 && (root === 'undefined' || root === 'NaN' || root === 'Infinity')) return OPAQUE();
  if (names.length === 2 && env.assigns.has(root) && env.assigns.get(root).has(names[1])) {
    return env.ev(env.assigns.get(root).get(names[1]));
  }
  let v;
  if (env.consts.has(root)) v = constValue(env, root);
  else if (env.enums.has(root)) v = env.enums.get(root);
  else return REF(names.join('.'));
  for (let i = 1; i < names.length; i++) {
    if (isMarker(v, 'ref')) return REF([v.name, ...names.slice(i)].join('.'));
    if (isObj(v) && Object.prototype.hasOwnProperty.call(v, names[i])) v = v[names[i]];
    else return OPAQUE();
  }
  return v;
}

function propKey(env, name) {
  if (name.kind === K.ComputedPropertyName) {
    const k = env.ev(name.expression);
    return typeof k === 'string' ? k : (typeof k === 'number' ? String(k) : null);
  }
  return nameOf(name);
}

function evalNode(env, n, depth) {
  if (depth > 40) return OPAQUE();
  n = skipOuter(n);
  switch (n.kind) {
    case K.StringLiteral:
    case K.NoSubstitutionTemplateLiteral:
      return n.text;
    case K.NumericLiteral:
      return numberOf(n.text);
    case K.TrueKeyword: return true;
    case K.FalseKeyword: return false;
    case K.NullKeyword: return null;
    case K.PrefixUnaryExpression:
      if ((n.operator === K.MinusToken || n.operator === K.PlusToken) && n.operand.kind === K.NumericLiteral) {
        const v = numberOf(n.operand.text);
        return n.operator === K.MinusToken ? -v : v;
      }
      return OPAQUE();
    case K.Identifier:
      return chain(env, [n.text]);
    case K.PropertyAccessExpression: {
      const names = chainNames(n);
      return names ? chain(env, names) : OPAQUE();
    }
    case K.ObjectLiteralExpression: {
      const out = {};
      for (const p of n.properties) {
        if (p.kind === K.SpreadAssignment) {
          const v = evalNode(env, p.expression, depth + 1);
          if (isObj(v)) Object.assign(out, v);
        } else if (p.kind === K.PropertyAssignment) {
          const key = propKey(env, p.name);
          if (key !== null) out[key] = evalNode(env, p.initializer, depth + 1);
        } else if (p.kind === K.ShorthandPropertyAssignment) {
          out[p.name.text] = chain(env, [p.name.text]);
        } else if (p.kind === K.MethodDeclaration || p.kind === K.GetAccessor || p.kind === K.SetAccessor) {
          const key = propKey(env, p.name);
          if (key !== null) out[key] = OPAQUE();
        }
      }
      return out;
    }
    case K.ArrayLiteralExpression: {
      const out = [];
      for (const el of n.elements) {
        if (el.kind === K.OmittedExpression) continue;
        if (el.kind === K.SpreadElement) {
          const v = evalNode(env, el.expression, depth + 1);
          if (Array.isArray(v)) out.push(...v); else out.push(OPAQUE());
        } else {
          out.push(evalNode(env, el, depth + 1));
        }
      }
      return out;
    }
    case K.CallExpression: {
      const names = chainNames(n.expression);
      if (!names || n.arguments.length !== 1) return OPAQUE();
      const full = names.join('.');
      if (full === 'Object.freeze' || full === 'Object.seal') return evalNode(env, n.arguments[0], depth + 1);
      if (full === 'Object.keys' || full === 'Object.values' || full === 'Object.entries') {
        const v = evalNode(env, n.arguments[0], depth + 1);
        if (isObj(v)) {
          if (full === 'Object.keys') return Object.keys(v);
          if (full === 'Object.values') return Object.values(v);
          return Object.entries(v).map(([k, x]) => [k, x]);
        }
      }
      return OPAQUE();
    }
    case K.JsxElement:
    case K.JsxSelfClosingElement:
    case K.JsxFragment:
      return JSXV(n.getText(env.sf));
    default:
      return OPAQUE();
  }
}

// ── использование компонентов в JSX (collect_usage) ─────────────────────────

function outerJsx(node) {
  const out = [];
  (function walk(n) {
    if (isJsx(n)) { out.push(n); return; }
    ts.forEachChild(n, walk);
  })(node);
  return out;
}

function collectUsage(env, root) {
  const out = [];
  const budget = { n: MAX_USAGE_ELEMENTS };
  const walkExpr = (expr) => { for (const j of outerJsx(expr)) walkEl(j); };
  const walkEl = (el) => {
    if (budget.n <= 0) return;
    budget.n -= 1;
    const props = {};
    const later = [];
    let tag = '';
    if (el.kind !== K.JsxFragment) {
      const open = el.kind === K.JsxElement ? el.openingElement : el;
      tag = open.tagName.getText(env.sf);
      for (const a of open.attributes.properties) {
        if (a.kind !== K.JsxAttribute) continue;
        const name = a.name.getText(env.sf);
        const init = a.initializer;
        if (!init) {
          (props[name] = props[name] || []).push(true);
        } else if (init.kind === K.StringLiteral) {
          (props[name] = props[name] || []).push(init.text);
        } else if (init.kind === K.JsxExpression) {
          if (!init.expression) continue;
          const v = env.ev(init.expression);
          if (isScalar(v)) {
            (props[name] = props[name] || []).push(v);
          } else if (isMarker(v, 'jsx')) {
            later.push(skipOuter(init.expression));
          } else if (isMarker(v, 'opaque') || isMarker(v, 'ref') || Array.isArray(v) || isObj(v)) {
            later.push({ expr: init.expression });
          }
        } else if (isJsx(init)) {
          later.push(init);
        }
      }
    }
    if (tag && upperFirst(tag)) out.push([tag, props]);
    for (const x of later) { if (x.expr) walkExpr(x.expr); else walkEl(x); }
    for (const ch of el.kind === K.JsxFragment || el.kind === K.JsxElement ? el.children : []) {
      if (isJsx(ch)) walkEl(ch);
      else if (ch.kind === K.JsxExpression && ch.expression) walkExpr(ch.expression);
    }
  };
  walkEl(root);
  return out;
}

// ── stories (csf.py) ─────────────────────────────────────────────────────────

function importsOf(sf) {
  const out = {};
  for (const s of sf.statements) {
    if (s.kind !== K.ImportDeclaration || !s.importClause || s.importClause.isTypeOnly) continue;
    const spec = s.moduleSpecifier.text;
    const c = s.importClause;
    if (c.name) out[c.name.text] = { from: spec, named: null, default: true, ns: false };
    const nb = c.namedBindings;
    if (nb && nb.kind === K.NamespaceImport) {
      out[nb.name.text] = { from: spec, named: null, default: false, ns: true };
    } else if (nb) {
      for (const e of nb.elements) {
        if (e.isTypeOnly) continue;
        const imported = (e.propertyName || e.name).text;
        out[e.name.text] = imported === 'default'
          ? { from: spec, named: null, default: true, ns: false }
          : { from: spec, named: imported, default: false, ns: false };
      }
    }
  }
  return out;
}

function objectNode(env, n, depth = 0) {
  n = skipOuter(n);
  if (n.kind === K.ObjectLiteralExpression) return n;
  if (depth > 4) return null;
  if (n.kind === K.Identifier && env.consts.has(n.text)) return objectNode(env, env.consts.get(n.text), depth + 1);
  if (n.kind === K.CallExpression && n.arguments.length && chainNames(n.expression)) {
    return objectNode(env, n.arguments[0], depth + 1);
  }
  return null;
}

function propNode(obj, key) {
  for (const p of obj.properties) {
    if (!p.name || nameOf(p.name) !== key) continue;
    if (p.kind === K.PropertyAssignment) return p.initializer;
    if (p.kind === K.ShorthandPropertyAssignment) return p.name;
    if (p.kind === K.MethodDeclaration) return p;
  }
  return null;
}

function chainText(n) {
  n = skipOuter(n);
  const names = n.kind === K.Identifier ? [n.text] : (n.kind === K.PropertyAccessExpression ? chainNames(n) : null);
  return names ? names.join('.') : null;
}

function docsText(params, key) {
  const v = isObj(params) && isObj(params.docs) && isObj(params.docs.description) ? params.docs.description[key] : null;
  return typeof v === 'string' && v.trim() ? cleanText(v) : null;
}

function findJsx(node) {
  let first = null;
  let ret = null;
  (function walk(n) {
    if (ret) return;
    if (isJsx(n)) { if (!first) first = n; return; }
    if (n.kind === K.ReturnStatement && n.expression) {
      let e = n.expression;
      while (e.kind === K.ParenthesizedExpression) e = e.expression;
      if (isJsx(e)) { ret = e; return; }
    }
    ts.forEachChild(n, walk);
  })(node);
  return ret || first;
}

function nameFilter(env, n) {
  if (!n) return null;
  if (n.kind === K.RegularExpressionLiteral) {
    const m = /^\/(.*)\/([a-z]*)$/s.exec(n.getText(env.sf));
    try { const rx = new RegExp(m[1], m[2].includes('i') ? 'i' : ''); return (s) => rx.test(s); } catch (e) { return null; }
  }
  const v = env.ev(n);
  if (Array.isArray(v) && v.every((x) => typeof x === 'string')) { const set = new Set(v); return (s) => set.has(s); }
  return null;
}

function makeStory(env, exportName, name, format, argsNode, argTypesNode, jsxNode, params) {
  const story = { export: exportName, name, format, args: null, argTypes: null, jsx: null, usage: [],
    component: null, description: docsText(params, 'story') };
  for (const [key, node] of [['args', argsNode], ['argTypes', argTypesNode]]) {
    if (!node) continue;
    const v = env.ev(node);
    if (isObj(v)) story[key] = v;
  }
  if (jsxNode) {
    story.jsx = jsxNode.getText(env.sf);
    story.usage = collectUsage(env, jsxNode);
  }
  return story;
}

function classifyInit(env, init) {
  const n = skipOuter(init);
  if (n.kind === K.ObjectLiteralExpression) return ['object', n];
  if (n.kind === K.ArrowFunction || n.kind === K.FunctionExpression || n.kind === K.FunctionDeclaration) return ['func', n];
  if (n.kind === K.CallExpression) {
    const names = chainNames(n.expression);
    if (names && names.length >= 2 && names[names.length - 1] === 'bind') return ['bind', names.slice(0, -1).join('.')];
    const obj = objectNode(env, n);
    if (obj) return ['object', obj];
  }
  return ['other', null];
}

function namedStory(env, name, init) {
  const [kind, info] = classifyInit(env, init);
  const asg = env.assigns.get(name) || new Map();
  let argsNode = null; let argTypesNode = null; let jsx = null; let sname = null; let params = null; let fmt;
  if (kind === 'object') {
    const val = env.ev(info);
    argsNode = propNode(info, 'args');
    argTypesNode = propNode(info, 'argTypes');
    const rp = propNode(info, 'render');
    if (rp) jsx = findJsx(rp);
    if (isObj(val)) { sname = typeof val.name === 'string' ? val.name : null; params = val.parameters; }
    fmt = 'csf3';
  } else if (kind === 'bind') {
    fmt = 'csf2';
    const tpl = env.consts.get(info) || env.funcs.get(info);
    if (tpl) jsx = findJsx(tpl);
  } else if (kind === 'func') {
    fmt = asg.size ? 'csf2' : 'csf1';
    jsx = findJsx(info);
  } else {
    fmt = 'other';
  }
  if (asg.has('args')) argsNode = asg.get('args');
  if (asg.has('argTypes')) argTypesNode = asg.get('argTypes');
  for (const key of ['storyName', 'name']) {
    if (asg.has(key)) { const v = env.ev(asg.get(key)); if (typeof v === 'string') sname = v; }
  }
  if (asg.has('parameters')) params = env.ev(asg.get('parameters'));
  return makeStory(env, name, sname, fmt, argsNode, argTypesNode, jsx, params);
}

function namedStories(env, obj) {
  const include = obj ? nameFilter(env, propNode(obj, 'includeStories')) : null;
  const exclude = obj ? nameFilter(env, propNode(obj, 'excludeStories')) : null;
  const out = [];
  const seen = new Set();
  const take = (name, node) => {
    if (seen.has(name) || ALWAYS_EXCLUDED.has(name) || (include && !include(name)) || (exclude && exclude(name))) return;
    seen.add(name);
    out.push(namedStory(env, name, node));
  };
  for (const s of env.sf.statements) {
    if (hasMod(s, K.DefaultKeyword)) continue;
    if (s.kind === K.VariableStatement && hasMod(s, K.ExportKeyword)) {
      for (const d of s.declarationList.declarations) {
        if (d.name.kind === K.Identifier && d.initializer) take(d.name.text, d.initializer);
      }
    } else if (s.kind === K.FunctionDeclaration && hasMod(s, K.ExportKeyword) && s.name) {
      take(s.name.text, s);
    } else if (s.kind === K.ExportDeclaration && !s.moduleSpecifier && s.exportClause && s.exportClause.elements) {
      for (const e of s.exportClause.elements) {
        const local = (e.propertyName || e.name).text;
        if (e.name.text === 'default') continue;
        const node = env.consts.get(local) || env.funcs.get(local);
        if (node) take(e.name.text, node);
      }
    }
  }
  return out;
}

function emptyMeta() {
  return { title: null, component: null, args: null, argTypes: null, description: null };
}

function metaFrom(env, obj) {
  const meta = emptyMeta();
  if (!obj) return meta;
  const val = env.ev(obj);
  if (isObj(val)) {
    if (typeof val.title === 'string') meta.title = val.title;
    for (const key of ['args', 'argTypes']) if (isObj(val[key])) meta[key] = val[key];
    meta.description = docsText(val.parameters, 'component');
  }
  const comp = propNode(obj, 'component');
  if (comp) meta.component = chainText(comp);
  return meta;
}

function defaultExport(sf) {
  for (const s of sf.statements) {
    if (s.kind === K.ExportAssignment && !s.isExportEquals) return s.expression;
    if (s.kind === K.ExportDeclaration && !s.moduleSpecifier && s.exportClause && s.exportClause.elements) {
      for (const e of s.exportClause.elements) if (e.name.text === 'default') return e.propertyName || null;
    }
  }
  return null;
}

function leftmostIsIdentifier(n) {
  while (n.kind === K.CallExpression || n.kind === K.PropertyAccessExpression || n.kind === K.ElementAccessExpression
      || n.kind === K.AsExpression || n.kind === K.SatisfiesExpression || n.kind === K.NonNullExpression) n = n.expression;
  return n.kind === K.Identifier;
}

// storiesOf('Группа', module).addParameters({component}).add('имя', () => <X/>, {…})
function applyChain(env, group, methods) {
  for (const m of methods) {
    const args = m.args;
    if (m.name === 'add' && args.length) {
      const name = env.ev(args[0]);
      if (typeof name === 'string') {
        const jsx = args.length > 1 ? findJsx(args[1]) : null;
        const params = args.length > 2 ? env.ev(args[2]) : null;
        group.stories.push(makeStory(env, null, name, 'storiesOf', null, null, jsx, params));
      }
    } else if (m.name === 'addParameters' && args.length) {
      const obj = objectNode(env, args[0]);
      if (!obj) continue;
      const comp = propNode(obj, 'component');
      if (comp && group.meta.component === null) group.meta.component = chainText(comp);
      const val = env.ev(obj);
      const desc = isObj(val) ? docsText(val, 'component') : null;
      if (desc && group.meta.description === null) group.meta.description = desc;
    }
  }
}

function upChain(call) {
  const methods = [];
  let cur = call;
  while (cur.parent && cur.parent.kind === K.PropertyAccessExpression && cur.parent.expression === cur
      && cur.parent.parent && cur.parent.parent.kind === K.CallExpression && cur.parent.parent.expression === cur.parent) {
    methods.push({ name: cur.parent.name.text, args: cur.parent.parent.arguments });
    cur = cur.parent.parent;
  }
  return methods;
}

function storiesOf(env) {
  const calls = [];
  (function walk(n) {
    if (n.kind === K.CallExpression && n.expression.kind === K.Identifier && n.expression.text === 'storiesOf') calls.push(n);
    ts.forEachChild(n, walk);
  })(env.sf);
  const groups = [];
  const byVar = new Map();
  for (const c of calls) {
    const meta = emptyMeta();
    if (c.arguments.length) {
      const title = env.ev(c.arguments[0]);
      if (typeof title === 'string') meta.title = title;
    }
    const group = { meta, stories: [] };
    applyChain(env, group, upChain(c));
    if (c.parent.kind === K.VariableDeclaration && c.parent.initializer === c && c.parent.name.kind === K.Identifier) {
      byVar.set(c.parent.name.text, group);
    }
    groups.push(group);
  }
  if (byVar.size) {
    for (const s of env.sf.statements) {
      if (s.kind !== K.ExpressionStatement) continue;
      const methods = [];
      let cur = s.expression;
      while (cur.kind === K.CallExpression && cur.expression.kind === K.PropertyAccessExpression) {
        methods.unshift({ name: cur.expression.name.text, args: cur.arguments });
        cur = cur.expression.expression;
      }
      if (cur.kind === K.Identifier && byVar.has(cur.text) && methods.length) applyChain(env, byVar.get(cur.text), methods);
    }
  }
  return groups;
}

function scanStories(sf, file, env) {
  const facts = emptyFacts(file, 'stories');
  facts.imports = importsOf(sf);
  const dflt = defaultExport(sf);
  if (dflt) {
    const obj = objectNode(env, dflt);
    if (!obj && leftmostIsIdentifier(dflt)) {
      facts.warnings.push('default export не объектный литерал — мета (title, component) не прочитана');
    }
    facts.groups.push({ meta: metaFrom(env, obj), stories: namedStories(env, obj) });
  }
  facts.groups.push(...storiesOf(env));
  return facts;
}

// ── вход ─────────────────────────────────────────────────────────────────────

function scriptKind(p) {
  if (p.endsWith('.tsx')) return ts.ScriptKind.TSX;
  if (p.endsWith('.ts') || p.endsWith('.mts') || p.endsWith('.cts')) return ts.ScriptKind.TS;
  return ts.ScriptKind.JSX;
}

function main() {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch (e) {
    fail(4, 'bad-input', 'stdin: ожидается JSON {"root", "files"}');
    return;
  }
  try {
    ts = createRequire(path.join(input.root, 'package.json'))('typescript');
  } catch (e) {
    fail(3, 'typescript-not-found', 'в репо не найден пакет typescript (node_modules/typescript): установите зависимости '
      + 'или используйте engine: python');
    return;
  }
  initKinds();
  const files = [];
  for (const f of input.files) {
    // CRLF → LF до хеша и разбора — как в Python (daiver/ds_requirements.read_entries)
    const text = fs.readFileSync(path.join(input.root, f.path)).toString('utf8').replace(/\r\n/g, '\n');
    const buf = Buffer.from(text, 'utf8');
    const sf = ts.createSourceFile(f.path, text, ts.ScriptTarget.Latest, true, scriptKind(f.path));
    let facts;
    let parseError = false;
    try {
      const env = makeEnv(sf);
      facts = f.kind === 'stories' ? scanStories(sf, f.path, env) : scanDecls(sf, f.path, f.kind, env);
    } catch (e) {
      if (e instanceof ReferenceError || e instanceof SyntaxError) throw e;     // баги хелпера — громко
      facts = emptyFacts(f.path, f.kind);
      facts.warnings.push(`файл не разобран (${e.name}) — пропущен`);
      parseError = true;
    }
    files.push({ path: f.path, sha256: crypto.createHash('sha256').update(buf).digest('hex'), facts, parseError });
  }
  process.stdout.write(JSON.stringify({ ts: ts.version, files }));
}

main();
