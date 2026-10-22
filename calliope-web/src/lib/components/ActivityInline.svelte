<script lang="ts">
	/** "Working…" line next to the element an LLM step or agent is busy with. */
	import { elapsed, projectActivity, runningOp } from '$lib/activity';
	import { t } from '$lib/i18n.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';

	let {
		projectId,
		kinds,
		agents = false,
	}: { projectId: number; kinds: string[]; agents?: boolean } = $props();

	const query = projectActivity(() => projectId);
	const op = $derived(runningOp($query.data, ...kinds));
	const agent = $derived(agents ? $query.data?.agents[0] : undefined);
</script>

{#if op || agent}
	<p class="inline-activity" role="status">
		<Spinner size="sm" />
		{#if op}
			<span>{op.label}{op.step ? ` — ${op.step}` : ''}</span>
			<span class="time">{elapsed(op.elapsed_sec)}</span>
		{:else if agent}
			<span>{t('activity.agentWorkingTitle', { title: agent.title })}</span>
		{/if}
	</p>
{/if}

<style>
	.inline-activity {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 8px 0;
		font-size: 13px;
		color: var(--text-primary);
	}
	.time {
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
</style>
