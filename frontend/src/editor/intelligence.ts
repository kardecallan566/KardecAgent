export type CompletionKind = "keyword" | "symbol" | "file";

export interface EditorCompletion {
  label: string;
  insertText: string;
  detail: string;
  kind: CompletionKind;
}

export interface EditorDiagnostic {
  file: string;
  line: number;
  column: number;
  severity: "error" | "warning" | "info";
  code?: string;
  message: string;
  source: "json" | "ruff" | "eslint";
}

const KEYWORDS: Record<string, string[]> = {
  py: ["and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "False", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass", "raise", "return", "self", "True", "try", "while", "with", "yield", "print"],
  pyi: ["and", "as", "assert", "async", "await", "class", "def", "else", "False", "from", "if", "import", "None", "pass", "raise", "return", "self", "True", "try", "while", "with", "yield"],
  js: ["async", "await", "break", "case", "catch", "class", "const", "continue", "default", "else", "export", "extends", "false", "finally", "for", "from", "function", "if", "import", "let", "new", "null", "return", "super", "switch", "this", "throw", "true", "try", "typeof", "undefined", "var", "while", "console", "log"],
  jsx: ["async", "await", "class", "const", "export", "extends", "false", "function", "import", "let", "null", "return", "this", "true", "undefined", "useState", "useEffect", "useMemo", "useCallback", "console", "log"],
  ts: ["any", "async", "await", "boolean", "class", "const", "enum", "export", "extends", "false", "function", "implements", "import", "interface", "let", "never", "null", "number", "private", "protected", "public", "readonly", "return", "string", "true", "type", "undefined", "unknown", "useState", "useEffect", "Promise"],
  tsx: ["any", "async", "await", "boolean", "class", "const", "enum", "export", "extends", "false", "function", "implements", "import", "interface", "let", "never", "null", "number", "private", "protected", "public", "readonly", "return", "string", "true", "type", "undefined", "unknown", "useState", "useEffect", "useMemo", "Promise"],
  rs: ["as", "async", "await", "const", "crate", "else", "enum", "false", "fn", "for", "if", "impl", "let", "loop", "match", "mod", "mut", "pub", "return", "self", "Self", "static", "struct", "trait", "true", "type", "use", "where", "while"],
  go: ["break", "case", "chan", "const", "continue", "defer", "else", "fallthrough", "for", "func", "go", "if", "import", "interface", "map", "package", "range", "return", "select", "struct", "switch", "type", "var"],
};

const LANGUAGE_LABEL: Record<string, string> = {
  py: "Python", pyi: "Python", js: "JavaScript", jsx: "JavaScript/React",
  ts: "TypeScript", tsx: "TypeScript/React", rs: "Rust", go: "Go",
};

export function getEditorCompletions(
  source: string,
  extension: string,
  cursor: number,
  projectFiles: string[] = [],
  force = false,
): { items: EditorCompletion[]; start: number; end: number } {
  const prefixMatch = source.slice(0, cursor).match(/[A-Za-z_$][\w$]*$/);
  const prefix = prefixMatch?.[0] ?? "";
  const start = cursor - prefix.length;
  if (!force && prefix.length < 2) return { items: [], start, end: cursor };

  const ext = extension.toLowerCase();
  const language = LANGUAGE_LABEL[ext] || "Linguagem";
  const candidates = new Map<string, EditorCompletion>();
  for (const keyword of KEYWORDS[ext] || []) {
    candidates.set(keyword, {
      label: keyword, insertText: keyword, detail: language + " · palavra-chave",
      kind: "keyword",
    });
  }

  const declarations = /\b(?:def|class|function|const|let|var|type|interface|enum|struct|fn|func)\s+([A-Za-z_$][\w$]*)/g;
  for (const match of source.matchAll(declarations)) {
    const name = match[1];
    if (name && !candidates.has(name)) {
      candidates.set(name, {
        label: name, insertText: name, detail: "Símbolo declarado neste arquivo",
        kind: "symbol",
      });
    }
  }

  for (const file of projectFiles) {
    const name = file.split(/[\\/]/).pop() || file;
    const stem = name.replace(/\.[^.]+$/, "");
    if (stem && !candidates.has(stem)) {
      candidates.set(stem, {
        label: stem, insertText: stem, detail: "Arquivo do projeto · " + file,
        kind: "file",
      });
    }
  }

  const normalized = prefix.toLocaleLowerCase();
  const items = [...candidates.values()]
    .filter(item => item.label.toLocaleLowerCase().startsWith(normalized) && item.label !== prefix)
    .sort((a, b) => {
      const rank = (item: EditorCompletion) => item.kind === "symbol" ? 0 : item.kind === "keyword" ? 1 : 2;
      return rank(a) - rank(b) || a.label.localeCompare(b.label);
    })
    .slice(0, 10);
  return { items, start, end: cursor };
}

export function getLiveEditorDiagnostics(
  source: string,
  extension: string,
  file: string,
): EditorDiagnostic[] {
  if (extension.toLowerCase() !== "json") return [];
  try {
    JSON.parse(source);
    return [];
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const position = Number(message.match(/position\s+(\d+)/i)?.[1] ?? 0);
    const bounded = Math.max(0, Math.min(source.length, position));
    const before = source.slice(0, bounded);
    return [{
      file,
      line: before.split("\n").length,
      column: (before.length - before.lastIndexOf("\n")),
      severity: "error",
      code: "JSON",
      message,
      source: "json",
    }];
  }
}

export function parseToolDiagnostics(output: string, file: string): EditorDiagnostic[] {
  const diagnostics: EditorDiagnostic[] = [];
  const ruff = /^(.+?):(\d+):(\d+):\s+([A-Z]\d+)\s+(.+)$/gm;
  for (const match of output.matchAll(ruff)) {
    diagnostics.push({
      file: match[1] || file,
      line: Number(match[2]),
      column: Number(match[3]),
      severity: "warning",
      code: match[4],
      message: match[5],
      source: "ruff",
    });
  }
  const eslint = /^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)(?:\s{2,}([@\w./-]+))?\s*$/gm;
  for (const match of output.matchAll(eslint)) {
    diagnostics.push({
      file,
      line: Number(match[1]),
      column: Number(match[2]),
      severity: match[3] === "error" ? "error" : "warning",
      code: match[5],
      message: match[4],
      source: "eslint",
    });
  }
  return diagnostics.sort((a, b) => a.line - b.line || a.column - b.column);
}
