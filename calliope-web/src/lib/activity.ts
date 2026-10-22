import { createQuery } from '@tanstack/svelte-query';
import { toStore } from 'svelte/store';
import { activityApi, type ProjectActivity } from '$lib/api';

/**
 * Shared "is anything working?" query for one project. Every indicator uses
 * the same key, so one poll feeds them all: every 2 s while busy, 6 s idle.
 */
export function projectActivity(getProjectId: () => number) {
	return createQuery(
		toStore(() => ({
			queryKey: ['activity', getProjectId()],
			queryFn: () => activityApi.project(getProjectId()),
			refetchInterval: (q: { state: { data?: ProjectActivity } }) => (q.state.data?.busy ? 2000 : 6000),
		})),
	);
}

/** "1:05" from seconds. */
export function elapsed(sec: number): string {
	return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

/** The newest running LLM step of one of `kinds`, if any. */
export function runningOp(a: ProjectActivity | undefined, ...kinds: string[]) {
	return a?.operations.filter((o) => kinds.includes(o.kind)).at(-1);
}
