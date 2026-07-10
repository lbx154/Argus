import React, { useMemo, useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';

import type { ProjectRow } from '../api.js';
import { rankProjects } from '../../../core/src/projects.js';
import { theme } from '../theme.js';
import { useTerminalSize } from '../useTerminalSize.js';
import { Wordmark } from './Wordmark.js';

export function ResumePicker({
  projects,
  onSelect,
}: {
  projects: ProjectRow[];
  onSelect: (project: ProjectRow) => void;
}) {
  const { exit } = useApp();
  const terminal = useTerminalSize();
  const rows = useMemo(() => rankProjects(projects), [projects]);
  const [selected, setSelected] = useState(0);
  const pageSize = Math.max(4, Math.min(12, terminal.rows - 8));
  const page = Math.floor(selected / pageSize);
  const shown = rows.slice(page * pageSize, (page + 1) * pageSize);

  useInput((input, key) => {
    if (key.escape || (key.ctrl && (input === 'c' || input === 'd'))) {
      exit();
      return;
    }
    if (key.upArrow || input === 'k') {
      setSelected((current) => Math.max(0, current - 1));
      return;
    }
    if (key.downArrow || input === 'j') {
      setSelected((current) => Math.min(rows.length - 1, current + 1));
      return;
    }
    if (key.pageUp) {
      setSelected((current) => Math.max(0, current - pageSize));
      return;
    }
    if (key.pageDown) {
      setSelected((current) => Math.min(rows.length - 1, current + pageSize));
      return;
    }
    if (key.return && rows[selected]) onSelect(rows[selected]);
  });

  return (
    <Box flexDirection="column" paddingX={1} width={terminal.columns}>
      <Wordmark />
      <Box marginTop={1} marginBottom={1}>
        <Text bold>Resume a conversation</Text>
        <Text dimColor>{`  ${rows.length} project${rows.length === 1 ? '' : 's'}`}</Text>
      </Box>
      {shown.map((project, index) => {
        const absolute = page * pageSize + index;
        const focused = absolute === selected;
        const label = project.label || project.display_name || project.id;
        return (
          <Box key={project.id}>
            <Text color={focused ? theme.accent : 'gray'}>{focused ? '› ' : '  '}</Text>
            <Text color={project.daemon_alive ? theme.success : 'gray'}>
              {project.daemon_alive ? '● ' : '○ '}
            </Text>
            <Text bold={focused} color={focused ? theme.accent : undefined}>
              {label.slice(0, Math.max(12, terminal.columns - 28))}
            </Text>
            <Text dimColor>{`  ${project.id.slice(0, 12)}`}</Text>
          </Box>
        );
      })}
      <Box marginTop={1}>
        <Text dimColor>
          {`↑/↓ select · PgUp/PgDn page · Enter resume · Esc quit${rows.length > pageSize ? ` · page ${page + 1}/${Math.ceil(rows.length / pageSize)}` : ''}`}
        </Text>
      </Box>
    </Box>
  );
}
