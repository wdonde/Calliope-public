<script lang="ts">
	/**
	 * Create a character/location/item from notes and/or the user's own pictures.
	 * The backend describes the look (vision model when there are pictures) and
	 * renders the sheet with the pictures as references (agent/asset_create.py).
	 */
	import { assetLibraryApi, playgroundApi, type AssetKind } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	interface Props {
		open: boolean;
		projectId: number;
		kind: AssetKind;
		oncreated: () => void;
	}
	let { open = $bindable(), projectId, kind, oncreated }: Props = $props();

	const MAX = 6;
	let name = $state('');
	let notes = $state('');
	let files = $state<File[]>([]);
	let generate = $state(true);
	let busy = $state(false);
	const previews = $derived(files.map((f) => URL.createObjectURL(f)));

	function addFiles(list: FileList | null) {
		const images = Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));
		files = [...files, ...images].slice(0, MAX);
	}

	async function submit() {
		if (!name.trim()) return;
		busy = true;
		try {
			const paths: string[] = [];
			for (const f of files) paths.push((await playgroundApi.upload(f)).path);
			await assetLibraryApi.create(projectId, { kind, name: name.trim(), prompt: notes, image_paths: paths, generate });
			toast.success(t('assets.newCreated', { name: name.trim() }));
			name = '';
			notes = '';
			files = [];
			open = false;
			oncreated();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title={t(`assets.new.${kind}`)} size="lg" onclose={() => (open = false)}>
	<div class="form">
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
				{#each previews as src, i (src)}
					<figure>
						<img {src} alt="" />
						<button type="button" aria-label={t('common.delete')} onclick={() => (files = files.filter((_, k) => k !== i))}>×</button>
					</figure>
				{/each}
				{#if files.length < MAX}
					<label class="add">
						+
						<input type="file" accept="image/*" multiple hidden onchange={(e) => addFiles(e.currentTarget.files)} />
					</label>
				{/if}
			</div>
			<p class="muted small">{files.length ? t('assets.new.picturesHint') : t('assets.new.noPicturesHint')}</p>
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
	.muted {
		color: var(--text-muted);
		margin: 4px 0 0;
	}
	.small {
		font-size: 12px;
	}
</style>
