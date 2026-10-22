<script lang="ts">
	/** Project-wide busy indicator: LLM steps, agent turns and render jobs. */
	import { projectActivity, elapsed } from '$lib/activity';
	import { t } from '$lib/i18n.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';

	let { projectId }: { projectId: number } = $props();
	const query = projectActivity(() => projectId);
	const a = $derived($query.data);

	const lines = $derived.by(() => {
		if (!a) return [];
		const out: string[] = [];
		for (const op of a.operations) {
			out.push(`${op.label}${op.step ? ` — ${op.step}` : ''} · ${elapsed(op.elapsed_sec)}`);
		}
		for (const ag of a.agents) out.push(t('activity.agentWorkingTitle', { title: ag.title }));
		if (a.jobs.running || a.jobs.queued) {
			out.push(t('activity.jobsLine', { running: a.jobs.running, queued: a.jobs.queued }));
		}
		return out;
	});
</script>

<div class="status" class:busy={a?.busy} title={lines.join('\n') || t('activity.idleTitle')}>
	{#if a?.busy}
		<Spinner size="sm" />
		<span class="main">{lines[0]}</span>
		{#if lines.length > 1}<span class="more">+{lines.length - 1}</span>{/if}
	{:else}
		<Icon name="check" size={13} />
		<span class="main">{t('activity.idle')}</span>
	{/if}
</div>

<style>
	.status {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		max-width: 520px;
		padding: 3px 10px;
		border-radius: 999px;
		border: 1px solid var(--border, #3f3f46);
		color: var(--text-muted);
		font-size: 12px;
	}
	.status.busy {
		border-color: var(--accent);
		color: var(--text-primary);
		background: color-mix(in srgb, var(--accent) 12%, transparent);
	}
	.main {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.more {
		color: var(--text-muted);
	}
</style>
