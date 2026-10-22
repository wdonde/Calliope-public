<script lang="ts" module>
	/** A picture from disk (file) or one Calliope already has (path). */
	export type Pic = { src: string; file?: File; path?: string };
</script>

<script lang="ts">
	/**
	 * The asset's own pictures: previews with a small remove button, "+" for
	 * files from disk, a grid of images already in Calliope (uploads and
	 * Playground results), and Ctrl+V to paste. Used by New asset and Edit.
	 */
	import { createQuery } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetUrl, libraryApi, playgroundApi } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Icon from '$lib/components/ui/Icon.svelte';

	interface Props {
		pics: Pic[];
		max?: number;
		/** Let the user pick reference pictures and reorder them (Edit dialog). */
		selectable?: boolean;
		/** Positions in `pics` picked as references; in picture order they are image 1, 2, ... */
		selected?: number[];
	}
	let { pics = $bindable(), max = 6, selectable = false, selected = $bindable([]) }: Props = $props();

	/** "image n" for a picked picture (its rank among the picked ones), else 0. */
	const imageNo = (i: number) => (selected.includes(i) ? [...selected].sort((a, b) => a - b).indexOf(i) + 1 : 0);

	function toggleRef(i: number) {
		if (selected.includes(i)) selected = selected.filter((k) => k !== i);
		else if (selected.length < 6) selected = [...selected, i].sort((a, b) => a - b);
	}

	/** Move a picture left/right; picks move with it. */
	function move(i: number, step: number) {
		const j = i + step;
		if (j < 0 || j >= pics.length) return;
		const next = [...pics];
		[next[i], next[j]] = [next[j], next[i]];
		pics = next;
		selected = selected.map((k) => (k === i ? j : k === j ? i : k)).sort((a, b) => a - b);
	}

	function remove(i: number) {
		pics = pics.filter((_, k) => k !== i);
		selected = selected.filter((k) => k !== i).map((k) => (k > i ? k - 1 : k));
	}

	let browsing = $state(false);
	const existingQuery = createQuery(
		toStore(() => ({ queryKey: ['library-media'], queryFn: libraryApi.list, enabled: browsing })),
	);
	const existing = $derived(
		($existingQuery.data ?? []).filter((m) => m.kind === 'image').sort((a, b) => b.mtime.localeCompare(a.mtime)),
	);
	const chosen = $derived(new Set(pics.map((p) => p.path).filter(Boolean)));

	function addFiles(list: FileList | File[] | null) {
		const images = Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));
		pics = [...pics, ...images.map((file) => ({ src: URL.createObjectURL(file), file }))].slice(0, max);
	}

	function toggle(path: string) {
		if (chosen.has(path)) pics = pics.filter((p) => p.path !== path);
		else if (pics.length < max) pics = [...pics, { src: assetUrl(path) ?? '', path }];
	}

	let dragging = $state(false);
	// Pictures without color (manga pages, ink drawings): the colors must come from the text.
	let gray = $state<Record<string, boolean>>({});
	const anyGray = $derived(pics.some((p) => gray[p.src]));

	function checkGray(e: Event, src: string) {
		const img = e.currentTarget as HTMLImageElement;
		try {
			const c = document.createElement('canvas');
			c.width = c.height = 48;
			const g = c.getContext('2d', { willReadFrequently: true });
			if (!g) return;
			g.drawImage(img, 0, 0, 48, 48);
			const d = g.getImageData(0, 0, 48, 48).data;
			let colored = 0;
			for (let i = 0; i < d.length; i += 4) {
				const max = Math.max(d[i], d[i + 1], d[i + 2]);
				const min = Math.min(d[i], d[i + 1], d[i + 2]);
				if (max > 0 && (max - min) / max > 0.16) colored++;
			}
			gray = { ...gray, [src]: colored / (d.length / 4) < 0.03 };
		} catch {
			// unreadable image (e.g. cross-origin): no badge
		}
	}

	/** "Paste screenshot": read an image straight from the clipboard (Win+Shift+S, Print, copy image). */
	async function pasteFromClipboard() {
		try {
			const items = await navigator.clipboard.read();
			const files: File[] = [];
			for (const item of items) {
				const type = item.types.find((t) => t.startsWith('image/'));
				if (type) {
					const blob = await item.getType(type);
					files.push(new File([blob], `screenshot-${Date.now()}.${type.split('/')[1] || 'png'}`, { type }));
				}
			}
			if (files.length) addFiles(files);
			else toast.info(t('assets.new.clipboardEmpty'));
		} catch {
			// Browsers that block clipboard reading still deliver Ctrl+V.
			toast.info(t('assets.new.clipboardBlocked'));
		}
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		addFiles(Array.from(e.dataTransfer?.files ?? []));
	}

	function onPaste(e: ClipboardEvent) {
		const images = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
		if (images.length) addFiles(images);
	}

	/** Paths for the API: new files are uploaded first. */
	export async function resolvePaths(): Promise<string[]> {
		const out: string[] = [];
		for (const p of pics) out.push(p.path ?? (await playgroundApi.upload(p.file!)).path);
		return out;
	}
</script>

<svelte:window onpaste={onPaste} />

<div
	class="pics"
	class:dragging
	role="group"
	aria-label={t('assets.new.dropHint')}
	ondragover={(e) => {
		e.preventDefault();
		dragging = true;
	}}
	ondragleave={() => (dragging = false)}
	ondrop={onDrop}
