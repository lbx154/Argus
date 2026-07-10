import {
  backspace,
  deleteForward,
  deleteWordBefore,
  EMPTY,
  end,
  fromString,
  home,
  insert,
  killToEnd,
  killToStart,
  left,
  right,
  type Edit,
} from './input/editor.js';

export type NewDaemonField = 'name' | 'objective';

export interface NewDaemonDraft {
  name: Edit;
  objective: Edit;
  field: NewDaemonField;
  busy: boolean;
  error: string;
}

export interface DaemonFormKey {
  upArrow?: boolean;
  downArrow?: boolean;
  leftArrow?: boolean;
  rightArrow?: boolean;
  return?: boolean;
  escape?: boolean;
  ctrl?: boolean;
  tab?: boolean;
  backspace?: boolean;
  delete?: boolean;
  meta?: boolean;
}

export interface DaemonFormInputResult {
  draft: NewDaemonDraft;
  intent?: 'submit' | 'cancel';
}

const LIMITS: Record<NewDaemonField, number> = {
  name: 80,
  objective: 4000,
};

export function newDaemonDraft(
  objective = '',
  field: NewDaemonField = objective.trim() ? 'objective' : 'name',
): NewDaemonDraft {
  return {
    name: EMPTY,
    objective: fromString(objective),
    field,
    busy: false,
    error: '',
  };
}

export function daemonDraftValues(draft: NewDaemonDraft): { name: string; objective: string } {
  return {
    name: draft.name.value.trim(),
    objective: draft.objective.value.trim(),
  };
}

function toggleField(field: NewDaemonField): NewDaemonField {
  return field === 'name' ? 'objective' : 'name';
}

function editActive(draft: NewDaemonDraft, operation: (edit: Edit) => Edit): NewDaemonDraft {
  return { ...draft, [draft.field]: operation(draft[draft.field]), error: '' };
}

/** Pure keyboard reducer shared by the first-run screen and the `/new` panel. */
export function daemonFormInput(
  draft: NewDaemonDraft,
  input: string,
  key: DaemonFormKey,
): DaemonFormInputResult {
  if (draft.busy) return { draft };
  if (key.escape) return { draft, intent: 'cancel' };
  if (key.return) return { draft, intent: 'submit' };
  if (key.tab || key.upArrow || key.downArrow) {
    return { draft: { ...draft, field: toggleField(draft.field) } };
  }
  if (key.leftArrow || (key.ctrl && input === 'b')) return { draft: editActive(draft, left) };
  if (key.rightArrow || (key.ctrl && input === 'f')) return { draft: editActive(draft, right) };
  if (key.ctrl && input === 'a') return { draft: editActive(draft, home) };
  if (key.ctrl && input === 'e') return { draft: editActive(draft, end) };
  if (key.ctrl && input === 'w') return { draft: editActive(draft, deleteWordBefore) };
  if (key.ctrl && input === 'u') return { draft: editActive(draft, killToStart) };
  if (key.ctrl && input === 'k') return { draft: editActive(draft, killToEnd) };
  if (key.backspace) return { draft: editActive(draft, backspace) };
  if (key.delete) return { draft: editActive(draft, deleteForward) };
  if (input && !key.ctrl && !key.meta) {
    return {
      draft: editActive(draft, (current) => {
        const room = LIMITS[draft.field] - Array.from(current.value).length;
        return room > 0 ? insert(current, Array.from(input).slice(0, room).join('')) : current;
      }),
    };
  }
  return { draft };
}
