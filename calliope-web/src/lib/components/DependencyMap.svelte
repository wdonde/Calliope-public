<script lang="ts">
	/**
	 * A project's dependency map: every beat, character, location, item, scene,
	 * clip, panel, library asset, render style, workflow (and optionally the
	 * prompts) as a zoomable graph. An arrow source -> target means "target is
	 * built from source". Clicking a node lights up what a change to it affects
	 * (downstream) and what it depends on (upstream); the side panel shows it,
	 * opens it and offers the fixes: remove or add a connection, merge
	 * duplicates, delete what nothing uses, update from the library.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import cytoscape, { type Core, type ElementDefinition } from 'cytoscape';
	import {
		assetLibraryApi,
		assetUrl,
		dependenciesApi,
		projects as projectsApi,
		type DepEdge,
		type DepFinding,
		type DepKind,
		type DepNode,
	} from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';

	interface Props {
		projectId: number;
	}
	let { projectId }: Props = $props();

	const COLORS: Record<DepKind, string> = {
		library: '#d4a72c',
		style: '#e26d5c',
		prompt: '#8a8f98',
		beat: '#9b7bd4',
		character: '#4f9ee8',
		location: '#3fb27f',
		item: '#c98a3c',
		scene: '#7c6cf0',
		clip: '#5aa9b8',
		panel: '#d65db1',
		workflow: '#6b7a8f',
	};
	const KINDS = Object.keys(COLORS) as DepKind[];

	let showPrompts = $state(false);
	const client = useQueryClient();
	const mapQuery = createQuery(
		toStore(() => ({
			queryKey: ['dependencies', projectId, showPrompts],
			queryFn: () => dependenciesApi.get(projectId, showPrompts),
		})),
	);
	const map = $derived($mapQuery.data);
	let hidden = $state<Set<DepKind>>(new Set());
	let search = $state('');
	let selected = $state<{ type: 'node'; node: DepNode } | { type: 'edge'; edge: DepEdge } | null>(null);
	let impact = $state<{ down: string[]; up: string[] }>({ down: [], up: [] });

	let container: HTMLDivElement;
	let cy: Core | null = null;

	const nodeById = $derived(new Map((map?.nodes ?? []).map((n) => [n.id, n])));
	const flagged = $derived(new Set((map?.findings ?? []).filter((f) => f.severity === 'warn').flatMap((f) => f.nodes)));
	const counts = $derived(
		KINDS.map((k) => [k, (map?.nodes ?? []).filter((n) => n.kind === k).length] as const).filter(([, n]) => n),
	);

	/** Columns left to right; inside a column the kinds stack with a gap. */
	function positions(nodes: DepNode[]): Map<string, { x: number; y: number }> {
		const out = new Map<string, { x: number; y: number }>();
		const y: Record<number, number> = {};
		let lastKind: Record<number, string> = {};
		for (const n of nodes) {
			const col = n.column;
			y[col] ??= 0;
			if (lastKind[col] && lastKind[col] !== n.kind) y[col] += 40;
			lastKind = { ...lastKind, [col]: n.kind };
			out.set(n.id, { x: col * 320, y: y[col] });
			y[col] += 44;
		}
		// centre each column vertically on the tallest one
		const tallest = Math.max(...Object.values(y), 0);
		for (const n of nodes) {
			const p = out.get(n.id)!;
			p.y += (tallest - y[n.column]) / 2;
		}
		return out;
	}

	function elements(): ElementDefinition[] {
		if (!map) return [];
		const nodes = map.nodes.filter((n) => !hidden.has(n.kind));
		const keep = new Set(nodes.map((n) => n.id));
		const pos = positions(nodes);
		return [
			...nodes.map((n) => ({
				group: 'nodes' as const,
				data: { id: n.id, label: n.label.length > 34 ? n.label.slice(0, 33) + '…' : n.label, kind: n.kind,
					color: COLORS[n.kind] },
				position: pos.get(n.id),
				classes: [flagged.has(n.id) ? 'flagged' : '', n.kind === 'panel' && !n.rendered ? 'pending' : ''].join(' '),
			})),
			...map.edges
				.filter((e) => keep.has(e.source) && keep.has(e.target))
				.map((e) => ({
					group: 'edges' as const,
					data: { id: e.id, source: e.source, target: e.target, rel: e.rel },
					classes: [e.rel === 'mentioned' || e.rel === 'writes' ? 'soft' : '', e.outdated ? 'outdated' : '',
						e.configurable ? 'configurable' : ''].join(' '),
				})),
		];
	}

	function draw() {
		if (!cy) return;
		const zoom = cy.zoom();
		const pan = cy.pan();
		const had = cy.nodes().length > 0;
		cy.elements().remove();
		cy.add(elements());
		if (had) {
			cy.zoom(zoom);
			cy.pan(pan);
		} else {
			cy.fit(undefined, 30);
		}
		applySelection();
	}

	function applySelection() {
		if (!cy) return;
		cy.elements().removeClass('dim up down picked');
		if (!selected) return;
		cy.elements().addClass('dim');
		if (selected.type === 'edge') {
			const e = cy.getElementById(selected.edge.id);
			e.removeClass('dim').addClass('picked');
			e.connectedNodes().removeClass('dim').addClass('picked');
			return;
		}
		const n = cy.getElementById(selected.node.id);
		const down = n.successors();
		const up = n.predecessors();
		down.removeClass('dim').addClass('down');
		up.removeClass('dim').addClass('up');
		n.removeClass('dim').addClass('picked');
		impact = {
			down: down.nodes().map((x) => x.id()),
			up: up.nodes().map((x) => x.id()),
		};
	}

	function select(id: string | null) {
		const n = id ? nodeById.get(id) : undefined;
		selected = n ? { type: 'node', node: n } : null;
		applySelection();
		if (n && cy) cy.animate({ center: { eles: cy.getElementById(n.id) } }, { duration: 250 });
	}

	onMount(() => {
		const css = getComputedStyle(document.documentElement);
		const text = css.getPropertyValue('--text-primary').trim() || '#e8e8ea';
		const accent = css.getPropertyValue('--accent').trim() || '#8b5cf6';
		cy = cytoscape({
			container,
			minZoom: 0.15,
			maxZoom: 2.5,
			wheelSensitivity: 0.25,
			style: [
				{ selector: 'node', style: {
					'background-color': 'data(color)', label: 'data(label)', color: text, 'font-size': 11,
					'text-valign': 'center', 'text-halign': 'right', 'text-margin-x': 6, width: 16, height: 16,
					'text-max-width': '230px', 'text-wrap': 'ellipsis' } },
				{ selector: 'node.flagged', style: { 'border-width': 3, 'border-color': '#f0a33a' } },
				{ selector: 'node.pending', style: { 'background-opacity': 0.35 } },
				{ selector: 'edge', style: {
					width: 1.2, 'line-color': '#5b6170', 'target-arrow-color': '#5b6170', 'target-arrow-shape': 'triangle',
					'arrow-scale': 0.7, 'curve-style': 'bezier', opacity: 0.55 } },
				{ selector: 'edge.soft', style: { 'line-style': 'dashed' } },
				{ selector: 'edge.outdated', style: { 'line-color': '#f0a33a', 'target-arrow-color': '#f0a33a', width: 2.5, opacity: 1 } },
				{ selector: '.dim', style: { opacity: 0.12 } },
				{ selector: 'node.down', style: { 'border-width': 3, 'border-color': '#e5534b' } },
				{ selector: 'edge.down', style: { 'line-color': '#e5534b', 'target-arrow-color': '#e5534b', opacity: 1, width: 2 } },
				{ selector: 'node.up', style: { 'border-width': 3, 'border-color': '#3fb27f' } },
				{ selector: 'edge.up', style: { 'line-color': '#3fb27f', 'target-arrow-color': '#3fb27f', opacity: 1, width: 2 } },
				{ selector: 'node.picked', style: { 'border-width': 4, 'border-color': accent, width: 24, height: 24 } },
				{ selector: 'edge.picked', style: { 'line-color': accent, 'target-arrow-color': accent, opacity: 1, width: 3 } },
			],
			layout: { name: 'preset' },
		});
		cy.on('tap', 'node', (ev) => select(ev.target.id()));
		cy.on('tap', 'edge', (ev) => {
			const e = map?.edges.find((x) => x.id === ev.target.id());
			selected = e ? { type: 'edge', edge: e } : null;
			applySelection();
		});
		cy.on('tap', (ev) => {
			if (ev.target === cy) {
				selected = null;
				applySelection();
			}
		});
		draw();
	});
	onDestroy(() => cy?.destroy());

	$effect(() => {
		void map;
		void hidden;
		untrack(draw);
	});

	$effect(() => {
		const q = search.trim().toLowerCase();
		if (!q || !cy) return;
		const hit = (map?.nodes ?? []).find((n) => n.label.toLowerCase().includes(q));
		if (hit) untrack(() => select(hit.id));
	});

	function toggleKind(k: DepKind) {
		const next = new Set(hidden);
		if (next.has(k)) next.delete(k);
		else next.add(k);
		hidden = next;
	}

	async function refresh() {
		await client.invalidateQueries({ queryKey: ['dependencies', projectId] });
		await client.invalidateQueries({ queryKey: ['assets'] });
	}

	async function run(fn: () => Promise<unknown>, done: string) {
		try {
			await fn();
			toast.success(done);
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	const num = (id: string) => Number(id.split(':')[1]);

	function unlinkEdge(e: DepEdge) {
		if (!e.configurable) return;
		void run(
			() => dependenciesApi.unlink(projectId, { kind: e.configurable!, source_id: num(e.source), target_id: num(e.target) }),
			t('depmap.unlinked'),
		);
		selected = null;
	}

	let addScene = $state('');
	function linkToScene(n: DepNode) {
		if (!addScene) return;
		const kind = n.kind === 'character' ? 'scene_character' : 'scene_location';
		void run(() => dependenciesApi.link(projectId, { kind, source_id: n.entity_id!, target_id: Number(addScene) }),
			t('depmap.linked'));
		addScene = '';
	}

	let confirm = $state<{ title: string; message: string; go: () => void } | null>(null);
	let confirmOpen = $state(false);
	function ask(title: string, message: string, go: () => void) {
		confirm = { title, message, go };
		confirmOpen = true;
	}

	function removeNode(n: DepNode) {
		ask(t('depmap.deleteTitle'), t('depmap.deleteBody', { name: n.label }), () => {
			const id = n.entity_id!;
			const fn = n.kind === 'character' ? () => projectsApi.deleteCharacter(projectId, id)
				: n.kind === 'location' ? () => projectsApi.deleteLocation(projectId, id)
				: () => projectsApi.deleteItem(projectId, id);
			void run(fn, t('depmap.deleted', { name: n.label }));
			selected = null;
		});
	}

	function mergeFinding(f: DepFinding) {
		const [keep, drop] = f.nodes.map((id) => nodeById.get(id)!);
		if (!keep || !drop) return;
		ask(t('depmap.mergeTitle'), t('depmap.mergeBody', { keep: keep.label, drop: drop.label }), () =>
			void run(() => dependenciesApi.merge(projectId, keep.kind as 'character' | 'location', keep.entity_id!, drop.entity_id!),
				t('depmap.merged', { name: keep.label })));
	}

	function act(f: DepFinding) {
		const n = nodeById.get(f.nodes[f.nodes.length - 1]);
		if (f.action === 'merge') mergeFinding(f);
		else if (f.action === 'delete' && n) removeNode(n);
		else if (f.action === 'update_from_library' && n?.entity_id)
			void run(() => assetLibraryApi.updateFromLibrary(projectId, n.entity_id!), t('depmap.updated', { name: n.label }));
	}

	const scenes = $derived((map?.nodes ?? []).filter((n) => n.kind === 'scene'));
	const selNode = $derived(selected?.type === 'node' ? selected.node : null);
	const nodeFindings = $derived(selNode ? (map?.findings ?? []).filter((f) => f.nodes.includes(selNode.id)) : []);
	const linkedScenes = $derived(
		new Set(selNode ? (map?.edges ?? []).filter((e) => e.source === selNode.id).map((e) => e.target) : []),
	);
	const labelOf = (id: string) => nodeById.get(id)?.label ?? id;
	const warnCount = $derived((map?.findings ?? []).filter((f) => f.severity === 'warn').length);
</script>

{#if confirm}
	<ConfirmDialog bind:open={confirmOpen} title={confirm.title} message={confirm.message}
		confirmLabel={t('depmap.confirm')} onconfirm={() => confirm?.go()} />
{/if}

<header class="stage-head">
	<div>
		<h2>{t('depmap.title')}</h2>
		<p class="muted">{t('depmap.lead')}</p>
	</div>
</header>

<div class="toolbar">
	<input class="field-input search" placeholder={t('depmap.search')} bind:value={search} />
	<div class="legend">
		{#each counts as [k, n] (k)}
			<button type="button" class="kind" class:off={hidden.has(k)} onclick={() => toggleKind(k)}
				title={t('depmap.toggleKind')}>
				<span class="dot" style:background={COLORS[k]}></span>{t(`depmap.kind.${k}`)} <span class="muted">{n}</span>
			</button>
		{/each}
	</div>
	<label class="check"><input type="checkbox" bind:checked={showPrompts} /> {t('depmap.showPrompts')}</label>
	<Button size="sm" variant="ghost" onclick={() => cy?.fit(undefined, 30)}>{t('depmap.fit')}</Button>
</div>
<p class="keys small muted">
	<span><span class="sw red"></span>{t('depmap.affects')}</span>
	<span><span class="sw green"></span>{t('depmap.dependsOn')}</span>
	<span><span class="sw orange"></span>{t('depmap.flagged')}</span>
	<span>- - - {t('depmap.soft')}</span>
</p>

<div class="body">
	<div class="graph" bind:this={container}>
		{#if $mapQuery.isLoading}<p class="muted loading">…</p>{/if}
	</div>

	<aside class="side">
		{#if selected?.type === 'node'}
			{@const n = selected.node}
			<div class="card">
				<span class="kind-label" style:color={COLORS[n.kind]}>{t(`depmap.kind.${n.kind}`)}</span>
				<h3>{n.label}</h3>
				{#if n.image}<img src={assetUrl(n.image)} alt="" />{/if}
				<p class="small">
					<b class="red">{t('depmap.affectsN', { n: impact.down.length })}</b> ·
					<b class="green">{t('depmap.dependsOnN', { n: impact.up.length })}</b>
				</p>
				{#if impact.down.length}
					<details>
						<summary class="small">{t('depmap.whatChanges')}</summary>
						<ul class="small">{#each impact.down.slice(0, 40) as id (id)}<li><button type="button" class="link" onclick={() => select(id)}>{labelOf(id)}</button></li>{/each}</ul>
					</details>
				{/if}
				{#each nodeFindings as f (f.type + f.nodes.join())}
					<p class="finding {f.severity} small">{f.text}
						{#if f.action}<button type="button" class="link" onclick={() => act(f)}>{t(`depmap.action.${f.action}`)}</button>{/if}
					</p>
				{/each}
				{#if n.kind === 'character' || n.kind === 'location'}
					<div class="row small">
						<select class="field-select" bind:value={addScene}>
							<option value="">{t('depmap.addToScene')}</option>
							{#each scenes.filter((s) => !linkedScenes.has(s.id)) as s (s.id)}<option value={s.entity_id}>{s.label}</option>{/each}
						</select>
						<Button size="sm" variant="secondary" disabled={!addScene} onclick={() => linkToScene(n)}>{t('depmap.add')}</Button>
					</div>
				{/if}
				<div class="row">
					{#if n.link}<a class="small" href={n.link}>{t('depmap.open')} →</a>{/if}
					{#if (n.kind === 'character' || n.kind === 'location' || n.kind === 'item') && n.entity_id}
						<button type="button" class="link danger small" onclick={() => removeNode(n)}>{t('common.delete')}</button>
					{/if}
				</div>
			</div>
		{:else if selected?.type === 'edge'}
			{@const e = selected.edge}
			<div class="card">
				<span class="kind-label">{t('depmap.connection')}</span>
				<h3>{labelOf(e.source)} → {labelOf(e.target)}</h3>
				<p class="small">{t(`depmap.rel.${e.rel}`, { rel: e.rel })}</p>
				{#if e.outdated}<p class="finding warn small">{t('depmap.outdatedEdge')}</p>{/if}
				{#if e.configurable}
					<Button size="sm" variant="secondary" onclick={() => unlinkEdge(e)}>{t('depmap.unlink')}</Button>
				{:else}
					<p class="muted small">{t('depmap.notConfigurable')}</p>
				{/if}
			</div>
		{:else}
			<p class="muted small hint">{t('depmap.clickHint')}</p>
		{/if}

		<h4>{t('depmap.findings', { n: map?.findings.length ?? 0, w: warnCount })}</h4>
		<div class="findings">
			{#each map?.findings ?? [] as f, i (i)}
				<div class="finding {f.severity} small">
					<button type="button" class="link plain" onclick={() => select(f.nodes[0])}>{f.text}</button>
					{#if f.action}<button type="button" class="link" onclick={() => act(f)}>{t(`depmap.action.${f.action}`)}</button>{/if}
				</div>
			{:else}
				<p class="muted small">{t('depmap.noFindings')}</p>
			{/each}
		</div>
	</aside>
</div>

<style>
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		align-items: center;
		margin: 8px 0 4px;
	}
	.search {
		max-width: 220px;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}
	.kind {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 2px 9px;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: none;
		color: var(--text-secondary);
		cursor: pointer;
		font-size: 12px;
	}
	.kind.off {
		opacity: 0.35;
	}
	.dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
	}
	.check {
		display: flex;
		gap: 6px;
		align-items: center;
		font-size: 12px;
	}
	.keys {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		margin: 0 0 8px;
	}
	.sw {
		display: inline-block;
		width: 14px;
		height: 3px;
		margin-right: 5px;
		vertical-align: middle;
	}
	.sw.red,
	.red {
		background: none;
		color: #e5534b;
	}
	.sw.red {
		background: #e5534b;
	}
	.sw.green {
		background: #3fb27f;
	}
	.green {
		color: #3fb27f;
	}
	.sw.orange {
		background: #f0a33a;
	}
	.body {
		display: grid;
		grid-template-columns: 1fr 320px;
		gap: 12px;
		height: calc(100vh - 250px);
		min-height: 480px;
	}
	@media (max-width: 900px) {
		.body {
			grid-template-columns: 1fr;
			height: auto;
		}
		.graph {
			height: 70vh;
		}
	}
	.graph {
		position: relative;
		border: 1px solid var(--border);
		border-radius: 10px;
		background: var(--bg-surface, transparent);
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
		gap: 6px;
		padding: 12px;
		border: 1px solid var(--accent);
		border-radius: 10px;
	}
	.card h3 {
		margin: 0;
		font-size: 15px;
	}
	.card img {
		max-width: 100%;
		max-height: 160px;
		object-fit: contain;
		border-radius: 6px;
		align-self: flex-start;
	}
	.kind-label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 700;
	}
	.row {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.row select {
		flex: 1;
	}
	ul {
		margin: 4px 0 0;
		padding-left: 18px;
		max-height: 180px;
		overflow-y: auto;
	}
	h4 {
		margin: 6px 0 0;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.findings {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.finding {
		margin: 0;
		padding: 5px 8px;
		border-radius: 6px;
		border-left: 3px solid var(--border);
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 2px;
	}
	.finding.warn {
		border-left-color: #f0a33a;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: inherit;
		text-align: left;
	}
	.link.plain {
		color: inherit;
	}
	.danger {
		color: var(--error, #e5534b);
		margin-left: auto;
	}
	a {
		color: var(--accent);
	}
	.hint {
		padding: 12px;
		border: 1px dashed var(--border);
		border-radius: 10px;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
