import React, { useEffect, useState } from 'react';
import { Box, Text } from 'ink';
import { caretSplit, type Edit } from '../input/editor.js';
import { daemonDraftValues, type NewDaemonDraft, type NewDaemonField } from '../newDaemonForm.js';
import { SPINNER, theme } from '../theme.js';

function FormField({
  field,
  label,
  edit,
  active,
}: {
  field: NewDaemonField;
  label: string;
  edit: Edit;
  active: boolean;
}) {
  const { before, at, after } = caretSplit(edit);
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={active ? theme.accent : undefined} bold={active} dimColor={!active}>
        {`${active ? '›' : ' '} ${label} (optional)`}
      </Text>
      <Box paddingLeft={2}>
        {active ? (
          <>
            <Text>{before}</Text>
            {at ? <Text inverse>{at}</Text> : <Text color={theme.accent}>▏</Text>}
            <Text>{after}</Text>
          </>
        ) : edit.value ? (
          <Text>{edit.value}</Text>
        ) : (
          <Text dimColor>{field === 'name' ? 'generated automatically' : 'start with a conversation'}</Text>
        )}
      </Box>
    </Box>
  );
}

export function NewDaemonForm({
  draft,
  title = '/new — open a fresh daemon',
  cancelHint = 'Esc/Ctrl-C cancel',
}: {
  draft: NewDaemonDraft;
  title?: string;
  cancelHint?: string;
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!draft.busy) return;
    const id = setInterval(() => setTick((value) => value + 1), 90);
    return () => clearInterval(id);
  }, [draft.busy]);

  const { objective } = daemonDraftValues(draft);
  const armed = Boolean(objective);
  const action = armed ? 'create & start' : 'create idle daemon';

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={2} marginTop={1}>
      <Text bold color={theme.accent}>{title}</Text>
      <Text dimColor>A clean Manager context with its own project timeline.</Text>
      <FormField field="name" label="Name" edit={draft.name} active={draft.field === 'name'} />
      <FormField field="objective" label="Objective" edit={draft.objective} active={draft.field === 'objective'} />
      <Box flexDirection="column" marginTop={1}>
        <Text color={armed ? theme.accent : theme.info}>
          {armed ? '● Campaign starts immediately' : '○ Idle until you message Argus'}
        </Text>
        <Text dimColor>
          {armed
            ? 'The objective is persisted, continuous mode is armed, and the executor starts.'
            : 'No executor is spawned yet; your first message can reply or dispatch work.'}
        </Text>
      </Box>
      <Box marginTop={1}>
        {draft.error ? (
          <Text color={theme.error}>{`Could not create daemon · ${draft.error} · Enter to retry`}</Text>
        ) : draft.busy ? (
          <Text color={theme.accent}>{`${SPINNER[tick % SPINNER.length]} ${armed ? 'creating daemon + starting campaign…' : 'creating idle daemon…'}`}</Text>
        ) : (
          <Text dimColor>{`Tab/↑↓ field · Enter ${action} · ${cancelHint}`}</Text>
        )}
      </Box>
      <Text> </Text>
    </Box>
  );
}
