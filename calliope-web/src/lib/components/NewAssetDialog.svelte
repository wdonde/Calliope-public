<script lang="ts">
	/**
	 * Create a character/location/item from notes and/or the user's own pictures.
	 * The backend describes the look (vision model when there are pictures) and
	 * renders the sheet with the pictures as references (agent/asset_create.py).
	 */
	import { createQuery } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetLibraryApi, assetUrl, libraryApi, playgroundApi, type AssetKind } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	interface Props {
		open: boolean;
		/** Without a project the asset goes straight into the asset library. */
		projectId?: number;
		kind: AssetKind;
		/** Let the user pick character/location/item (the library has no tabs). */
		chooseKind?: boolean;
		oncreated: () => void;
	}
	let { open = $bindable(), projectId, kind, chooseKind = false, oncreated }: Props = $props();

	let picked = $state<AssetKind | null>(null);
	const current = $derived<AssetKind>(picked ?? kind);

	const MAX = 6;
	let name = $state('');
	let notes = $state('');
	/** A new file from disk, or an image Calliope already has (path). */
	type Pic = { src: string; file?: File; path?: string };
	let pics = $state<Pic[]>([]);
	let generate = $state(true);
	let busy = $state(false);
	let browsing = $state(false);

	// Images already in Calliope (uploads and Playground results), newest first.
	const existingQuery = createQuery(
		toStore(() => ({ queryKey: ['library-media'], queryFn: libraryApi.list, enabled: open && browsing })),
	);
	const existing = $derived(
		($existingQuery.data ?? []).filter((m) => m.kind === 'image').sort((a, b) => b.mtime.localeCompare(a.mtime)),
	);
	const chosenPaths = $derived(new Set(pics.map((p) => p.path).filter(Boolean)));

	function addFiles(list: FileList | File[] | null) {
		const images = Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));
		pics = [...pics, ...images.map((file) => ({ src: URL.createObjectURL(file), file }))].slice(0, MAX);
	}

	function toggleExisting(path: string) {
		if (chosenPaths.has(path)) pics = pics.filter((p) => p.path !== path);
		else if (pics.length < MAX) pics = [...pics, { src: assetUrl(path) ?? '', path }];
	}

	function onPaste(e: ClipboardEvent) {
		const images = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
		if (images.length) addFiles(images);
	}

	async function submit() {
		if (!name.trim()) return;
		busy = true;
		try {
			const paths: string[] = [];
			for (const p of pics) paths.push(p.path ?? (await playgroundApi.upload(p.file!)).path);
			const payload = { kind: current, name: name.trim(), prompt: notes, image_paths: paths, generate };
			if (projectId) await assetLibraryApi.create(projectId, payload);
			else await assetLibraryApi.createInLibrary(payload);
			toast.success(t('assets.newCreated', { name: name.trim() }));
			name = '';
			notes = '';
			pics = [];
			browsing = false;
			open = false;
			oncreated();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title={t(`assets.new.${current}`)} size="lg" onclose={() => (open = false)}>
	<div class="form" onpaste={onPaste}>
		{#if chooseKind}
			<div class="kinds">
				{#each ['character', 'location', 'item'] as const as k (k)}
					<button type="button" class:active={current === k} onclick={() => (picked = k)}>{t(`assets.new.${k}`)}</button>
				{/each}
			</div>
		{/if}
		<label>
			<span class="field-label">{t('assets.new.name')}</span>
			<input class="field-input" bind:value={name} placeholder="Captain Edward 'Ripper Eddy' Reed" />
		</label>
		<label>
			<span class="field-label">{t('assets.new.notes')}</span>
			<textarea class="field-textarea" rows="4" bind:value={notes} placeholder={t('assets.new.notesPlaceholder')}></textarea>
		</label>
		<div>
			<span class="field-label">{t('assets.new.pictures', { max: MAX })}</span>
			<div class="pics">
				{#each pics as p, i (p.src)}
					<figure>
						<img src={p.src} alt="" />
						<button type="button" aria-label={t('common.delete')} onclick={() => (pics = pics.filter((_, k) => k !== i))}>×</button>
					</figure>
				{/each}
				{#if pics.length < MAX}
					<label class="add" title={t('assets.new.fromDisk')}>
						+
						<input type="file" accept="image/*" multiple hidden onchange={(e) => addFiles(e.currentTarget.files)} />
					</label>
				{/if}
			</div>
			<div class="pick-row">
				<button type="button" class="link" onclick={() => (browsing = !browsing)}>
					{browsing ? t('assets.new.hideExisting') : t('assets.new.fromExisting')}
				</button>
				<span class="muted small">{t('assets.new.pasteHint')}</span>
			</div>
			{#if browsing}
				<div class="existing">
					{#each existing as m (m.path)}
						<button type="button" class="thumb" class:chosen={chosenPaths.has(m.path)} title={m.name}
							disabled={!chosenPaths.has(m.path) && pics.length >= MAX} onclick={() => toggleExisting(m.path)}>
							<img src={assetUrl(m.path)} alt={m.name} loading="lazy" />
						</button>
					{:else}
						<p class="muted small">{$existingQuery.isLoading ? '…' : t('assets.new.noExisting')}</p>
					{/each}
				</div>
			{/if}
			<p class="muted small">{pics.length ? t('assets.new.picturesHint') : t('assets.new.noPicturesHint')}</p>
		</div>
		<label class="check">
			<input type="checkbox" bind:checked={generate} />
			{t('assets.new.generate')}
		</label>
	</div>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.cancel')}</Button>
		<Button variant="primary" disabled={!name.trim()} loading={busy} onclick={submit}>{t('assets.new.create')}</Button>
	{/snippet}
</Modal>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.kinds {
		display: flex;
		gap: 6px;
	}
	.kinds button {
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-secondary);
		border-radius: 999px;
		padding: 4px 12px;
		cursor: pointer;
	}
	.kinds button.active {
		border-color: var(--accent);
		color: var(--accent);
	}
	.check {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
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
		margin: 4px 0 0;
	}
	.small {
		font-size: 12px;
	}
</style>
