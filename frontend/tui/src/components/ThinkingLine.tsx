import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { spinnerFrame } from '../soul.js';
import { thinkingStatusLine } from '../../../core/src/thinking.js';
import {
  formatStepSeconds,
  stepElapsedS,
  visibleTrail,
  type PhaseStep,
} from '../../../core/src/phaseTrail.js';

/**
 * The live "Argus is working" block shown while a Manager turn is in flight.
 *
 * It used to be a SINGLE line that each new phase overwrote, so the operator
 * could never see what had already happened — the "I can't tell what the system
 * is doing" complaint. Now it renders the append-only trail: every real step
 * (the command it ran, the tool it called, the file it touched) stays on screen
 * with its own duration, the newest one carries the spinner, and finished steps
 * are ticked off. Nothing here is invented: a row appears only when the backend
 * reported a real action.
 */
export function ThinkingLine({
  tick,
  phase,
  elapsedS,
  heartbeat = false,
  quietS = 0,
  steps = [],
  width = 80,
}: {
  tick: number;
  phase: string;
  elapsedS: number;
  heartbeat?: boolean;
  quietS?: number;
  steps?: PhaseStep[];
  width?: number;
}) {
  const spin = spinnerFrame(tick);
  const body = thinkingStatusLine(phase, tick, heartbeat, quietS);
  const rows = visibleTrail(steps);
  const now = Date.now() / 1000;
  const showDetail = width >= 100;

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        {'  '}
        <Text color={theme.role.manager ?? 'magenta'}>{spin}</Text>
        {' '}
        <Text color={theme.role.manager ?? 'magenta'} bold>Your message</Text>
        {'  '}
        <Text color={theme.accent}>{body}</Text>
        <Text dimColor>{`   ${elapsedS}s`}</Text>
      </Text>
      {rows.map((step, index) => {
        const active = index === rows.length - 1 && !step.endedTs;
        const seconds = formatStepSeconds(stepElapsedS(step, now));
        return (
          <Text key={step.id} wrap="truncate-end">
            {'    '}
            <Text color={active ? theme.role[step.role] ?? theme.info : theme.success}>
              {active ? spin : '✓'}
            </Text>
            {' '}
            <Text dimColor={!active}>{step.label}</Text>
            {seconds ? <Text dimColor>{` · ${seconds}`}</Text> : null}
            {showDetail && step.detail && step.detail !== step.label
              ? <Text dimColor>{` · ${step.detail}`}</Text>
              : null}
          </Text>
        );
      })}
      <Text dimColor>{'  Esc stop waiting · /cancel'}</Text>
    </Box>
  );
}
