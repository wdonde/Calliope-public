<script lang="ts">
	/**
	 * Colorize a black-and-white picture (agent/colorize.py): original and result
	 * side by side, colors from the notes and -- with a project chosen -- from its
	 * characters' sheets, so a character gets the same colors in every picture.
	 */
	import { createQuery } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetUrl, colorizeApi, jobsApi, projects, type Character } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	interface Props {
		open: boolean;
		imagePath: string | null;
		/** Preselected project (Panels stage); the library lets the user pick one. */
		projectId?: number | null;
	}
	let { open = $bindable(), imagePath, projectId = null }: Props = $props();

	let notes = $state('');
	let chosenProject = $state<number | ''>('');
	let chosenChars = $state<Set<number>>(new Set());
	let jobId = $state<number | null>(null);
	let busy = $state(false);

	$effect(() => {
		if (open) {
			chosenProject = projectId ?? '';
			jobId = null;
		}
	});

	const projectsQuery = createQuery({ queryKey: ['projects'], queryFn: projects.list });
	const charsQuery = createQuery(
		toStore(() => ({
			queryKey: ['assets', chosenProject],
			queryFn: () => projects.getAssets(Number(chosenProject)),
			enabled: open && chosenProject !== '',
		})),
	);
	const characters = $derived((chosenProject !== '' ? ($charsQuery.data?.characters ?? []) : []) as Character[]);
	$effect(() => {
		// All characters of a newly chosen project start selected.
		chosenChars = new Set(characters.map((c) => c.id));
	});

	const jobQuery = createQuery(
		toStore(() => ({
			queryKey: ['job', jobId],
			queryFn: () => jobsApi.get(jobId!),
			enabled: jobId !== null,
			refetchInterval: (q: { state: { data?: { status: string } } }) =>
				['done', 'failed', 'cancelled'].includes(q.state.data?.status ?? '') ? false : 3000,
		})),
	);
	const job = $derived(jobId !== null ? $jobQuery.data : undefined);
	const result = $derived(job?.status === 'done' ? job.output_paths?.[0] : undefined);
	const running = $derived(jobId !== null && !!job && !['done', 'failed', 'cancelled'].includes(job.status));

	function toggleChar(id: number) {
		const next = new Set(chosenChars);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		chosenChars = next;
	}

	async function start() {
		if (!imagePath) return;
		busy = true;
		try {
			const r = await colorizeApi.start({
				image_path: imagePath,
				notes,
				project_id: chosenProject === '' ? null : chosenProject,
				character_ids: chosenProject === '' ? null : [...chosenChars],
			});
			jobId = r.job_id;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title={t('colorize.title')} size="xl" onclose={() => (open = false)}>
	<div class="layout">
		<div class="pair">
			<figure>
				{#if imagePath}<img src={assetUrl(imagePath)} alt="" />{/if}
				<figcaption>{t('colorize.original')}</figcaption>
			</figure>
			<figure>
				{#if result}
					<a href={assetUrl(result)} target="_blank" rel="noopener"><img src={assetUrl(result)} alt="" /></a>
				{:else}
					<div class="empty">
						{running ? t('colorize.running') : job?.status === 'failed' ? (job.error ?? t('comic.failed')) : t('colorize.waiting')}
					</div>
				{/if}
				<figcaption>{t('colorize.result')}</figcaption>
			</figure>
		</div>
		<div class="form">
			<label>
				<span class="field-label">{t('colorize.notes')}</span>
				<textarea class="field-textarea" rows="3" bind:value={notes} placeholder={t('colorize.notesPlaceholder')}></textarea>
			</label>
			<label>
				<span class="field-label">{t('colorize.project')}</span>
				<select class="field-select" bind:value={chosenProject}>
					<option value="">{t('colorize.noProject')}</option>
					{#each $projectsQuery.data ?? [] as p (p.id)}<option value={p.id}>{p.title}</option>{/each}
				</select>
			</label>
			{#if characters.length}
				<div class="chars">
					<span class="field-label">{t('colorize.characters')}</span>
					{#each characters as c (c.id)}
						<label class="check"><input type="checkbox" checked={chosenChars.has(c.id)} onchange={() => toggleChar(c.id)} /> {c.name}</label>
					{/each}
				</div>
			{/if}
			<p class="muted small">{t('colorize.hint')}</p>
		</div>
	</div>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.close')}</Button>
		<Button variant="primary" loading={busy || running} disabled={!imagePath} onclick={start}>
			{result ? t('colorize.again') : t('colorize.start')}
		</Button>
	{/snippet}
</Modal>

<style>
	.layout {
		display: grid;
		grid-template-columns: 1fr 320px;
		gap: 20px;
	}
	@media (max-width: 860px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
	.pair {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
	figure {
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	figure img,
	.empty {
		width: 100%;
		max-height: 60vh;
		object-fit: contain;
		border-radius: 8px;
		border: 1px solid var(--border);
		background: var(--surface-2, #111);
	}
	.empty {
		aspect-ratio: 2 / 3;
		display: grid;
		place-items: center;
		color: var(--text-muted);
		font-size: 12px;
		padding: 12px;
		text-align: center;
	}
	figcaption {
		font-size: 12px;
		color: var(--text-muted);
		text-align: center;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.chars {
		display: flex;
		flex-direction: column;
		gap: 4px;
		max-height: 200px;
		overflow-y: auto;
	}
	.check {
		flex-direction: row;
		align-items: center;
		gap: 6px;
		font-size: 13px;
	}
	.muted {
		color: var(--text-muted);
		margin: 0;
	}
	.small {
		font-size: 12px;
	}
</style>
