/** Server paths inside the project's workspace, shown relative to it ("demo/c1-review/README.md"). */
export function relativizeWorkspacePaths(text: string, ...roots: Array<string | null | undefined>): string {
  let out = String(text ?? '');
  const prefixes = [...new Set(roots.map((root) => String(root || '').replace(/\/+$/, '')).filter((root) => root.length > 1))]
    .sort((a, b) => b.length - a.length);
  for (const root of prefixes) {
    const escaped = root.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // "<root>/a/b" -> "a/b"; a bare "<root>" -> "项目目录" is left to the caller's wording, so keep it as "./".
    out = out.replace(new RegExp(`(file://)?${escaped}/`, 'g'), '').replace(new RegExp(`(file://)?${escaped}(?![\\w/.-])`, 'g'), './');
  }
  return out;
}
