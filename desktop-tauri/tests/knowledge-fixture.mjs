// Synthetic HTTP data for the real upstream WikiEntry/WikiLibrary components.
// No component mocks, providers, writable knowledge APIs or account discovery.
export function knowledgeFixture(url) {
  const sid = url.searchParams.get('sid') || 's-A';
  // Current ts-core LearningLibrary reads skill catalogs/documents, not the
  // retired vertical/project wiki tabs. Keep exact identities and reject any
  // unknown document; browsing this fixture grants no write/provider endpoint.
  const skills = [
    { library: 'team', source: 'shared', path: 'skills/research/SKILL.md', name: 'Reviewed research lesson', description: 'Reusable research method', updated_at: 1001 },
    { library: 'team', source: 'shared', path: 'wiki/host.md', name: 'Shared host reference', description: 'Host reference', updated_at: 1000 },
  ];
  if (url.pathname === '/api/skill-library') return { body: { items: skills } };
  if (url.pathname === '/api/skill-library/document') {
    const item = skills.find(row => row.library === url.searchParams.get('library') && row.path === url.searchParams.get('path'));
    return item ? { body: { content: `# ${item.name}\n\nSynthetic shared body for ${item.name}.` } }
      : { status: 404, body: { error: 'Synthetic skill document not found' } };
  }
  if (url.pathname === '/api/skill-packs') return { body: { packs: [{ name: 'Synthetic store pack', version: '1.0.0', description: 'Read-only store fixture', needs: [], requires: [], installed_version: null }] } };
  const items = [
    { scope: 'global', vertical: '', path: 'pages/global.md', title: 'Shared host reference', kind: 'fact' },
    { scope: 'vertical', vertical: 'research', path: 'pages/lesson.md', title: 'Reviewed research lesson', kind: 'lesson' },
    { scope: 'project', vertical: 'research', path: 'pages/project.md', title: `Knowledge ${sid}`, kind: 'survey' },
  ].map((item, index) => ({ ...item, root: `synthetic/${item.scope}`, description: `Synthetic ${item.scope} knowledge`,
    updated_at: 1000 + index, source: 'synthetic/project', created: '2026-09-18', reuse_count: index }));
  if (url.pathname === '/api/wiki') return { body: {
    scopes: ['global', 'vertical', 'project'], items, verticals: ['research'], active_vertical: 'research', errors: [],
    libraries: items.map(item => ({ scope: item.scope, vertical: item.vertical, root: item.root,
      index_markdown: `# ${item.scope} index\n\nSynthetic index.`, pages: [item],
      principles: item.scope === 'vertical' ? '1. Read evidence before acting.\n' : null })),
  } };
  if (url.pathname === '/api/wiki/page') {
    const item = items.find(row => ['scope', 'vertical', 'path'].every(key => row[key] === (url.searchParams.get(key) || '')));
    return item ? { body: { ...item, content: `# ${item.title}\n\nSynthetic ${item.scope} body for ${item.scope === 'project' ? sid : 'shared readers'}.`,
      markdown: `# ${item.title}\n`, truncated: false } } : { status: 404, body: { error: 'Synthetic knowledge page not found' } };
  }
  if (url.pathname === '/api/knowledge/feed') return { body: { events: [{
    ts: 1000, kind: 'learned', scope: 'global', vertical: '', path: 'skills/research/SKILL.md',
    title: 'Reviewed research lesson', source_project: 's-A', mission_id: 'synthetic', role: 'reviewer',
    page_kind: 'lesson', note: 'Synthetic upstream learning feed',
  }] } };
  return null;
}
