interface CreatedDaemon {
  sid: string;
  [key: string]: unknown;
}

interface DaemonCreationClient {
  createDaemon: (
    objective: string,
    name?: string,
    workdir?: string,
  ) => Promise<CreatedDaemon>;
  setContinuous: (
    sid: string,
    enabled: boolean,
    objective?: string,
  ) => Promise<unknown>;
}

/** Create/select can finish before the expensive Manager campaign handoff. */
export async function createSessionFast(
  client: DaemonCreationClient,
  name: string,
  objective: string,
  workdir = '',
): Promise<{
  created: CreatedDaemon;
  startCampaign: (() => Promise<unknown>) | null;
}> {
  const created = await client.createDaemon('', name, workdir);
  const body = objective.trim();
  return {
    created,
    startCampaign: body
      ? () => client.setContinuous(created.sid, true, body)
      : null,
  };
}
