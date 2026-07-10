import React, { useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import type { CreatedDaemon } from '../api.js';
import {
  daemonDraftValues,
  daemonFormInput,
  newDaemonDraft,
  type NewDaemonDraft,
} from '../newDaemonForm.js';
import { WORDMARK_TAG } from '../theme.js';
import { useTerminalSize } from '../useTerminalSize.js';
import { TAGLINE } from '../soul.js';
import { NewDaemonForm } from './NewDaemonForm.js';
import { Wordmark } from './Wordmark.js';

export interface FirstRunProps {
  createDaemon: (objective: string, name: string) => Promise<CreatedDaemon>;
  onCreated: (daemon: CreatedDaemon) => void;
}

/**
 * Interactive zero-project state. Creation remains deliberate: Enter on an
 * empty line opens an idle Manager workspace; a typed objective starts a
 * campaign. This keeps a fresh install usable without silently spawning work.
 */
export function FirstRun({ createDaemon, onCreated }: FirstRunProps) {
  const { exit } = useApp();
  const terminal = useTerminalSize();
  const [draft, setDraft] = useState<NewDaemonDraft>(() => newDaemonDraft('', 'objective'));

  const submit = async () => {
    if (draft.busy) return;
    const { objective, name } = daemonDraftValues(draft);
    setDraft((current) => ({ ...current, busy: true, error: '' }));
    try {
      onCreated(await createDaemon(objective, name));
    } catch (cause) {
      setDraft((current) => ({
        ...current,
        busy: false,
        error: (cause as Error).message || 'daemon creation failed',
      }));
    }
  };

  useInput((input, key) => {
    if (key.ctrl && (input === 'c' || input === 'd')) {
      exit();
      return;
    }
    const result = daemonFormInput(draft, input, key);
    if (result.intent === 'submit') void submit();
    else if (result.intent === 'cancel') setDraft(newDaemonDraft('', 'objective'));
    else if (result.draft !== draft) setDraft(result.draft);
  });

  return (
    <Box flexDirection="column" paddingX={1} width={terminal.columns}>
      <Box>
        <Wordmark />
        <Text color={WORDMARK_TAG} dimColor>{`  ${TAGLINE}`}</Text>
      </Box>
      <NewDaemonForm
        draft={draft}
        title="No daemons yet — open your first one"
        cancelHint="Esc clear · Ctrl-C quit"
      />
    </Box>
  );
}
