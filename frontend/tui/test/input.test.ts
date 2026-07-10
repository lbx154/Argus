import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as ed from '../src/input/editor.js';
import * as h from '../src/input/history.js';
import * as sl from '../src/input/slash.js';
import { consumePasteChunk } from '../src/input/paste.js';
import { moveSelection } from '../src/input/selection.js';

// ── editor ────────────────────────────────────────────────────────────────

test('insert at cursor advances the caret', () => {
  let e = ed.EMPTY;
  e = ed.insert(e, 'abc');
  assert.deepEqual(e, { value: 'abc', cursor: 3 });
  e = ed.left(e); // between b and c
  e = ed.insert(e, 'X');
  assert.deepEqual(e, { value: 'abXc', cursor: 3 });
});

test('backspace / deleteForward act at the cursor', () => {
  let e = ed.fromString('abc', 2); // caret between b and c
  e = ed.backspace(e);
  assert.deepEqual(e, { value: 'ac', cursor: 1 });
  e = ed.deleteForward(e); // delete 'c'
  assert.deepEqual(e, { value: 'a', cursor: 1 });
  assert.deepEqual(ed.backspace(ed.EMPTY), ed.EMPTY); // no-op at start
});

test('left/right/home/end clamp and move by code points', () => {
  let e = ed.fromString('hi', 0);
  assert.equal(ed.left(e).cursor, 0); // clamp
  e = ed.end(e);
  assert.equal(e.cursor, 2);
  assert.equal(ed.right(e).cursor, 2); // clamp
  assert.equal(ed.home(e).cursor, 0);
});

test('CJK moves and deletes as ONE unit (no surrogate/codepoint split)', () => {
  let e = ed.fromString('你好世界', 4); // caret at end, 4 code points
  e = ed.left(e); // between 世 and 界
  assert.equal(e.cursor, 3);
  e = ed.backspace(e); // delete 世
  assert.deepEqual(e, { value: '你好界', cursor: 2 });
  // an astral emoji is one unit too
  let g = ed.insert(ed.EMPTY, '🚀');
  assert.equal(g.cursor, 1);
  assert.deepEqual(ed.backspace(g), ed.EMPTY);
});

test('deleteWordBefore eats trailing spaces then the word', () => {
  let e = ed.fromString('run the kernel  ');
  e = ed.deleteWordBefore(e);
  assert.equal(e.value, 'run the ');
  e = ed.deleteWordBefore(e);
  assert.equal(e.value, 'run ');
});

test('killToStart / killToEnd', () => {
  assert.equal(ed.killToEnd(ed.fromString('abcdef', 3)).value, 'abc');
  assert.deepEqual(ed.killToStart(ed.fromString('abcdef', 3)), { value: 'def', cursor: 0 });
});

test('caretSplit exposes before/at/after for rendering', () => {
  assert.deepEqual(ed.caretSplit(ed.fromString('abc', 1)), { before: 'a', at: 'b', after: 'c' });
  assert.deepEqual(ed.caretSplit(ed.fromString('abc', 3)), { before: 'abc', at: '', after: '' });
});

test('bracketed paste preserves multiline text and strips terminal markers', () => {
  const one = consumePasteChunk('\u001b[200~第一行\nsecond line\u001b[201~', false);
  assert.equal(one.handled, true);
  assert.equal(one.active, false);
  assert.equal(one.text, '第一行\nsecond line');

  const start = consumePasteChunk('[200~hello', false);
  assert.equal(start.active, true);
  const newline = consumePasteChunk('', start.active);
  assert.equal(newline.text, '\n');
  const end = consumePasteChunk('world[201~', newline.active);
  assert.equal(end.active, false);
  assert.equal(start.text + newline.text + end.text, 'hello\nworld');
});

test('ordinary single-key input is not mistaken for a paste', () => {
  assert.deepEqual(consumePasteChunk('你', false), {
    handled: false, active: false, text: '你', pasted: false,
  });
  const ime = consumePasteChunk('你好', false);
  assert.equal(ime.handled, true);
  assert.equal(ime.text, '你好');
});

// ── history ─────────────────────────────────────────────────────────────

test('history recalls older lines and restores the live draft', () => {
  let hist = h.EMPTY_HISTORY;
  hist = h.remember(hist, 'first');
  hist = h.remember(hist, 'second');
  // typing a live draft, then Up
  let up = h.older(hist, 'draft-in-progress');
  assert.equal(up.value, 'second');
  up = h.older(up.h, up.value);
  assert.equal(up.value, 'first');
  up = h.older(up.h, up.value); // clamp at oldest
  assert.equal(up.value, 'first');
  // Down back toward the live draft
  let down = h.newer(up.h);
  assert.equal(down.value, 'second');
  down = h.newer(down.h);
  assert.equal(down.value, 'draft-in-progress'); // draft restored
});

