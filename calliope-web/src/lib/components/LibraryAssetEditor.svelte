<script lang="ts">
	/**
	 * Edit a library asset: name, tags, category, look/description, the image
	 * prompt sent to ComfyUI, and its own pictures. "Save & render" renders the
	 * sheet again -- with the pictures as references, or from the prompt alone.
	 */
	import { assetLibraryApi, assetUrl, type LibraryAsset } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import PicturePicker, { type Pic } from '$lib/components/PicturePicker.svelte';

	interface Props {
		open: boolean;
		asset: LibraryAsset | null;
		onsaved: () => void;
	}
	let { open = $bindable(), asset, onsaved }: Props = $props();

	const CATEGORIES = ['weapon', 'vehicle', 'device', 'furniture', 'clothing', 'tool', 'creature', 'prop'];
	let name = $state('');
	let category = $state('');
	let tags = $state('');
	let fields = $state<Record<string, string>>({});
	let pics = $state<Pic[]>([]);
	let picker = $state<PicturePicker>();
	let busy = $state(false);

	const isChar = $derived(asset?.kind === 'character');
	const textFields = $derived(
		// Library characters hold only who they are; outfit, personality and role are per project.
		isChar
			? [{ key: 'appearance', label: t('assets.edit.identity'), rows: 12 }]
			: [{ key: 'description', label: t('assets.description'), rows: 4 }],
	);

	// Load the asset into the form each time the dialog opens.
	let loadedFor = $state<number | null>(null);
	$effect(() => {
		if (!open || !asset || loadedFor === asset.id) return;
		loadedFor = asset.id;
		name = asset.name;
		category = asset.category ?? '';
		tags = asset.tags.join(', ');
		const d = asset.data as Record<string, unknown>;
		fields = Object.fromEntries(
			['appearance', 'role', 'age', 'personality', 'description', 'consistency_prompt'].map((k) => [k, String(d[k] ?? '')]),
		);
		pics = ((d.source_images as string[] | undefined) ?? []).map((path) => ({ src: assetUrl(path) ?? '', path }));
		savedState = snapshot();
		rendering = false;
	});
	$effect(() => {
		if (!open) loadedFor = null;
	});

	// Render works on the saved asset, so it waits until the form is saved.
	let savedState = $state('');
	let rendering = $state(false);
	const snapshot = () => JSON.stringify([name, category, tags, fields, pics.map((p) => p.path ?? p.src)]);
	const dirty = $derived(savedState !== '' && snapshot() !== savedState);

	let generating = $state(false);
	const lookKey = $derived(isChar ? 'appearance' : 'description');

	/** Draft the look from the pictures, name and current text (as notes); fills the form. */
	async function generate() {
		if (!asset) return;
		generating = true;
		try {
			await picker?.resolvePaths().then((paths) =>
				assetLibraryApi.update(asset!.id, { source_images: paths }),
			);
			// The current text is not sent as notes: notes win over pictures and would keep old mistakes.
			const r = await assetLibraryApi.describeLibrary(asset.id);
			fields[lookKey] = r.text;
			if (isChar) {
				tags = r.tags.join(', ');
				return;
			}
			// Suggestions only add keys the asset does not have yet (several aliases are fine);
			// identity tags are read again from the new sheet on save.
			const current = tags.split(',').map((s) => s.trim()).filter(Boolean);
			const keyOf = (tag: string) => (tag.includes(':') ? tag.split(':')[0].toLowerCase() : tag.toLowerCase());
			const have = new Set(current.map(keyOf));
			const lower = new Set(current.map((s) => s.toLowerCase()));
			const added = r.tags.filter((tag) => !lower.has(tag.toLowerCase()) && (keyOf(tag) === 'alias' || !have.has(keyOf(tag))));
			tags = [...current, ...added].join(', ');
			if (asset.kind === 'item' && !category && r.category) category = r.category;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			generating = false;
		}
	}

	async function save() {
		if (!asset || !name.trim()) return;
		// An empty look is written automatically before saving.
		if (!(fields[lookKey] ?? '').trim()) await generate();
		busy = true;
		try {
			const keys = [...textFields.map((f) => f.key), 'consistency_prompt'];
			const saved = await assetLibraryApi.update(asset.id, {
				name: name.trim(),
				...(isChar ? {} : { tags: tags.split(',').map((s) => s.trim()).filter(Boolean) }),
				...(asset.kind === 'item' ? { category: category || null } : {}),
				data: Object.fromEntries(keys.map((k) => [k, fields[k] ?? ''])),
				source_images: (await picker?.resolvePaths()) ?? [],
			});
			// Take back what the server made of it (identity tags, prompt, stored picture paths).
			tags = saved.tags.join(', ');
			fields = { ...fields, consistency_prompt: String(saved.data.consistency_prompt ?? fields.consistency_prompt) };
			pics = ((saved.data.source_images as string[] | undefined) ?? []).map((path) => ({ src: assetUrl(path) ?? '', path }));
			savedState = snapshot();
			toast.success(t('assets.edit.saved'));
			onsaved();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}

	async function render() {
		if (!asset || dirty) return;
		rendering = true;
		try {
			await assetLibraryApi.libraryFromImages(asset.id);
			toast.success(t('assets.edit.renderQueued'));
			onsaved();
		} catch (err) {
			rendering = false;
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}
</script>

<Modal bind:open title={t('assets.edit.title', { name: asset?.name ?? '' })} size="xl" onclose={() => (open = false)}>
	{#if asset}
		<div class="layout">
			<div class="preview">
				{#if asset.image_path}
					<img src={assetUrl(asset.image_path)} alt={asset.name} />
				{:else}
					<div class="noimg">{t('assets.edit.noImage')}</div>
				{/if}
				<p class="muted small">
					{#if asset.source_project}{t('assets.edit.from', { project: asset.source_project.title })}<br />{/if}
					{asset.used_in?.length
						? t('assets.edit.usedIn', { projects: asset.used_in.map((p) => (p.outdated ? `${p.title} (${t('assets.edit.outdated')})` : p.title)).join(', ') })
						: t('assets.edit.unused')}
				</p>
			</div>
			<div class="form">
				<label>
					<span class="field-label">{t('assets.new.name')}</span>
					<input class="field-input" bind:value={name} />
				</label>
				{#if asset.kind === 'item'}
					<label>
						<span class="field-label">{t('assets.category')}</span>
						<input class="field-input" list="edit-categories" bind:value={category} />
						<datalist id="edit-categories">
							{#each CATEGORIES as c (c)}<option value={c}></option>{/each}
						</datalist>
					</label>
				{/if}
				<label>
					<span class="field-label">{t('assets.edit.tags')}</span>
					{#if isChar}
						<!-- Character tags are read from the sheet below (alias and role included). -->
						<input class="field-input" value={tags} readonly placeholder="alias:Ripper Eddy, faction:Union Guard" />
						<span class="muted small">{t('assets.tagsFromSheet')}</span>
					{:else}
						<input class="field-input" bind:value={tags} placeholder="alias:Ripper Eddy, faction:Union Guard" />
					{/if}
				</label>
				{#each textFields as f (f.key)}
					<label>
						<span class="field-label">
							{f.label}
							{#if f.key === lookKey}
								<button type="button" class="gen" disabled={generating} onclick={generate}>
									{generating ? t('assets.edit.generating') : t('assets.edit.generate')}
								</button>
							{/if}
						</span>
						{#if f.rows > 1}
							<textarea class="field-textarea" rows={f.rows} bind:value={fields[f.key]}></textarea>
						{:else}
							<input class="field-input" bind:value={fields[f.key]} />
						{/if}
					</label>
				{/each}
				<label>
					<span class="field-label">{t('assets.edit.prompt')}</span>
					<textarea class="field-textarea mono" rows="8" bind:value={fields.consistency_prompt}></textarea>
					<span class="muted small">{t('assets.edit.promptHint')}</span>
				</label>
				<div>
					<span class="field-label">{t('assets.new.pictures', { max: 6 })}</span>
					<PicturePicker bind:this={picker} bind:pics />
				</div>
			</div>
		</div>
	{/if}
	{#snippet footer()}
		{#if rendering}<span class="muted small status">{t('assets.edit.renderingNote')}</span>{/if}
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.close')}</Button>
		<Button variant="secondary" disabled={!dirty} loading={busy} onclick={save}>{t('assets.edit.save')}</Button>
		<Button variant="primary" disabled={dirty || busy} title={dirty ? t('assets.edit.saveFirst') : ''} onclick={render}>
			{pics.length ? t('assets.edit.renderPictures') : t('assets.edit.renderPrompt')}
		</Button>
	{/snippet}
</Modal>

<style>
	.layout {
		display: grid;
		grid-template-columns: 260px 1fr;
		gap: 20px;
	}
	@media (max-width: 760px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
	.preview img,
	.noimg {
		width: 100%;
		border-radius: 8px;
		border: 1px solid var(--border);
	}
	.noimg {
		aspect-ratio: 3 / 2;
		display: grid;
		place-items: center;
		color: var(--text-muted);
		font-size: 12px;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 12px;
		max-height: 65vh;
		overflow-y: auto;
		padding-right: 4px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.gen {
		margin-left: 8px;
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: 12px;
	}
	.gen:disabled {
		opacity: 0.5;
	}
	.status {
		margin-right: auto;
	}
	.mono {
		font-family: var(--font-mono, monospace);
		font-size: 12px;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