>
	{#each pics as p, i (p.src)}
		<figure>
			<img src={p.src} alt="" onload={(e) => checkGray(e, p.src)} />
			{#if gray[p.src]}<span class="bw" title={t('assets.new.bwTitle')}>B/W</span>{/if}
			<button type="button" aria-label={t('common.delete')} onclick={() => remove(i)}>×</button>
			{#if selectable}
				<div class="controls">
					<button type="button" class="ref" class:on={imageNo(i) > 0} title={t('assets.pick.refTitle')}
						disabled={!imageNo(i) && selected.length >= 6} onclick={() => toggleRef(i)}>
						{imageNo(i) ? t('assets.pick.imageN', { n: imageNo(i) }) : t('assets.pick.ref')}
					</button>
					<button type="button" class="mv" aria-label={t('assets.pick.left')} disabled={i === 0} onclick={() => move(i, -1)}>‹</button>
					<button type="button" class="mv" aria-label={t('assets.pick.right')} disabled={i === pics.length - 1} onclick={() => move(i, 1)}>›</button>
				</div>
			{/if}
		</figure>
	{/each}
	{#if pics.length < max}
		<label class="add" title={t('assets.new.fromDisk')}>
			+
			<input type="file" accept="image/*" multiple hidden onchange={(e) => addFiles(e.currentTarget.files)} />
		</label>
		<button type="button" class="add paste" title={t('assets.new.pasteHint')} onclick={pasteFromClipboard}>
			<Icon name="copy" size={20} />
			<span>{t('assets.new.pasteButton')}</span>
		</button>
	{/if}
</div>
{#if anyGray}<p class="bw-hint">{t('assets.new.bwHint')}</p>{/if}
<div class="pick-row">
	<button type="button" class="link" onclick={() => (browsing = !browsing)}>
		{browsing ? t('assets.new.hideExisting') : t('assets.new.fromExisting')}
	</button>
	<span class="muted small">{t('assets.new.pasteHint')}</span>
</div>
{#if browsing}
	<div class="existing">
		{#each existing as m (m.path)}
			<button type="button" class="thumb" class:chosen={chosen.has(m.path)} title={m.name}
				disabled={!chosen.has(m.path) && pics.length >= max} onclick={() => toggle(m.path)}>
				<img src={assetUrl(m.path)} alt={m.name} loading="lazy" />
			</button>
		{:else}
			<p class="muted small">{$existingQuery.isLoading ? '…' : t('assets.new.noExisting')}</p>
		{/each}
	</div>
{/if}

<style>
	.pics {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 4px;
	}
	figure {
		position: relative;
		margin: 0;
	}
	figure img,
	.add {
		width: 88px;
		height: 88px;
		object-fit: cover;
		border-radius: 8px;
		border: 1px solid var(--border);
	}
	.controls {
		display: flex;
		gap: 3px;
		margin-top: 4px;
		width: 88px;
	}
	.controls button {
		position: static;
		height: 20px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface-2, transparent);
		color: var(--text-secondary);
		cursor: pointer;
		font-size: 11px;
		line-height: 1;
	}
	.controls .ref {
		flex: 1;
		font-weight: 700;
		width: auto;
		padding: 0 2px;
		font-size: 10px;
		white-space: nowrap;
	}
	.controls .ref.on {
		background: var(--accent);
		border-color: var(--accent);
		color: #fff;
	}
	.controls .mv {
		width: 16px;
		padding: 0;
	}
	.controls button:disabled {
		opacity: 0.35;
		cursor: default;
	}
	figure button {
		position: absolute;
		top: 2px;
		right: 2px;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		border: none;
		background: rgba(0, 0, 0, 0.7);
		color: #fff;
		cursor: pointer;
		line-height: 1;
	}
	.add {
		display: grid;
		place-items: center;
		font-size: 28px;
		color: var(--text-muted);
		cursor: pointer;
		border-style: dashed;
	}
	.pics.dragging {
		outline: 2px dashed var(--accent);
		outline-offset: 4px;
		border-radius: 8px;
	}
	.paste {
		background: none;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 4px;
		font-size: 11px;
		padding: 4px;
		text-align: center;
	}
	.bw {
		position: absolute;
		left: 4px;
		top: 4px;
		background: rgba(0, 0, 0, 0.75);
		color: #fff;
		font-size: 10px;
		font-weight: 700;
		padding: 1px 5px;
		border-radius: 4px;
	}
	.bw-hint {
		margin: 6px 0 0;
		font-size: 12px;
		color: var(--warning, #e0a040);
	}
	.pick-row {
		display: flex;
		gap: 12px;
		align-items: baseline;
		margin-top: 6px;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: 12px;
	}
	.existing {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
		gap: 6px;
		max-height: 240px;
		overflow-y: auto;
		margin-top: 8px;
		padding: 4px;
		border: 1px solid var(--border);
		border-radius: 8px;
	}
	.thumb {
		padding: 0;
		border: 2px solid transparent;
		border-radius: 6px;
		background: none;
		cursor: pointer;
		aspect-ratio: 1;
		overflow: hidden;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.thumb.chosen {
		border-color: var(--accent);
	}
	.thumb:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
