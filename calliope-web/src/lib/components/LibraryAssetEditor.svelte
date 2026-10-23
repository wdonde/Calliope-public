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
	/** Pictures picked as references (positions in pics); empty = Calliope chooses. */
	let refIdx = $state<number[]>([]);
	let picker = $state<PicturePicker>();
	let busy = $state(false);

	const isChar = $derived(asset?.kind === 'character');
	const textFields = $derived(
		// Library characters hold only who they are; outfit, personality and role are per project.
		isChar
			? [
					// Own fields, linked into the sheet's Alias/Role lines; kept when the look is regenerated.
					{ key: 'alias', label: t('assets.edit.alias'), rows: 1 },
					{ key: 'role', label: t('assets.edit.role'), rows: 1 },
					{ key: 'appearance', label: t('assets.edit.identity'), rows: 12 },
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
			['alias', 'appearance', 'role', 'age', 'personality', 'description', 'consistency_prompt', 'max_refs', 'sd_tags'].map((k) => [
				k,
				String(d[k] ?? (k === 'max_refs' ? 2 : '')),
			]),
		);
		pics = ((d.source_images as string[] | undefined) ?? []).map((path) => ({ src: assetUrl(path) ?? '', path }));
		refIdx = ((d.ref_indices as number[] | undefined) ?? []).filter((i) => i < pics.length);
		savedState = snapshot();
		rendering = false;
		void loadPreview();
	});
	$effect(() => {
		if (!open) loadedFor = null;
	});

	// What a render sends right now (saved state): prompt + reference pictures.
	let preview = $state<{ prompt: string; refs: string[]; auto: boolean } | null>(null);
	async function loadPreview() {
		if (!asset) return;
		try {
			preview = await assetLibraryApi.renderPreview(asset.id);
		} catch {
			preview = null;
		}
	}

	// Render works on the saved asset, so it waits until the form is saved.
	let savedState = $state('');
	let rendering = $state(false);
	const snapshot = () => JSON.stringify([name, category, tags, fields, pics.map((p) => p.path ?? p.src), refIdx]);
	const dirty = $derived(savedState !== '' && snapshot() !== savedState);

	let generating = $state(false);
	const lookKey = $derived(isChar ? 'appearance' : 'description');

	/** Draft the look from the pictures, name and current text (as notes); fills the form. */
	async function generate() {
		if (!asset) return;
		generating = true;
		try {
			await picker?.resolvePaths().then((paths) =>
				assetLibraryApi.update(asset!.id, { source_images: paths, data: { ref_indices: refIdx } }),
			);
			// The current text is not sent as notes: notes win over pictures and would keep old mistakes.
			const r = await assetLibraryApi.describeLibrary(
				asset.id,
				isChar ? { alias: fields.alias ?? '', role: fields.role ?? '' } : {},
			);
			fields[lookKey] = r.text;
			if (isChar) {
				// Filled fields stay; empty ones take what the new sheet suggests.
				fields.alias = r.alias ?? fields.alias ?? '';
				fields.role = r.role ?? fields.role ?? '';
			}
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

	let writing = $state(false);
	/** Fill the Image prompt from what the reference pictures show; saving makes it your own prompt. */
	async function writeFromPictures() {
		if (!asset) return;
		writing = true;
		try {
			// The pictures as they are in the dialog right now (incl. a new choice of references).
			await assetLibraryApi.update(asset.id, {
				source_images: (await picker?.resolvePaths()) ?? [],
				data: { ref_indices: refIdx, max_refs: fields.max_refs },
			});
			const r = await assetLibraryApi.promptFromPictures(asset.id);
			fields.consistency_prompt = r.prompt;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			writing = false;
		}
	}

	let tagging = $state(false);
	let outfitTags = $state<string[]>([]);
	/** Danbooru tags of the character's identity from its pictures (WD tagger), for SDXL styles. */
	async function tagsFromPictures() {
		if (!asset) return;
		tagging = true;
		try {
			await assetLibraryApi.update(asset.id, {
				source_images: (await picker?.resolvePaths()) ?? [],
				data: { ref_indices: refIdx, max_refs: fields.max_refs },
			});
			const r = await assetLibraryApi.tagsFromPictures(asset.id);
			fields.sd_tags = r.tags.join(', ');
			outfitTags = r.outfit;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			tagging = false;
		}
	}

	async function save() {
		if (!asset || !name.trim()) return;
		// An empty look is written automatically before saving.
		if (!(fields[lookKey] ?? '').trim()) await generate();
		busy = true;
		try {
			const keys = [...textFields.map((f) => f.key), 'consistency_prompt', 'max_refs', ...(isChar ? ['sd_tags'] : [])];
			const saved = await assetLibraryApi.update(asset.id, {
				name: name.trim(),
				...(isChar ? {} : { tags: tags.split(',').map((s) => s.trim()).filter(Boolean) }),
				...(asset.kind === 'item' ? { category: category || null } : {}),
				data: { ...Object.fromEntries(keys.map((k) => [k, fields[k] ?? ''])), ref_indices: refIdx },
				source_images: (await picker?.resolvePaths()) ?? [],
			});
			// Take back what the server made of it (identity tags, prompt, stored picture paths).
			tags = saved.tags.join(', ');
			fields = { ...fields, consistency_prompt: String(saved.data.consistency_prompt ?? fields.consistency_prompt) };
			pics = ((saved.data.source_images as string[] | undefined) ?? []).map((path) => ({ src: assetUrl(path) ?? '', path }));
			refIdx = ((saved.data.ref_indices as number[] | undefined) ?? []).filter((i) => i < pics.length);
			savedState = snapshot();
			void loadPreview();
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
				{#if !isChar}
					<!-- A character's tags are read from its Character information; it has no tag field. -->
					<label>
						<span class="field-label">{t('assets.edit.tags')}</span>
						<input class="field-input" bind:value={tags} placeholder="alias:Ripper Eddy, faction:Union Guard" />
					</label>
				{/if}
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
					<span class="field-label">
						{t('assets.edit.prompt')}
						{#if pics.length}
							<button type="button" class="gen" disabled={writing} onclick={writeFromPictures}>
								{writing ? t('assets.edit.writing') : t('assets.edit.writeFromPictures')}
							</button>
						{/if}
					</span>
					<textarea class="field-textarea mono" rows="8" bind:value={fields.consistency_prompt}></textarea>
					<span class="muted small">
						{preview?.auto ? t('assets.edit.promptAuto') : t('assets.edit.promptOwn')}
						{#if !preview?.auto}
							<button type="button" class="gen" onclick={() => (fields.consistency_prompt = '')}>
								{t('assets.edit.promptReset')}
							</button>
						{/if}
					</span>
				</label>
				{#if isChar}
					<label>
						<span class="field-label">
							{t('assets.edit.sdTags')}
							{#if pics.length}
								<button type="button" class="gen" disabled={tagging} onclick={tagsFromPictures}>
									{tagging ? t('assets.edit.tagging') : t('assets.edit.tagsFromPictures')}
								</button>
							{/if}
						</span>
						<textarea class="field-textarea mono" rows="2" bind:value={fields.sd_tags}></textarea>
						<span class="muted small">
							{t('assets.edit.sdTagsHint')}
							{#if outfitTags.length}<br />{t('assets.edit.outfitTags', { tags: outfitTags.join(', ') })}{/if}
						</span>
					</label>
				{/if}
				{#if preview}
					<div class="refs">
						<span class="field-label">{t('assets.edit.sentRefs', { n: preview.refs.length })}</span>
						<div class="ref-row">
							{#each preview.refs as r (r)}<img src={assetUrl(r)} alt="" />{/each}
							{#if !preview.refs.length}<span class="muted small">{t('assets.edit.noRefs')}</span>{/if}
						</div>
					</div>
				{/if}
				<div>
					<span class="field-label">{t('assets.new.pictures', { max: 6 })}</span>
					<PicturePicker bind:this={picker} bind:pics bind:selected={refIdx} selectable />
					{#if refIdx.length}
						<p class="muted small">
							{t('assets.pick.chosen', { n: refIdx.length })}
							<button type="button" class="gen" onclick={() => (refIdx = [])}>{t('assets.pick.auto')}</button>
						</p>
					{/if}
					<label class="refs-max" class:hidden={refIdx.length > 0}>
						<span class="field-label">{t('assets.edit.maxRefs')}</span>
						<select class="field-select" bind:value={fields.max_refs}>
							{#each [1, 2, 3, 4, 5, 6] as n (n)}<option value={String(n)}>{n}</option>{/each}
						</select>
						<span class="muted small">{t('assets.edit.maxRefsHint')}</span>
					</label>
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
	.hidden {
		display: none;
	}
	.refs-max {
		margin-top: 8px;
		max-width: 360px;
	}
	.refs {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.ref-row {
		display: flex;
		gap: 6px;
	}
	.ref-row img {
		width: 64px;
		height: 64px;
		object-fit: cover;
		border-radius: 6px;
		border: 1px solid var(--border);
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
