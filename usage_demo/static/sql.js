// Only whitespace changes: quoted SQL literals and identifiers stay intact.
export function formatSql(sql) {
  const tokens = sql.match(/'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/|\s+|[A-Za-z_][A-Za-z_0-9]*|./g) || [];
  let output = '', depth = 0;
  for (const token of tokens) {
    if (/^\s+$/.test(token)) {
      if (output && !/\s$/.test(output)) output += ' ';
      continue;
    }
    if (token.startsWith('--')) {
      output += token.trimEnd() + '\n';
      continue;
    }
    if (depth === 0 && /^(SELECT|FROM|WHERE|GROUP|HAVING|ORDER|LIMIT|WITH)$/i.test(token)) {
      output = output.trimEnd() + (output.trim() ? '\n' : '');
    }
    output += token;
    if (token === '(') depth++;
    if (token === ')') depth--;
    if (/^SELECT$/i.test(token) && depth === 0) output += '\n  ';
    if (token === ',' && depth === 0) output = output.trimEnd() + '\n  ';
  }
  return output.trim();
}
