import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { ChiefNeeds } from '../components/ChiefNeeds';
import { chiefRequest, FIRST_NEEDS, NEEDS } from '../lib/daily';

it('offers every need to tick, the first ones ticked, and nothing that must be typed', () => {
  const html = renderToStaticMarkup(<ChiefNeeds zh onStart={async () => true} />);
  expect(html.match(/type="checkbox"/g)).toHaveLength(NEEDS.length);
  expect(html.match(/checked=""/g)).toHaveLength(FIRST_NEEDS.length);
  expect(html).toContain('可不填');
});

it('asks for the letter the needs name; without directions, has the chief infer them and ask', () => {
  const text = chiefRequest(true, ['progress', 'papers', 'rest'], '');
  expect(text).toContain('各项目的进展');
  expect(text).toContain('arXiv');
  // The wakes are set apart, not asked of the letter.
  expect(text).not.toContain('马上告诉我');
  expect(text).toContain('从我现有的项目推断');
  expect(chiefRequest(false, ['github'], 'agentic RL')).toContain('The directions I follow: agentic RL');
});