test('history dedupes consecutive + ignores empty', () => {
  let hist = h.EMPTY_HISTORY;
  hist = h.remember(hist, 'x');
  hist = h.remember(hist, 'x'); // dupe
  hist = h.remember(hist, '   '); // empty
  assert.deepEqual(hist.entries, ['x']);
});

// ── slash ─────────────────────────────────────────────────────────────────

test('slash completions match names + aliases, only before a space', () => {
  assert.deepEqual(sl.slashCompletions('/dae').map((c) => c.name), ['/daemon', '/daemons']);
  assert.ok(sl.slashCompletions('/q').some((c) => c.name === '/quit')); // via alias /q
  assert.deepEqual(sl.slashCompletions('/daemon start'), []); // arg being typed → no menu
  assert.deepEqual(sl.slashCompletions('hello'), []); // not a slash line
  assert.equal(sl.slashCompletions('/artifact')[0]?.name, '/artifact'); // exact beats /artifacts
});

test('applyCompletion adds a trailing space only for arg commands', () => {
  const daemon = sl.SLASH_COMMANDS.find((c) => c.name === '/daemon')!;
  const help = sl.SLASH_COMMANDS.find((c) => c.name === '/help')!;
  assert.equal(sl.applyCompletion(daemon), '/daemon ');
  assert.equal(sl.applyCompletion(help), '/help');
});

test('parseCommand resolves aliases + splits the argument + flags unknown', () => {
  assert.equal(sl.parseCommand('/q')?.name, '/quit');
  assert.equal(sl.parseCommand('/rm x')?.name, '/skip'); // /rm is an alias of /skip
  const nudge = sl.parseCommand('/nudge fix the framework');
  assert.equal(nudge?.name, '/nudge');
  assert.equal(nudge?.rest, 'fix the framework');
  const unknown = sl.parseCommand('/bogus x');
  assert.equal(unknown?.cmd, null); // unknown → no cmd
  assert.equal(unknown?.name, '/bogus');
  assert.equal(sl.parseCommand('/artifact reports/final paper.md')?.rest, 'reports/final paper.md');
  assert.equal(sl.parseCommand('/new reproduce kernel benchmark')?.rest, 'reproduce kernel benchmark');
  assert.equal(sl.parseCommand('/daemons recursive live')?.rest, 'recursive live');
  assert.equal(sl.parseCommand('/abort')?.name, '/cancel');
  assert.equal(sl.parseCommand('/add build it')?.name, '/task');
});

test('Ink contains every retired Python REPL command surface', () => {
  const required = [
    '/help', '/status', '/roles', '/journal', '/backlog', '/add', '/plan',
    '/stop', '/done', '/note', '/nudge', '/run', '/daemon', '/daemons',
    '/attach', '/resume', '/doctor', '/backend', '/config', '/continuous',
    '/start', '/identity', '/reset', '/skills', '/exit',
  ];
  for (const command of required) {
    assert.notEqual(sl.parseCommand(command)?.cmd, null, command);
  }
});

test('/resume opens the list unless an explicit project is supplied', () => {
  assert.deepEqual(sl.parseResumeTarget(''), { kind: 'list' });
  assert.deepEqual(sl.parseResumeTarget(' list '), { kind: 'list' });
  assert.deepEqual(sl.parseResumeTarget('LIST'), { kind: 'list' });
  assert.deepEqual(sl.parseResumeTarget(' s-paper '), {
    kind: 'project',
    query: 's-paper',
  });
});

test('interactive panel selection wraps in both directions and handles empty lists', () => {
  assert.equal(moveSelection(0, 3, 1), 1);
  assert.equal(moveSelection(2, 3, 1), 0);
  assert.equal(moveSelection(0, 3, -1), 2);
  assert.equal(moveSelection(7, 0, 1), 0);
});

test('event view arguments accept a filter plus plain-text query', () => {
  assert.deepEqual(sl.parseEventViewArgs('attention credentials'), {
    filter: 'attention', query: 'credentials',
  });
  assert.deepEqual(sl.parseEventViewArgs('watch stalled'), {
    filter: 'attention', query: 'stalled',
  });
  assert.deepEqual(sl.parseEventViewArgs('kernel regression'), {
    filter: 'all', query: 'kernel regression',
  });
  assert.deepEqual(sl.parseEventViewArgs(''), { filter: 'all', query: '' });
});

test('didYouMean suggests the closest command', () => {
  assert.equal(sl.didYouMean('/staus'), '/status');
  assert.equal(sl.didYouMean('/doctar'), '/doctor');
  assert.equal(sl.didYouMean('/zzzzzz'), null); // too far → no suggestion
});

test('helpGroups folds aliases and orders sections', () => {
  const groups = sl.helpGroups();
  assert.deepEqual(groups.map((g) => g.group), [
    'Everyday', 'Task management', 'Daemon & diagnostics', 'Configuration', 'Other',
  ]);
  const skip = groups.flatMap((g) => g.rows).find((r) => r.label.startsWith('/skip'));
  assert.ok(skip && skip.label.includes('(= /rm)'));
});
