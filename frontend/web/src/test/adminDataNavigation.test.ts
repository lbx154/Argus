import { describe, expect, it, vi } from 'vitest';
import { adminProjectURL, automaticProjectSelection, openMatchingUserProject } from '../admin-data/navigation';
import { parseTaskLink, projectKey } from '../admin-data/model';
import type { CollaborationProject } from '../admin-data/types';

const project = (sid: string, tenant = 'trial-11'): CollaborationProject => ({ tenant_id: tenant, sid, title: 'Same title', eligible: true });

describe('administrator project navigation', () => {
  it('clears another project’s task when a search selects the original linked project later', () => {
    const a = project('s-a'), b = project('s-b');
    const originalLink = { tenant: 'trial-11', sid: 's-a', taskId: 'task-a' };
    const switched = automaticProjectSelection([a], projectKey(b), originalLink, false)!;
    expect(switched).toEqual({ key: projectKey(a), taskId: '' });
    const previousURL = 'https://argus.test/admin/data?tenant=trial-02&sid=s-b&task_id=task-b&task=legacy-b';
    const location = adminProjectURL(previousURL, a, switched.taskId);
    expect(location).toBe('/admin/data?tenant=trial-11&sid=s-a');
    expect(parseTaskLink(new URL(location, 'https://argus.test').search)).toEqual({ tenant: 'trial-11', sid: 's-a', taskId: null });
    const initial = automaticProjectSelection([a], '', originalLink, true)!;
    expect(initial).toEqual({ key: projectKey(a), taskId: 'task-a' });
    expect(adminProjectURL(previousURL, a, initial.taskId)).toBe('/admin/data?tenant=trial-11&sid=s-a&task_id=task-a');
    expect(automaticProjectSelection([a], projectKey(a), originalLink, false)).toBeNull();
    expect(automaticProjectSelection([b], '', originalLink, true)).toBeNull();
  });

  it('rechecks the current user on every click and never navigates using an old matching identity', async () => {
    const target = project('s-a');
    const readIdentity = vi.fn()
      .mockResolvedValueOnce({ key_id: 'trial-11', role: 'trial', readonly: false })
      .mockResolvedValueOnce({ key_id: 'trial-02', role: 'trial', readonly: false });
    const navigate = vi.fn();
    expect((await openMatchingUserProject(target, readIdentity, navigate)).matched).toBe(true);
    expect((await openMatchingUserProject(target, readIdentity, navigate)).matched).toBe(false);
    expect(readIdentity).toHaveBeenCalledTimes(2);
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/?project=s-a');
    await expect(openMatchingUserProject(target, async () => { throw new Error('expired'); }, navigate)).rejects.toThrow('expired');
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});
