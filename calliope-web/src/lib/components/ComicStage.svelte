<script lang="ts">
	/**
	 * Panels stage of a webtoon/manga project: one panel per clip. Panels are
	 * planned from the script, rendered as images, and their speech bubbles are
	 * dragged into place here; the export lays everything out (agent/comic.py).
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import ActivityInline from '$lib/components/ActivityInline.svelte';
	import MangaPages from '$lib/components/MangaPages.svelte';
	import ColorizeDialog from '$lib/components/ColorizeDialog.svelte';
	import { toStore } from 'svelte/store';
	import { assetUrl, comicApi, type Bubble, type Panel } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import Card from '$lib/components/ui/Card.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
	import RenderStylePicker from '$lib/components/RenderStylePicker.svelte';

	interface Props {
		projectId: number;
		format: string;
	}
	let { projectId, format }: Props = $props();

	const client = useQueryClient();
	const panelsQuery = createQuery(
		toStore(() => ({
			queryKey: ['panels', projectId],
			queryFn: () => comicApi.list(projectId),
			// Poll while any panel is still rendering.
			refetchInterval: (q: { state: { data?: Panel[] } }) =>
				(q.state.data ?? []).some((p) => p.job_status === 'pending' || p.job_status === 'running')
					? 4000
					: false,
		})),
	);
	const panels = $derived($panelsQuery.data ?? []);
	const isManga = $derived(format === 'manga');

	let busy = $state(false);
	let rtl = $state(true);
	let exported = $state<string[]>([]);
	let confirmReplan = $state(false);
	let openPrompt = $state<number | null>(null);
	let promptDrafts = $state<Record<number, string>>({});
	let colorizePath = $state<string | null>(null);
	let colorizeOpen = $state(false);

	async function refresh() {
		await client.invalidateQueries({ queryKey: ['panels', projectId] });
	}

	async function run<T>(fn: () => Promise<T>, ok?: (r: T) => string) {
		busy = true;
		try {
			const r = await fn();
			if (ok) toast.success(ok(r));
			await refresh();
			return r;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}

	const plan = (replan = false) =>
		run(() => comicApi.plan(projectId, replan), (r) => t('comic.planned', { n: r.length }));
	const renderMissing = () =>
		run(() => comicApi.render(projectId, { missing_only: true }), (r) => t('comic.queued', { n: r.queued }));
	const renderOne = (p: Panel, reseed: boolean) =>
		run(() => comicApi.render(projectId, { panel_ids: [p.id], reseed }), () => t('comic.queued', { n: 1 }));

	async function doExport() {
		const r = await run(() => comicApi.export(projectId, rtl), () => t('comic.exported'));
		if (r) exported = r.files;
	}

	/** Where a bubble sits when it has no saved position (mirrors agent/comic.py). */
	function autoXY(p: Panel, n: number): [number, number] {
		const first = isManga && rtl ? 0.72 : 0.28;
		const x = n % 2 === 0 ? first : 1 - first;
		const y = (isManga && p.caption ? 0.3 : 0.15) + 0.18 * Math.floor(n / 2);
		return [x, y];
	}

	function bubbleXY(p: Panel, b: Bubble, n: number): [number, number] {
		const [ax, ay] = autoXY(p, n);
		return [b.x ?? ax, b.y ?? ay];
	}

	async function saveBubbles(p: Panel, bubbles: Bubble[]) {
		try {
			await comicApi.update(p.id, { bubbles });
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	// ── drag ──
	let drag = $state<{ panel: Panel; index: number; rect: DOMRect; x: number; y: number } | null>(null);

	function startDrag(e: PointerEvent, p: Panel, index: number) {
		const frame = (e.currentTarget as HTMLElement).closest('.frame') as HTMLElement;
		const [x, y] = bubbleXY(p, p.bubbles[index], index);
		drag = { panel: p, index, rect: frame.getBoundingClientRect(), x, y };
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		e.preventDefault();
	}

	function moveDrag(e: PointerEvent) {
		if (!drag) return;
		const clamp = (v: number) => Math.min(0.95, Math.max(0.05, v));
		drag.x = clamp((e.clientX - drag.rect.left) / drag.rect.width);
		drag.y = clamp((e.clientY - drag.rect.top) / drag.rect.height);
	}

	function endDrag() {
		if (!drag) return;
		const { panel, index, x, y } = drag;
		drag = null;
		const bubbles = panel.bubbles.map((b, i) =>
			i === index ? { ...b, x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 } : b,
		);
		void saveBubbles(panel, bubbles);
	}

	function shownXY(p: Panel, b: Bubble, n: number): [number, number] {
		if (drag && drag.panel.id === p.id && drag.index === n) return [drag.x, drag.y];
		return bubbleXY(p, b, n);
	}

	function editText(p: Panel, n: number, text: string) {
		if (text === p.bubbles[n].text) return;
		void saveBubbles(p, p.bubbles.map((b, i) => (i === n ? { ...b, text } : b)));
	}

	function resetPositions(p: Panel) {
		void saveBubbles(p, p.bubbles.map((b) => ({ ...b, x: null, y: null })));
	}

	async function savePrompt(p: Panel) {
		const prompt = promptDrafts[p.id];
		if (prompt === undefined || prompt === p.prompt) return;
		await run(() => comicApi.update(p.id, { prompt }), () => t('comic.promptSaved'));
	}

	const rendered = $derived(panels.filter((p) => p.image_path).length);

	/** Keep a bubble (centred on x/y, 0..1) fully inside its frame, as the export does. */
	function keepInside(node: HTMLElement, xy: [number, number]) {
		let current = xy;
		const apply = () => {
			const frame = node.parentElement;
			if (!frame) return;
			const [w, h] = [frame.clientWidth, frame.clientHeight];
			const [bw, bh] = [node.offsetWidth, node.offsetHeight];
			const clamp = (v: number, half: number, size: number) => Math.min(Math.max(v, half + 4), size - half - 4);
			node.style.left = `${clamp(current[0] * w, bw / 2, w)}px`;
			node.style.top = `${clamp(current[1] * h, bh / 2, h)}px`;
		};
		const observer = new ResizeObserver(apply);
		observer.observe(node.parentElement!);
		observer.observe(node);
		apply();
		return {
			update(next: [number, number]) {
				current = next;
				apply();
			},
			destroy: () => observer.disconnect(),
		};
	}
</script>

<svelte:window onpointermove={moveDrag} onpointerup={endDrag} />

<header class="stage-head">
	<div>
		<h2>{t('nav.panels')}</h2>
		<ActivityInline {projectId} kinds={['panels']} />
		<p class="muted">
			{isManga ? t('comic.leadManga') : t('comic.leadWebtoon')}
			{#if panels.length}· {t('comic.progress', { done: rendered, n: panels.length })}{/if}
		</p>
		<RenderStylePicker {projectId} />
	</div>
	<div class="actions">
		{#if panels.length === 0}
			<Button variant="primary" disabled={busy} onclick={() => plan(false)}>
				<Icon name="sparkle" size={15} /> {t('comic.plan')}
			</Button>
		{:else}
			<Button variant="ghost" disabled={busy} onclick={() => (confirmReplan = true)}>{t('comic.replan')}</Button>
			<Button variant="secondary" disabled={busy} onclick={() => plan(false)}>{t('comic.planNew')}</Button>
			<Button variant="secondary" disabled={busy || rendered === panels.length} onclick={renderMissing}>
				<Icon name="image" size={15} /> {t('comic.renderMissing')}
			</Button>
			{#if isManga}
				<label class="rtl"><input type="checkbox" bind:checked={rtl} /> {t('comic.rtl')}</label>
			{/if}
			<Button variant="primary" disabled={busy || rendered === 0} onclick={doExport}>
				<Icon name="download" size={15} /> {t('comic.export')}
			</Button>
		{/if}
	</div>
</header>

{#if exported.length}
	<Card>
		<div class="exports">
			<strong>{t('comic.exportFiles')}</strong>
			{#each exported as f (f)}
				<a href={assetUrl(f)} target="_blank" rel="noopener">{f.split('/').pop()}</a>
			{/each}
		</div>
	</Card>
{/if}

{#if isManga && panels.length}
	<MangaPages {projectId} {panels} {rtl} />
{/if}

{#if panels.length === 0 && !$panelsQuery.isLoading}
	<Card><p class="muted">{t('comic.empty')}</p></Card>
{/if}

<div class="grid">
	{#each panels as p, idx (p.id)}
		<Card>
			{#snippet header()}
				<div class="panel-head">
					<span class="num">#{idx + 1}</span>
					{#if p.caption}<span class="caption">{p.caption}</span>{/if}
					<span class="status status-{p.job_status ?? (p.image_path ? 'done' : 'none')}">
						{p.job_status === 'running' || p.job_status === 'pending'
							? t('comic.rendering')
							: p.job_status === 'failed'
								? t('comic.failed')
								: p.image_path
									? ''
									: t('comic.notRendered')}
					</span>
				</div>
			{/snippet}
			<div class="frame" style="aspect-ratio: {p.width} / {p.height}" class:manga={isManga}>
				{#if p.image_path}
					<img src={`${assetUrl(p.image_path)}&v=${p.job_id ?? 0}`} alt="" draggable="false" />
				{:else}
					<div class="placeholder"><Icon name="image" size={28} /></div>
				{/if}
				{#each p.bubbles as b, n (n)}
					{@const xy = shownXY(p, b, n)}
					<div
						class="bubble"
						class:oval={isManga}
						class:dragging={drag?.panel.id === p.id && drag?.index === n}
						use:keepInside={xy}
						title={b.speaker}
						role="button"
						tabindex="-1"
						onpointerdown={(e) => startDrag(e, p, n)}
					>
						{b.text}
					</div>
				{/each}
			</div>
			{#if p.job_error}<p class="error">{p.job_error}</p>{/if}
			{#if p.bubbles.length}
				<div class="bubble-list">
					{#each p.bubbles as b, n (n)}
						<label>
							<span class="speaker">{b.speaker}</span>
							<input
								class="field-input"
								value={b.text}
								onchange={(e) => editText(p, n, e.currentTarget.value)}
							/>
						</label>
					{/each}
					<button type="button" class="link" onclick={() => resetPositions(p)}>{t('comic.resetBubbles')}</button>
				</div>
			{/if}
			<div class="row">
				<Button size="sm" variant="secondary" disabled={busy} onclick={() => renderOne(p, false)}>
					{p.image_path ? t('comic.rerender') : t('comic.render')}
				</Button>
				<Button size="sm" variant="ghost" disabled={busy} onclick={() => renderOne(p, true)}>
					<Icon name="refresh" size={14} /> {t('comic.reroll')}
				</Button>
				<button type="button" class="link" onclick={() => (openPrompt = openPrompt === p.id ? null : p.id)}>
					{t('comic.prompt')}
				</button>
				{#if p.image_path}
					<button type="button" class="link" onclick={() => {
						colorizePath = p.image_path;
						colorizeOpen = true;
					}}>{t('colorize.button')}</button>
				{/if}
				<span class="seed">seed {p.seed}</span>
			</div>
			{#if openPrompt === p.id}
				<textarea
					class="field-textarea"
					rows="5"
					value={promptDrafts[p.id] ?? p.prompt}
					oninput={(e) => (promptDrafts[p.id] = e.currentTarget.value)}
				></textarea>
				<div class="row">
					<Button size="sm" variant="primary" disabled={busy} onclick={() => savePrompt(p)}>
						{t('comic.savePrompt')}
					</Button>
					<span class="muted small">{t('comic.promptHint')}</span>
				</div>
			{/if}
		</Card>
	{/each}
</div>

<ColorizeDialog bind:open={colorizeOpen} imagePath={colorizePath} {projectId} />

<ConfirmDialog
	bind:open={confirmReplan}
	title={t('comic.replan')}
	message={t('comic.replanConfirm')}
	confirmLabel={t('comic.replan')}
	onconfirm={() => plan(true)}
/>

<style>
	.stage-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 16px;
		margin-bottom: 16px;
		flex-wrap: wrap;
	}
	.stage-head h2 {
		margin: 0 0 4px;
	}
	.actions {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.rtl {
		display: flex;
		gap: 6px;
		align-items: center;
		font-size: 13px;
		color: var(--text-secondary);
	}
	.muted {
		color: var(--text-muted);
		margin: 0;
	}
	.small {
		font-size: 12px;
	}
	.exports {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		align-items: center;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
		gap: 16px;
		margin-top: 16px;
	}
	.panel-head {
		display: flex;
		gap: 8px;
		align-items: center;
		font-size: 13px;
	}
	.num {
		font-weight: 600;
	}
	.caption {
		padding: 1px 6px;
		background: #111;
		color: #fff;
		font-size: 11px;
		font-weight: 600;
	}
	.status {
		margin-left: auto;
		color: var(--text-muted);
		font-size: 12px;
	}
	.status-failed {
		color: #fca5a5;
	}
	.frame {
		position: relative;
		width: 100%;
		overflow: hidden;
		border-radius: 6px;
		background: var(--bg-elevated, #18181b);
		user-select: none;
		touch-action: none;
	}
	.frame.manga img {
		filter: grayscale(1);
	}
	.frame img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.placeholder {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		color: var(--text-muted);
	}
	.bubble {
		position: absolute;
		transform: translate(-50%, -50%);
		/* Own width, not the space left of `left`, so bubbles near the edge keep their shape. */
		width: max-content;
		max-width: 46%;
		padding: 6px 12px;
		border: 2px solid #000;
		border-radius: 16px;
		background: #fff;
		color: #000;
		font-family: 'Comic Sans MS', 'Comic Neue', cursive;
		font-size: 12px;
		font-weight: 700;
		line-height: 1.25;
		text-align: center;
		cursor: grab;
	}
	.bubble.oval {
		max-width: 58%;
		border-radius: 50%;
		padding: 10px 16px;
	}
	.bubble.dragging {
		cursor: grabbing;
		box-shadow: 0 0 0 3px var(--accent);
	}
	.error {
		color: #fca5a5;
		font-size: 12px;
	}
	.bubble-list {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 10px;
	}
	.bubble-list label {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.speaker {
		font-size: 11px;
		color: var(--text-muted);
	}
	.row {
		display: flex;
		gap: 8px;
		align-items: center;
		margin-top: 10px;
		flex-wrap: wrap;
	}
	.link {
		border: 0;
		background: none;
		color: var(--accent);
		font: inherit;
		font-size: 12px;
		cursor: pointer;
		padding: 0;
		align-self: flex-start;
	}
	.seed {
		margin-left: auto;
		font-size: 11px;
		color: var(--text-muted);
	}
</style>
