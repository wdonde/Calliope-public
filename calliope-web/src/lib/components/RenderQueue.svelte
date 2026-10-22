<script lang="ts">
	/**
	 * The render queue at a glance: what renders now, what waits (in order),
	 * what ended in the last minutes. Polls while anything is active. In a
	 * project it shows that project's jobs and how many of other projects share
	 * the queue (images and videos render one after another).
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetUrl, jobsApi, type QueueJob, type QueueOverview } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';

	interface Props {
		projectId?: number;
	}
	let { projectId }: Props = $props();

	const client = useQueryClient();
	const queueQuery = createQuery(
		toStore(() => ({
			queryKey: ['queue-overview', projectId ?? 'all'],
			queryFn: () => jobsApi.active(projectId),
			refetchInterval: (q: { state: { data?: QueueOverview } }) => {
				const d = q.state.data;
				return d && (d.running.length || d.queued.length || d.elsewhere.running || d.elsewhere.queued) ? 3000 : 15000;
			},
		})),
	);
	const data = $derived($queueQuery.data);
	const active = $derived(Boolean(data && (data.running.length || data.queued.length)));
	let open = $state(false);
	const count = $derived(data ? data.running.length + data.queued.length : 0);

	// Whatever finished: refresh what shows the results (cards, library, panels).
	let lastDone = $state<number | null>(null);
	$effect(() => {
		const newest = data?.recent[0]?.id ?? null;
		if (newest !== null && newest !== lastDone) {
			if (lastDone !== null) {
				for (const key of ['assets', 'jobs', 'asset-library', 'panels', 'library-media']) {
					void client.invalidateQueries({ queryKey: [key] });
				}
			}
			lastDone = newest;
		}
	});

	let now = $state(Date.now());
	$effect(() => {
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	function since(stamp: string | null): string {
		if (!stamp) return '';
		const sec = Math.max(0, Math.round((now - Date.parse(stamp.replace(' ', 'T') + 'Z')) / 1000));
		return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`;
	}

	async function cancel(job: QueueJob) {
		try {
			await jobsApi.cancel(job.id);
			await client.invalidateQueries({ queryKey: ['queue-overview'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}
</script>

<!-- A compact button in the filter row; the details open in a dialog. -->
<button type="button" class="queue-btn" class:active={count > 0} title={t('queue.title')} onclick={() => (open = true)}>
	{#if data?.running.length}<span class="dot"></span>{/if}
	{t('queue.button', { n: count })}
</button>

<Modal bind:open title={t('queue.title')} size="lg" onclose={() => (open = false)}>
	{#if data}
		<p class="summary">
			{#if active}
				{t('queue.summary', { running: data.running.length, queued: data.queued.length })}
			{:else}
				{t('queue.idle')}
			{/if}
			{#if projectId != null && (data.elsewhere.running || data.elsewhere.queued)}
				· {t('queue.elsewhere', { n: data.elsewhere.running + data.elsewhere.queued })}
			{/if}
		</p>
		<ul>
			{#each data.running as j (j.id)}
				<li class="running">
					<span class="dot"></span>
					<span class="label">{j.label}</span>
					{#if projectId == null && j.project}<span class="muted">{j.project}</span>{/if}
					<span class="muted">{t('queue.rendering')} {since(j.started_at)}</span>
					<button type="button" class="link" onclick={() => cancel(j)}>{t('common.cancel')}</button>
				</li>
			{/each}
			{#each data.queued as j (j.id)}
				<li>
					<span class="pos">{j.position}</span>
					<span class="label">{j.label}</span>
					{#if projectId == null && j.project}<span class="muted">{j.project}</span>{/if}
					<span class="muted">{t('queue.waiting', { time: since(j.created_at) })}</span>
					<button type="button" class="link" onclick={() => cancel(j)}>{t('common.cancel')}</button>
				</li>
			{/each}
		</ul>
		{#if data.recent.length}
			<h3 class="sub">{t('queue.recentTitle')}</h3>
			<ul>
				{#each data.recent as j (j.id)}
					<li class="recent {j.status}">
						{#if j.output && j.status === 'done'}
							<a href={assetUrl(j.output)} target="_blank" rel="noopener"><img src={assetUrl(j.output)} alt="" /></a>
						{:else}
							<span class="state">{j.status === 'failed' ? '✕' : '–'}</span>
						{/if}
						<span class="label">{j.label}</span>
						{#if projectId == null && j.project}<span class="muted">{j.project}</span>{/if}
						<span class="muted" title={j.error ?? ''}>
							{j.status === 'done' ? t('queue.done', { time: since(j.completed_at) }) : j.status === 'failed' ? t('queue.failed') : t('queue.cancelled')}
						</span>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</Modal>

<style>
	.queue-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-secondary);
		border-radius: 999px;
		padding: 4px 12px;
		cursor: pointer;
		font-size: 13px;
	}
	.queue-btn.active {
		border-color: var(--accent);
		color: var(--accent);
	}
	.summary {
		color: var(--text-secondary);
		font-size: 13px;
		margin: 0 0 8px;
	}
	.sub {
		font-size: 13px;
		margin: 16px 0 4px;
		color: var(--text-secondary);
	}
	ul {
		list-style: none;
		margin: 10px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	li {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 13px;
	}
	.label {
		font-weight: 600;
	}
	.dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		background: var(--accent);
		animation: pulse 1.2s ease-in-out infinite;
		flex: none;
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
	.pos {
		min-width: 18px;
		height: 18px;
		border-radius: 50%;
		border: 1px solid var(--border);
		font-size: 11px;
		display: grid;
		place-items: center;
		color: var(--text-secondary);
		flex: none;
	}
	.recent img {
		width: 28px;
		height: 28px;
		object-fit: cover;
		border-radius: 4px;
		display: block;
	}
	.state {
		width: 28px;
		text-align: center;
		color: var(--text-muted);
	}
	.recent.failed .state {
		color: #f87171;
	}
	.muted {
		color: var(--text-muted);
		font-size: 12px;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: 12px;
		margin-left: auto;
	}
</style>
