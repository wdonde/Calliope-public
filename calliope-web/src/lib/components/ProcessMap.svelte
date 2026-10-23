<script lang="ts">
	/**
	 * The project's process map: the phases Story -> Assets -> Script ->
	 * Panels/Video as boxes, inside them the steps you can start, arrows for
	 * "this step needs what that one produces". A step is colored by its
	 * status (done, partly done, ready, blocked); clicking it shows what it
	 * needs and produces, which model, workflows, render style and prompts it
	 * uses -- each with a link to configure it -- and starts it.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import cytoscape, { type Core, type ElementDefinition } from 'cytoscape';
	import { processApi, type ProcessStep } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
	import RenderStylePicker from '$lib/components/RenderStylePicker.svelte';

	interface Props {
		projectId: number;
	}
	let { projectId }: Props = $props();

	const STATUS_COLOR: Record<ProcessStep['status'], string> = {
		done: '#3fb27f',
		partial: '#f0a33a',
		ready: '#4f9ee8',
		blocked: '#5b6170',
		optional: '#8a8f98',
	};

	const client = useQueryClient();
	const mapQuery = createQuery(
		toStore(() => ({
			queryKey: ['process-map', projectId],
			queryFn: () => processApi.get(projectId),
			refetchInterval: 8000,
		})),
	);
	const map = $derived($mapQuery.data);
	let selectedKey = $state<string | null>(null);
	const step = $derived(map?.steps.find((s) => s.key === selectedKey) ?? null);

	let container: HTMLDivElement;
	let cy: Core | null = null;

	function elements(): ElementDefinition[] {
		if (!map) return [];
		const out: ElementDefinition[] = [];
		map.phases.forEach((ph, i) => {
			out.push({ group: 'nodes', data: { id: `phase:${ph.key}`, label: `${i + 1} · ${ph.name}` }, classes: 'phase' });
			map.steps
				.filter((s) => s.phase === ph.key)
				.forEach((s, j) => {
					const label = s.progress ? `${s.name}\n${s.progress.have}/${s.progress.of}` : s.name;
					out.push({
						group: 'nodes',
						data: { id: s.key, parent: `phase:${ph.key}`, label, color: STATUS_COLOR[s.status] },
						position: { x: i * 260, y: j * 92 },
						classes: `step ${s.status}`,
					});
				});
		});
		for (const e of map.edges) out.push({ group: 'edges', data: { id: e.id, source: e.source, target: e.target, label: e.label } });
		return out;
	}

	function draw() {
		if (!cy) return;
		const had = cy.nodes().length > 0;
		cy.elements().remove();
		cy.add(elements());
		if (!had) cy.fit(undefined, 30);
		mark();
	}

	function mark() {
		if (!cy) return;
		cy.elements().removeClass('picked near');
		if (!selectedKey) return;
		const n = cy.getElementById(selectedKey);
		n.addClass('picked');
		n.connectedEdges().addClass('near');
	}

	onMount(() => {
		const css = getComputedStyle(document.documentElement);
		const text = css.getPropertyValue('--text-primary').trim() || '#e8e8ea';
		const accent = css.getPropertyValue('--accent').trim() || '#8b5cf6';
		const border = css.getPropertyValue('--border').trim() || '#33363d';
		cy = cytoscape({
			container,
			minZoom: 0.3,
			maxZoom: 2,
			wheelSensitivity: 0.25,
			autoungrabify: true,
			style: [
				{ selector: 'node.phase', style: {
					'background-opacity': 0.06, 'background-color': accent, 'border-width': 1, 'border-color': border,
					shape: 'round-rectangle', label: 'data(label)', color: text, 'font-size': 14, 'font-weight': 'bold',
					'text-valign': 'top', 'text-halign': 'center', 'text-margin-y': -6,
					// compound padding: supported by Cytoscape, missing in @types/cytoscape
					...({ padding: '18px' } as object) } },
				{ selector: 'node.step', style: {
					shape: 'round-rectangle', width: 190, height: 56, 'background-color': 'data(color)',
					'background-opacity': 0.22, 'border-width': 2, 'border-color': 'data(color)', label: 'data(label)',
					color: text, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': '175px', 'text-valign': 'center',
					'text-halign': 'center' } },
				{ selector: 'node.step.optional', style: { 'border-style': 'dashed' } },
				{ selector: 'node.step.blocked', style: { color: '#8a8f98' } },
				{ selector: 'node.picked', style: { 'border-width': 4, 'border-color': accent, 'background-opacity': 0.4 } },
				{ selector: 'edge', style: {
					width: 1.5, 'line-color': '#6b7280', 'target-arrow-color': '#6b7280', 'target-arrow-shape': 'triangle',
					'curve-style': 'bezier', opacity: 0.6 } },
				{ selector: 'edge.near', style: {
					'line-color': accent, 'target-arrow-color': accent, width: 3, opacity: 1, label: 'data(label)',
					'font-size': 10, color: text, 'text-background-color': '#000', 'text-background-opacity': 0.6,
					'text-background-padding': '2px' } },
			],
			layout: { name: 'preset' },
		});
		cy.on('tap', 'node.step', (ev) => {
			selectedKey = ev.target.id();
			mark();
		});
		cy.on('tap', (ev) => {
			if (ev.target === cy) {
				selectedKey = null;
				mark();
			}
		});
		draw();
	});
	onDestroy(() => cy?.destroy());

	// Redraw only when the map's shape or statuses change, not on every poll.
	let shape = '';
	$effect(() => {
		const next = JSON.stringify(map?.steps.map((s) => [s.key, s.status, s.progress]) ?? []);
		if (next === shape) return;
		shape = next;
		untrack(draw);
	});

	let confirmOpen = $state(false);
	let starting = $state(false);
	function askStart() {
		if (!step?.start) return;
		if (step.start.confirm) confirmOpen = true;
		else void start();
	}
	async function start() {
		if (!step?.start) return;
		starting = true;
		try {
			await processApi.start(step.start);
			toast.success(t('procmap.started', { name: step.name }));
			await client.invalidateQueries({ queryKey: ['process-map', projectId] });
			await client.invalidateQueries({ queryKey: ['jobs'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			starting = false;
		}
	}
</script>

<ConfirmDialog bind:open={confirmOpen} title={t('procmap.confirmTitle', { name: step?.name ?? '' })}
	message={step?.start?.confirm ?? ''} confirmLabel={t('procmap.start')} onconfirm={start} />

<p class="keys small muted">
	{#each Object.entries(STATUS_COLOR) as [s, c] (s)}
		<span><span class="sw" style:background={c}></span>{t(`procmap.status.${s}`)}</span>
	{/each}
	<span>→ {t('procmap.arrow')}</span>
</p>

<div class="body">
	<div class="graph" bind:this={container}>
		{#if $mapQuery.isLoading}<p class="muted loading">…</p>{/if}
	</div>

	<aside class="side">
		{#if step}
			<div class="card">
				<span class="status" style:color={STATUS_COLOR[step.status]}>{t(`procmap.status.${step.status}`)}</span>
				<h3>{step.name}</h3>
				<p class="small">{step.description}</p>
				{#if step.progress}
					<div class="bar"><span style:width={`${(100 * step.progress.have) / Math.max(1, step.progress.of)}%`}></span></div>
					<span class="small muted">{step.progress.have} / {step.progress.of}</span>
				{/if}

				<h4>{t('procmap.needs')}</h4>
				{#each step.needs as n (n.what)}
					<span class="small" class:missing={!n.have}>{n.have ? '✓' : '✗'} {n.label} ({n.have})</span>
				{:else}
					<span class="small muted">{t('procmap.nothing')}</span>
				{/each}
				<h4>{t('procmap.produces')}</h4>
				{#each step.produces as p (p.what)}<span class="small">{p.label} ({p.have})</span>{/each}

				{#if step.model || step.workflows.length || step.style || step.prompts.length}
					<h4>{t('procmap.configure')}</h4>
				{/if}
				{#if step.model}
					<span class="small">{t('procmap.model', { role: step.model.role })}
						<b>{step.model.model ?? t('procmap.activeModel')}</b>
						<a href="/settings?tab=agent">{t('procmap.change')}</a></span>
				{/if}
				{#each step.workflows as w (w.role)}
					<span class="small">{w.label}: <b>{w.workflow ?? t('procmap.noWorkflow')}</b>
						{#if w.from_style}<span class="muted">({t('procmap.fromStyle')})</span>{/if}
						<a href="/settings?tab=workflows">{t('procmap.change')}</a></span>
				{/each}
				{#if step.style}<div class="style"><RenderStylePicker {projectId} /></div>{/if}
				{#if step.prompts.length}
					<span class="small">{t('procmap.prompts')}</span>
					<ul class="small">
						{#each step.prompts as p (p.key)}
							<li><a href={`/settings?tab=prompts&key=${p.key}`}>{p.name}</a>
								{#if !p.editable}<span class="muted">({t('prompts.assembled')})</span>{/if}</li>
						{/each}
					</ul>
				{/if}

				<div class="actions">
					{#if step.start}
						<Button size="sm" variant="primary" loading={starting} disabled={step.status === 'blocked'}
							title={step.status === 'blocked' ? t('procmap.blockedHint') : ''} onclick={askStart}>
							{t('procmap.start')}
						</Button>
					{/if}
					<a class="small" href={step.page}>{step.start ? t('procmap.openPage') : t('procmap.doItThere')} →</a>
				</div>
			</div>
		{:else}
			<p class="muted small hint">{t('procmap.clickHint')}</p>
		{/if}
		{#if map}
			<div class="steps">
				{#each map.steps as s (s.key)}
					<button type="button" class="row" class:on={s.key === selectedKey}
						onclick={() => {
							selectedKey = s.key;
							mark();
						}}>
						<span class="dot" style:background={STATUS_COLOR[s.status]}></span>
						<span>{s.name}</span>
						{#if s.progress}<span class="muted small">{s.progress.have}/{s.progress.of}</span>{/if}
					</button>
				{/each}
			</div>
		{/if}
	</aside>
</div>

<style>
	.keys {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		margin: 0 0 8px;
	}
	.sw {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 3px;
		margin-right: 5px;
		vertical-align: middle;
	}
	.body {
		display: grid;
		grid-template-columns: 1fr 340px;
		gap: 12px;
		height: calc(100vh - 260px);
		min-height: 480px;
	}
	@media (max-width: 900px) {
		.body {
			grid-template-columns: 1fr;
			height: auto;
		}
		.graph {
			height: 60vh;
		}
	}
	.graph {
		position: relative;
		border: 1px solid var(--border);
		border-radius: 10px;
		min-height: 400px;
	}
	.loading {
		position: absolute;
		inset: 45% 0 0;
		text-align: center;
	}
	.side {
		display: flex;
		flex-direction: column;
		gap: 10px;
		overflow-y: auto;
		min-height: 0;
	}
	.card {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 12px;
		border: 1px solid var(--accent);
		border-radius: 10px;
	}
	.card h3 {
		margin: 0;
		font-size: 16px;
	}
	.card p {
		margin: 0 0 4px;
	}
	h4 {
		margin: 8px 0 2px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-secondary);
	}
	.status {
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.bar {
		height: 6px;
		border-radius: 3px;
		background: var(--border);
		overflow: hidden;
	}
	.bar span {
		display: block;
		height: 100%;
		background: #f0a33a;
	}
	.missing {
		color: var(--error, #e5534b);
	}
	ul {
		margin: 0;
		padding-left: 18px;
	}
	a {
		color: var(--accent);
		margin-left: 4px;
	}
	.style {
		margin: 4px 0;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 10px;
	}
	.hint {
		padding: 12px;
		border: 1px dashed var(--border);
		border-radius: 10px;
	}
	.steps {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 5px 8px;
		border: none;
		border-radius: 6px;
		background: none;
		color: inherit;
		text-align: left;
		cursor: pointer;
		font-size: 13px;
	}
	.row.on,
	.row:hover {
		background: var(--surface-2, rgba(255, 255, 255, 0.05));
	}
	.row .muted {
		margin-left: auto;
	}
	.dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		flex: none;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
