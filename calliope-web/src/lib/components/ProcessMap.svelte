<script lang="ts">
	/**
	 * Project assistant and overview: the phases Story -> Assets -> Script ->
	 * Panels/Video as lanes (in the style of an architecture workflow diagram),
	 * the steps you can run as cards colored by what they do, orthogonal
	 * arrows labelled with what one step hands to the next. The side panel
	 * says what to do next, sums the project up and -- for a clicked step --
	 * shows what it needs and produces, the model, workflows, render style and
	 * prompts it uses (each linked to its settings) and starts it.
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

	type StepType = 'write' | 'render' | 'export' | 'manual';
	const TYPE_COLOR: Record<StepType, string> = {
		write: '#2bb39a',
		render: '#e8893a',
		export: '#9b7bd4',
		manual: '#4f9ee8',
	};
	const STATUS_MARK: Record<ProcessStep['status'], string> = {
		done: '✓',
		partial: '◐',
		ready: '▶',
		blocked: '…',
		optional: '○',
	};
	const LANE_W = 1000;
	const CARD_W = 190;
	const typeOf = (s: ProcessStep): StepType =>
		s.key.endsWith('export') ? 'export' : s.workflows.length || s.key === 'video.render' ? 'render' : s.start ? 'write' : 'manual';

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
	/** The first step in flow order that can move the project on. */
	const next = $derived(
		map?.steps.find((s) => s.status === 'partial') ?? map?.steps.find((s) => s.status === 'ready') ?? null,
	);
	const legend = $derived(
		(Object.keys(TYPE_COLOR) as StepType[])
			.map((k) => [k, (map?.steps ?? []).filter((s) => typeOf(s) === k).length] as const)
			.filter(([, n]) => n),
	);

	let container: HTMLDivElement;
	let cy: Core | null = null;
	let zoom = $state(100);

	function subtitle(s: ProcessStep): string {
		const mark = STATUS_MARK[s.status];
		if (s.progress) return `${mark} ${s.progress.have} / ${s.progress.of}`;
		return `${mark} ${t(`procmap.status.${s.status}`)}`;
	}

	function elements(): ElementDefinition[] {
		if (!map) return [];
		const out: ElementDefinition[] = [];
		map.phases.forEach((ph, i) => {
			const lane = `phase:${ph.key}`;
			out.push({ group: 'nodes', data: { id: lane, label: `0${i + 1} / ${ph.name}  ·  ${ph.hint}` }, classes: 'lane' });
			// invisible ends make every lane the same width
			for (const [end, x] of [['l', -40], ['r', LANE_W]] as const)
				out.push({ group: 'nodes', data: { id: `${lane}:${end}`, parent: lane }, position: { x, y: i * 150 }, classes: 'spacer' });
			map.steps
				.filter((s) => s.phase === ph.key)
				.forEach((s, j) => {
					const ty = typeOf(s);
					out.push({
						group: 'nodes',
						data: { id: s.key, parent: lane, label: `${s.name}\n${subtitle(s)}`, color: TYPE_COLOR[ty] },
						position: { x: 100 + j * (CARD_W + 36), y: i * 150 },
						classes: `step ${s.status} ${ty} ${next?.key === s.key ? 'next' : ''}`,
					});
				});
		});
		for (const e of map.edges) {
			const from = map.steps.find((s) => s.key === e.source);
			out.push({
				group: 'edges',
				data: { id: e.id, source: e.source, target: e.target, label: e.label },
				classes: from?.status === 'optional' ? 'soft' : '',
			});
		}
		return out;
	}

	function draw() {
		if (!cy) return;
		const had = cy.nodes().length > 0;
		cy.elements().remove();
		cy.add(elements());
		if (!had) cy.fit(undefined, 24);
		zoom = Math.round(cy.zoom() * 100);
		mark();
	}

	function mark() {
		if (!cy) return;
		cy.elements().removeClass('picked near dim');
		if (!selectedKey) return;
		const n = cy.getElementById(selectedKey);
		const near = n.connectedEdges();
		cy.nodes('.step').not(n).not(near.connectedNodes()).addClass('dim');
		cy.edges().not(near).addClass('dim');
		n.addClass('picked');
		near.addClass('near');
	}

	function pick(key: string | null) {
		selectedKey = key;
		mark();
	}

	onMount(() => {
		const css = getComputedStyle(document.documentElement);
		const text = css.getPropertyValue('--text-primary').trim() || '#e8e8ea';
		const muted = css.getPropertyValue('--text-muted').trim() || '#8a8f98';
		const accent = css.getPropertyValue('--accent').trim() || '#8b5cf6';
		const border = css.getPropertyValue('--border').trim() || '#33363d';
		const mono = 'ui-monospace, "JetBrains Mono", "Cascadia Code", Consolas, monospace';
		cy = cytoscape({
			container,
			minZoom: 0.3,
			maxZoom: 2,
			wheelSensitivity: 0.25,
			autoungrabify: true,
			style: [
				{ selector: 'node.lane', style: {
					shape: 'round-rectangle', 'background-color': accent, 'background-opacity': 0.04,
					'border-width': 1, 'border-color': border, label: 'data(label)', color: muted,
					'font-family': mono, 'font-size': 12, 'text-valign': 'top', 'text-halign': 'center',
					'text-margin-y': 22,
					// compound padding: supported by Cytoscape, missing in @types/cytoscape
					...({ padding: '34px' } as object) } },
				{ selector: 'node.spacer', style: { width: 1, height: 1, opacity: 0, events: 'no' } },
				{ selector: 'node.step', style: {
					shape: 'round-rectangle', width: CARD_W, height: 62, 'background-color': 'data(color)',
					'background-opacity': 0.13, 'border-width': 1.5, 'border-color': 'data(color)', label: 'data(label)',
					color: text, 'font-family': mono, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': `${CARD_W - 16}px`,
					'text-valign': 'center', 'text-halign': 'center', 'line-height': 1.6 } },
				{ selector: 'node.step.blocked', style: { opacity: 0.45, 'border-style': 'dashed' } },
				{ selector: 'node.step.optional', style: { 'border-style': 'dashed' } },
				{ selector: 'node.step.done', style: { 'background-opacity': 0.22 } },
				{ selector: 'node.step.next', style: { 'border-width': 3,
					// glow behind the next step: Cytoscape underlay, missing in @types/cytoscape
					...({ 'underlay-color': 'data(color)', 'underlay-opacity': 0.25, 'underlay-padding': 6 } as object) } },
				{ selector: 'node.picked', style: { 'border-width': 3, 'border-color': accent, 'background-opacity': 0.32 } },
				{ selector: 'edge', style: {
					width: 1.6, 'line-color': '#7c8494', 'target-arrow-color': '#7c8494', 'target-arrow-shape': 'triangle',
					'curve-style': 'taxi', 'taxi-direction': 'auto', 'taxi-turn': '50%', label: 'data(label)',
					'font-family': mono, 'font-size': 9, color: muted, 'text-background-color': '#15161a',
					'text-background-opacity': 1, 'text-background-shape': 'roundrectangle', 'text-background-padding': '3px',
					'text-border-width': 1, 'text-border-color': border, 'text-border-opacity': 1 } },
				{ selector: 'edge.soft', style: { 'line-style': 'dashed' } },
				{ selector: '.dim', style: { opacity: 0.18 } },
				{ selector: 'edge.near', style: { 'line-color': accent, 'target-arrow-color': accent, width: 2.6, color: text,
					'text-border-color': accent } },
			],
			layout: { name: 'preset' },
		});
		cy.on('tap', 'node.step', (ev) => pick(ev.target.id()));
		cy.on('tap', (ev) => {
			if (ev.target === cy) pick(null);
		});
		cy.on('zoom', () => (zoom = Math.round((cy?.zoom() ?? 1) * 100)));
		draw();
	});
	onDestroy(() => cy?.destroy());

	// Redraw only when steps or statuses change, not on every poll.
	let shape = '';
	$effect(() => {
		const sig = JSON.stringify([next?.key, map?.steps.map((s) => [s.key, s.status, s.progress])]);
		if (sig === shape) return;
		shape = sig;
		untrack(draw);
	});

	function zoomBy(f: number) {
		if (!cy) return;
		cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
	}

	let confirmOpen = $state(false);
	let starting = $state(false);
	let toStart = $state<ProcessStep | null>(null);
	function askStart(s: ProcessStep) {
		if (!s.start) return;
		toStart = s;
		if (s.start.confirm) confirmOpen = true;
		else void start();
	}
	async function start() {
		const s = toStart;
		if (!s?.start) return;
		starting = true;
		try {
			await processApi.start(s.start);
			toast.success(t('procmap.started', { name: s.name }));
			await client.invalidateQueries({ queryKey: ['process-map', projectId] });
			await client.invalidateQueries({ queryKey: ['jobs'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			starting = false;
		}
	}

	const OVERVIEW = ['beats', 'characters', 'locations', 'items', 'scenes', 'clips', 'panels', 'panel_images', 'videos'];
</script>

<ConfirmDialog bind:open={confirmOpen} title={t('procmap.confirmTitle', { name: toStart?.name ?? '' })}
	message={toStart?.start?.confirm ?? ''} confirmLabel={t('procmap.start')} onconfirm={start} />

<div class="body">
	<div class="canvas">
		<div class="graph" bind:this={container}>
			{#if $mapQuery.isLoading}<p class="muted loading">…</p>{/if}
		</div>
		<div class="legend">
			<strong>{t('procmap.legend')}</strong>
			{#each legend as [k, n] (k)}
				<span><span class="sw" style:border-color={TYPE_COLOR[k]} style:background={`${TYPE_COLOR[k]}22`}></span>
					{t(`procmap.type.${k}`)} <span class="count">{n}</span></span>
			{/each}
			<span class="marks">✓ {t('procmap.status.done')} · ◐ {t('procmap.status.partial')} · ▶ {t('procmap.status.ready')}
				· … {t('procmap.status.blocked')} · ○ {t('procmap.status.optional')}</span>
		</div>
		<div class="zoombar">
			<button type="button" onclick={() => cy?.fit(undefined, 24)}>{t('procmap.fit')}</button>
			<span class="sep"></span>
			<button type="button" aria-label="zoom out" onclick={() => zoomBy(1 / 1.2)}>−</button>
			<span class="pct">{zoom}%</span>
			<button type="button" aria-label="zoom in" onclick={() => zoomBy(1.2)}>+</button>
		</div>
	</div>

	<aside class="side">
		{#if next && !step}
			<div class="card next">
				<span class="eyebrow">{t('procmap.nextStep')}</span>
				<h3>{next.name}</h3>
				<p class="small">{next.description}</p>
				{#if next.progress}<span class="small muted">{next.progress.have} / {next.progress.of}</span>{/if}
				<div class="actions">
					{#if next.start}
						<Button size="sm" variant="primary" loading={starting} onclick={() => askStart(next)}>{t('procmap.start')}</Button>
					{/if}
					<button type="button" class="link small" onclick={() => pick(next.key)}>{t('procmap.details')}</button>
					<a class="small" href={next.page}>{t('procmap.openPage')} →</a>
				</div>
			</div>
		{:else if !step && map}
			<div class="card"><span class="eyebrow">{t('procmap.nextStep')}</span><p class="small">{t('procmap.allDone')}</p></div>
		{/if}

		{#if step}
			<div class="card">
				<span class="eyebrow" style:color={TYPE_COLOR[typeOf(step)]}>
					{t(`procmap.type.${typeOf(step)}`)} · {t(`procmap.status.${step.status}`)}
				</span>
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
						<b>{step.model.model ?? t('procmap.activeModel')}</b> <a href="/settings?tab=agent">{t('procmap.change')}</a></span>
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
							title={step.status === 'blocked' ? t('procmap.blockedHint') : ''} onclick={() => askStart(step)}>
							{t('procmap.start')}
						</Button>
					{/if}
					<a class="small" href={step.page}>{step.start ? t('procmap.openPage') : t('procmap.doItThere')} →</a>
					<button type="button" class="link small close" onclick={() => pick(null)}>×</button>
				</div>
			</div>
		{/if}

		{#if map}
			<div class="card overview">
				<span class="eyebrow">{t('procmap.overview')}</span>
				<div class="stats">
					{#each OVERVIEW as k (k)}
						<span><b>{map.counts[k] ?? 0}</b> {t(`procmap.count.${k}`)}</span>
					{/each}
				</div>
				{#if map.counts.running}<span class="small running">{t('procmap.running', { n: map.counts.running })}</span>{/if}
			</div>
			<div class="steps">
				{#each map.steps as s (s.key)}
					<button type="button" class="row" class:on={s.key === selectedKey} onclick={() => pick(s.key)}>
						<span class="dot" style:border-color={TYPE_COLOR[typeOf(s)]}>{STATUS_MARK[s.status]}</span>
						<span>{s.name}</span>
						{#if s.progress}<span class="muted small">{s.progress.have}/{s.progress.of}</span>{/if}
					</button>
				{/each}
			</div>
		{/if}
	</aside>
</div>

<style>
	.body {
		display: grid;
		grid-template-columns: 1fr 340px;
		gap: 12px;
		height: calc(100vh - 230px);
		min-height: 520px;
	}
	@media (max-width: 900px) {
		.body {
			grid-template-columns: 1fr;
			height: auto;
		}
		.canvas {
			height: 65vh;
		}
	}
	.canvas {
		position: relative;
		border: 1px solid var(--border);
		border-radius: 14px;
		overflow: hidden;
		background-color: var(--bg-surface, transparent);
		background-image:
			linear-gradient(rgba(127, 127, 127, 0.07) 1px, transparent 1px),
			linear-gradient(90deg, rgba(127, 127, 127, 0.07) 1px, transparent 1px);
		background-size: 24px 24px;
		min-height: 420px;
	}
	.graph {
		position: absolute;
		inset: 0 0 44px;
	}
	.loading {
		position: absolute;
		inset: 45% 0 0;
		text-align: center;
	}
	.legend {
		position: absolute;
		left: 14px;
		bottom: 10px;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		align-items: center;
		font-family: ui-monospace, Consolas, monospace;
		font-size: 11px;
		color: var(--text-secondary);
		max-width: calc(100% - 230px);
	}
	.legend strong {
		font-family: inherit;
		color: var(--text-primary, inherit);
	}
	.sw {
		display: inline-block;
		width: 16px;
		height: 10px;
		border: 1.5px solid;
		border-radius: 3px;
		margin-right: 4px;
		vertical-align: middle;
	}
	.count {
		display: inline-grid;
		place-items: center;
		min-width: 16px;
		height: 16px;
		border-radius: 50%;
		border: 1px solid var(--border);
		font-size: 10px;
	}
	.marks {
		color: var(--text-muted);
	}
	.zoombar {
		position: absolute;
		right: 12px;
		bottom: 8px;
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 4px 6px;
		border: 1px solid var(--border);
		border-radius: 10px;
		background: var(--bg-surface, #15161a);
		font-family: ui-monospace, Consolas, monospace;
		font-size: 12px;
	}
	.zoombar button {
		background: none;
		border: none;
		color: var(--text-secondary);
		cursor: pointer;
		padding: 2px 6px;
		font-size: 13px;
	}
	.zoombar .sep {
		width: 1px;
		height: 16px;
		background: var(--border);
	}
	.pct {
		min-width: 40px;
		text-align: center;
		color: var(--text-secondary);
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
		border: 1px solid var(--border);
		border-radius: 12px;
	}
	.card.next {
		border-color: var(--accent);
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
	}
	.card h3 {
		margin: 0;
		font-size: 16px;
	}
	.card p {
		margin: 0 0 4px;
	}
	.eyebrow {
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--accent);
	}
	h4 {
		margin: 8px 0 2px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-secondary);
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
		background: #e8893a;
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
	}
	.style {
		margin: 4px 0;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 8px;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
	}
	.close {
		margin-left: auto;
		font-size: 16px;
		color: var(--text-muted);
	}
	.stats {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 4px 12px;
		font-size: 12px;
		color: var(--text-secondary);
	}
	.stats b {
		color: var(--text-primary, inherit);
		font-family: ui-monospace, Consolas, monospace;
	}
	.running {
		color: #e8893a;
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
		display: inline-grid;
		place-items: center;
		width: 20px;
		height: 20px;
		border: 1.5px solid;
		border-radius: 5px;
		font-size: 10px;
		flex: none;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
