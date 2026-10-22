<script lang="ts">
	/** "Add from library": pick library assets of one kind and copy them into the project. */
	import { createQuery } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetLibraryApi, assetUrl, type AssetKind, type LibraryAsset } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	interface Props {
		open: boolean;
		projectId: number;
		kind: AssetKind;
		onadded: () => void;
	}
	let { open = $bindable(), projectId, kind, onadded }: Props = $props();

	let q = $state('');
	let adding = $state<number | null>(null);
	const libraryQuery = createQuery(
		toStore(() => ({
			queryKey: ['asset-library', kind, q],
			queryFn: () => assetLibraryApi.list({ kind, q: q.trim() || undefined }),
			enabled: open,
		})),
	);
	const assets = $derived(($libraryQuery.data ?? []) as LibraryAsset[]);

	async function add(a: LibraryAsset) {
		adding = a.id;
		try {
			await assetLibraryApi.addToProject(a.id, projectId);
			toast.success(t('assets.libraryAdded', { name: a.name }));
			onadded();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			adding = null;
		}
	}
</script>

<Modal bind:open title={t('assets.fromLibrary')} size="lg" onclose={() => (open = false)}>
	<input class="field-input search" placeholder={t('assets.searchPlaceholder')} bind:value={q} />
	{#if assets.length === 0}
		<p class="muted">{$libraryQuery.isLoading ? '…' : t('assets.libraryEmpty')}</p>
	{:else}
		<div class="list">
			{#each assets as a (a.id)}
				<div class="entry">
					{#if a.image_path}
						<img src={assetUrl(a.image_path)} alt="" />
					{:else}
						<div class="noimg"></div>
					{/if}
					<div class="text">
						<strong>{a.name}</strong>
						<span class="muted small">
							{[a.category, ...a.tags].filter(Boolean).join(' · ')}
						</span>
					</div>
					<Button size="sm" variant="secondary" loading={adding === a.id} onclick={() => add(a)}>
						{t('assets.addToProject')}
					</Button>
				</div>
			{/each}
		</div>
	{/if}
</Modal>

<style>
	.search {
		width: 100%;
		margin-bottom: 12px;
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-height: 60vh;
		overflow-y: auto;
	}
	.entry {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.entry img,
	.noimg {
		width: 64px;
		height: 64px;
		object-fit: cover;
		border-radius: 6px;
		background: var(--surface-2, #222);
		flex: none;
	}
	.text {
		display: flex;
		flex-direction: column;
		gap: 2px;
		flex: 1;
		min-width: 0;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
