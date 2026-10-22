<script lang="ts">
	/**
	 * Saved assets across projects (characters, locations, items): search by
	 * name/tags/description, filter by kind or tag, edit tags, add a copy to a
	 * project, delete. Assets get here via "To library" in a project's Assets stage.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { assetLibraryApi, assetUrl, projects, type AssetKind, type LibraryAsset } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import AssetTags from '$lib/components/AssetTags.svelte';
	import NewAssetDialog from '$lib/components/NewAssetDialog.svelte';
	import LibraryAssetEditor from '$lib/components/LibraryAssetEditor.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';

	const client = useQueryClient();
	const libraryQuery = createQuery({
		queryKey: ['asset-library'],
		queryFn: () => assetLibraryApi.list(),
		// Poll while a library render is still running.
		refetchInterval: (q: { state: { data?: LibraryAsset[] } }) =>
			(q.state.data ?? []).some((a) => a.data.pending_job) ? 5000 : false,
	});
	let newOpen = $state(false);
	let editing = $state<LibraryAsset | null>(null);
	let editOpen = $state(false);
	const projectsQuery = createQuery({ queryKey: ['projects'], queryFn: projects.list });

	let kind = $state<AssetKind | 'all'>('all');
	let search = $state('');
	let activeTag = $state<string | null>(null);
	let target = $state<Record<number, number | ''>>({});
	let deleting = $state<LibraryAsset | null>(null);
	let confirmOpen = $state(false);

	const KINDS: { id: AssetKind | 'all'; key: string }[] = [
		{ id: 'all', key: 'library.filterAll' },
		{ id: 'character', key: 'assets.tabCharacters' },
		{ id: 'location', key: 'assets.tabEnvironments' },
		{ id: 'item', key: 'assets.tabItems' },
	];

	const shown = $derived.by(() => {
		const words = search.toLowerCase().split(/\s+/).filter(Boolean);
		return ($libraryQuery.data ?? []).filter((a) => {
			if (kind !== 'all' && a.kind !== kind) return false;
			if (activeTag) {
				const ok = activeTag.startsWith('category:')
					? `category:${a.category ?? ''}` === activeTag
					: a.tags.includes(activeTag);
				if (!ok) return false;
			}
			const hay = [a.name, a.category ?? '', ...a.tags, ...Object.values(a.data).filter((v): v is string => typeof v === 'string')]
				.join(' ')
				.toLowerCase();
			return words.every((w) => hay.includes(w));
		});
	});

	const refresh = () => client.invalidateQueries({ queryKey: ['asset-library'] });

	async function act<T>(fn: () => Promise<T>, ok?: string) {
		try {
			await fn();
			if (ok) toast.success(ok);
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	function addTo(a: LibraryAsset) {
		const pid = target[a.id];
		if (!pid) return;
		const title = ($projectsQuery.data ?? []).find((p) => p.id === pid)?.title ?? `#${pid}`;
		void act(() => assetLibraryApi.addToProject(a.id, pid), t('assets.libraryAddedTo', { name: a.name, project: title }));
	}
</script>

<div class="toolbar">
	<div class="kinds">
		{#each KINDS as k (k.id)}
			<button type="button" class="filter-chip" class:active={kind === k.id} onclick={() => (kind = k.id)}>{t(k.key)}</button>
		{/each}
	</div>
	<input class="field-input" type="search" placeholder={t('assets.searchPlaceholder')} bind:value={search} />
	{#if activeTag}
		<button type="button" class="tag-filter" onclick={() => (activeTag = null)}>{activeTag} ×</button>
	{/if}
	<Button size="sm" variant="primary" onclick={() => (newOpen = true)}>{t('assets.new.asset')}</Button>
</div>

<LibraryAssetEditor bind:open={editOpen} asset={editing} onsaved={refresh} />
<NewAssetDialog bind:open={newOpen} kind={kind === 'all' ? 'character' : kind} chooseKind oncreated={refresh} />

{#if ($libraryQuery.data ?? []).length === 0 && !$libraryQuery.isLoading}
	<EmptyState title={t('assets.libraryEmptyTitle')} body={t('assets.libraryEmptyBody')}>
		{#snippet icon()}<Icon name="assets" size={28} />{/snippet}
	</EmptyState>
{:else}
	{#if shown.length === 0}<p class="muted">{t('assets.noMatches')}</p>{/if}
	<div class="grid">
		{#each shown as a (a.id)}
			<article class="card">
				{#if a.image_path}
					<img src={assetUrl(a.image_path)} alt={a.name} loading="lazy" />
				{:else}
					<div class="noimg"><Icon name="image" size={24} /></div>
				{/if}
				<div class="body">
					<div class="title">
						<strong>{a.name}</strong>
						<span class="muted small">{t(KINDS.find((k) => k.id === a.kind)?.key ?? '')}</span>
					</div>
					<AssetTags
						tags={a.tags}
						category={a.category}
						withCategory={a.kind === 'item'}
						active={activeTag}
						onfilter={(tag) => (activeTag = activeTag === tag ? null : tag)}
						onsave={(f) => act(() => assetLibraryApi.update(a.id, f))}
						pictures={(a.data.source_images as string[] | undefined)?.length ?? 0}
						onfromimages={() => act(() => assetLibraryApi.libraryFromImages(a.id), t('assets.fromPicturesQueued'))}
					/>
					{#if a.data.pending_job}<p class="muted small">{t('assets.rendering')}</p>{/if}
					<p class="desc muted small">{String(a.data.appearance ?? a.data.description ?? '')}</p>
					<p class="projects small">
						{#if a.source_project}<span>{t('assets.edit.from', { project: a.source_project.title })}</span>{/if}
						<span>
							{a.used_in?.length
								? t('assets.edit.usedIn', { projects: a.used_in.map((p) => p.title).join(', ') })
								: t('assets.edit.unused')}
						</span>
					</p>
					<div class="row">
						<select class="field-select" bind:value={target[a.id]}>
							<option value="">{t('assets.chooseProject')}</option>
							{#each $projectsQuery.data ?? [] as p (p.id)}
								<option value={p.id}>{p.title}</option>
							{/each}
						</select>
						<Button size="sm" variant="secondary" disabled={!target[a.id]} onclick={() => addTo(a)}>
							{t('assets.addToProject')}
						</Button>
						<Button size="sm" variant="ghost" title={t('assets.edit.button')} onclick={() => {
							editing = a;
							editOpen = true;
						}}>
							<Icon name="edit" size={14} />
						</Button>
						<Button size="sm" variant="ghost" title={t('common.delete')} onclick={() => {
								deleting = a;
								confirmOpen = true;
							}}>
							<Icon name="trash" size={14} />
						</Button>
					</div>
				</div>
			</article>
		{/each}
	</div>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title={t('assets.libraryDeleteTitle')}
	message={t('assets.libraryDeleteBody', { name: deleting?.name ?? '' })}
	confirmLabel={t('common.delete')}
	onconfirm={() => {
		const a = deleting;
		deleting = null;
		if (a) void act(() => assetLibraryApi.remove(a.id));
	}}
/>

<style>
	.toolbar {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
		margin-bottom: 16px;
	}
	.toolbar input {
		flex: 1;
		min-width: 200px;
		max-width: 420px;
	}
	.kinds {
		display: flex;
		gap: 6px;
	}
	.filter-chip {
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-secondary);
		border-radius: 999px;
		padding: 4px 12px;
		cursor: pointer;
		font-size: 13px;
	}
	.filter-chip.active {
		border-color: var(--accent);
		color: var(--accent);
	}
	.tag-filter {
		border: 1px solid var(--accent);
		background: var(--accent);
		color: #fff;
		border-radius: 999px;
		padding: 2px 10px;
		font-size: 12px;
		cursor: pointer;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 16px;
	}
	.card {
		border: 1px solid var(--border);
		border-radius: 10px;
		overflow: hidden;
		background: var(--surface-1, transparent);
		display: flex;
		flex-direction: column;
	}
	.card img,
	.noimg {
		width: 100%;
		aspect-ratio: 3 / 2;
		object-fit: cover;
		background: var(--surface-2, #222);
		display: grid;
		place-items: center;
		color: var(--text-muted);
	}
	.body {
		padding: 10px 12px 12px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.title {
		display: flex;
		justify-content: space-between;
		gap: 8px;
		align-items: baseline;
	}
	.desc {
		margin: 0;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.projects {
		display: flex;
		flex-direction: column;
		gap: 2px;
		margin: 0;
		color: var(--text-secondary);
	}
	.row {
		display: flex;
		gap: 6px;
		align-items: center;
	}
	.row select {
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
