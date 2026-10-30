import { expect, it } from 'vitest';
import { operatorDecisionCards, questionRole } from '../../../core/src/decisions';

const [card] = operatorDecisionCards([{ id: 't1', operator_decision: {
  id: 'engineer-t1', item_id: 't1', status: 'pending', title: '编写并运行 hello.py',
  question: '请问输出用中文还是英文？', options: [],
} }], [{ id: 't1', title: '编写并运行 hello.py' }]);

it.each(['engineer', 'reviewer', 'planner'])('uses the server answer ID for the %s, including task IDs containing hyphens', role => {
  expect(questionRole({ ...card, id: `${role}-task-with-hyphens`, item_id: 'task-with-hyphens' })).toBe(role);
  expect(questionRole({ ...card, id: role, item_id: '' })).toBe(role);
});
