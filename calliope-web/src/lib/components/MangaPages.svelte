<script lang="ts">
	/**
	 * Manga pages of a comic project. The backend spreads the panels over pages
	 * and picks a layout per page (agent/manga_layout.py); here the user can try
	 * other layouts, move panels between pages, or go back to automatic.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { comicApi, type MangaPage, type Panel } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import Card from '$lib/components/ui/Card.svelte';

	interface Props {
		projectId: number;
		panels: Panel[];
		rtl: boolean;
	}
	let { projectId, panels, rtl }: Props = $props();

	const client = useQueryClient();
	const layoutQuery = createQuery(
		toStore(() => ({
			// The panel list is part of the key, so new or removed panels re-resolve the pages.
			queryKey: ['comic-layout', projectId, panels.map((p) => p.id).join(',')],
			queryFn: () => comicApi.layout(projectId),
		})),
	);
	const layout = $derived($layoutQuery.data);
	const numbers = $derived(new Map(panels.map((p, i) => [p.id, i + 1])));
	let busy = $state(false);

	async function save(pages: MangaPage[] | null) {
		busy = true;
		try {
			const next = pages ? await comicApi.saveLayout(projectId, pages) : await comicApi.resetLayout(projectId);
			client.setQueryData(['comic-layout', projectId, panels.map((p) => p.id).join(',')], next);
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}

	const copy = (): MangaPage[] => (layout?.pages ?? []).map((pg) => ({ ...pg, panels: [...pg.panels] }));

	function cycle(index: number, step: number) {
		const pages = copy();
		const options = layout?.templates[pages[index].panels.length] ?? [];
		if (options.length < 2) return;
		const at = options.indexOf(pages[index].template);
		pages[index] = { ...pages[index], template: options[(at + step + options.length) % options.length], auto: false };
		void save(pages);
	}

	/** Move the first panel of a page to the end of the page before it. */
	function toPrevious(index: number) {
		const pages = copy();
		pages[index - 1].panels.push(pages[index].panels.shift()!);
		pages[index - 1].auto = true;
		pages[index].auto = true;
		void save(pages.filter((pg) => pg.panels.length));
	}

	/** Move the last panel of a page to the start of the next one (a new page at the end). */
	function toNext(index: number) {
		const pages = copy();
		if (index === pages.length - 1) pages.push({ panels: [], template: '', auto: true });
		pages[index + 1].panels.unshift(pages[index].panels.pop()!);
		pages[index + 1].auto = true;
		pages[index].auto = true;
		void save(pages.filter((pg) => pg.panels.length));
	}

	// Cache key per page: changes when its panels, images, bubbles or layout change.
	function version(page: MangaPage): string {
		const byId = new Map(panels.map((p) => [p.id, p]));
		const text = JSON.stringify([
			page,
			rtl,
			page.panels.map((id) => {
				const p = byId.get(id);
				return [p?.image_path, p?.job_id, p?.bubbles, p?.caption];
			}),
		]);
		let h = 5381;
		for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
		return h.toString(36);
	}

	const full = (page: MangaPage | undefined) => !!page && page.panels.length >= (layout?.max_per_page ?? 6);
</script>

{#if layout}
	<Card>
		<div class="head">
			<div>
				<strong>{t('comic.pages', { n: layout.pages.length })}</strong>
				<p class="muted">{layout.custom ? t('comic.pagesCustom') : t('comic.pagesAuto')}</p>
			</div>
			{#if layout.custom}
				<Button size="sm" variant="ghost" disabled={busy} onclick={() => save(null)}>{t('comic.pagesReset')}</Button>
			{/if}
		</div>
		<div class="pages">
			{#each layout.pages as page, i (i)}
				<figure>
					<img
						src={`/api/projects/${projectId}/comic/pages/${i}.png?rtl=${rtl}&v=${version(page)}`}
						alt={t('comic.pageN', { n: i + 1 })}
						loading="lazy"
					/>
					<figcaption>
						<span class="title">
							{t('comic.pageN', { n: i + 1 })} · #{page.panels.map((id) => numbers.get(id)).join(', #')}
						</span>
						<span class="layout">
							<button type="button" disabled={busy} onclick={() => cycle(i, -1)} aria-label={t('comic.layoutPrev')}>‹</button>
							<span title={page.auto ? t('comic.layoutAuto') : ''}>{page.template}{page.auto ? ' ·A' : ''}</span>
							<button type="button" disabled={busy} onclick={() => cycle(i, 1)} aria-label={t('comic.layoutNext')}>›</button>
						</span>
						<span class="moves">
							<button type="button" class="link" disabled={busy || i === 0 || full(layout.pages[i - 1])} onclick={() => toPrevious(i)}>
								{t('comic.firstToPrev')}
							</button>
							<button type="button" class="link" disabled={busy || page.panels.length < 2 || full(layout.pages[i + 1])} onclick={() => toNext(i)}>
								{t('comic.lastToNext')}
							</button>
						</span>
					</figcaption>
				</figure>
			{/each}
		</div>
	</Card>
{/if}

<style>
	.head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
		margin-bottom: 12px;
	}
	.muted {
		color: var(--text-muted);
		margin: 2px 0 0;
		font-size: 13px;
	}
	.pages {
		display: flex;
		gap: 16px;
		overflow-x: auto;
		padding-bottom: 8px;
	}
	figure {
		margin: 0;
		flex: 0 0 220px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	img {
		width: 220px;
		aspect-ratio: 1240 / 1754;
		background: #fff;
		border: 1px solid var(--border);
		border-radius: 4px;
	}
	figcaption {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 12px;
	}
	.title {
		color: var(--text-secondary);
	}
	.layout {
		display: flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-mono, monospace);
	}
	.layout button {
		border: 1px solid var(--border);
		background: var(--surface-2, transparent);
		color: inherit;
		border-radius: 4px;
		width: 24px;
		cursor: pointer;
	}
	.moves {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: 12px;
	}
	.link:disabled,
	.layout button:disabled {
		opacity: 0.4;
		cursor: default;
	}
</style>
