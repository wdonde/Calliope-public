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
		isChar
			? [
					{ key: 'appearance', label: t('assets.appearance'), rows: 4 },
					{ key: 'role', label: t('assets.edit.role'), rows: 1 },
					{ key: 'age', label: t('assets.edit.age'), rows: 1 },
					{ key: 'personality', label: t('assets.personality'), rows: 2 },
				]
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
	});
	$effect(() => {
		if (!open) loadedFor = null;
	});

	async function save(render: boolean) {
		if (!asset || !name.trim()) return;
		busy = true;
		try {
			const keys = [...textFields.map((f) => f.key), 'consistency_prompt'];
			await assetLibraryApi.update(asset.id, {
				name: name.trim(),
				tags: tags.split(',').map((s) => s.trim()).filter(Boolean),
				...(asset.kind === 'item' ? { category: category || null } : {}),
				data: Object.fromEntries(keys.map((k) => [k, fields[k] ?? ''])),
				source_images: (await picker?.resolvePaths()) ?? [],
			});
			if (render) await assetLibraryApi.libraryFromImages(asset.id);
			toast.success(render ? t('assets.edit.savedRendering') : t('assets.edit.saved'));
			open = false;
			onsaved();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
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
						? t('assets.edit.usedIn', { projects: asset.used_in.map((p) => p.title).join(', ') })
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
					<input class="field-input" bind:value={tags} placeholder="alias:Ripper Eddy, faction:Union Guard" />
				</label>
				{#each textFields as f (f.key)}
					<label>
						<span class="field-label">{f.label}</span>
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
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.cancel')}</Button>
		<Button variant="secondary" loading={busy} onclick={() => save(false)}>{t('assets.edit.save')}</Button>
		<Button variant="primary" loading={busy} onclick={() => save(true)}>
			{pics.length ? t('assets.edit.saveRenderPictures') : t('assets.edit.saveRenderPrompt')}
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
