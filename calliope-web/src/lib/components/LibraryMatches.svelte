<script lang="ts">
	/**
	 * Library characters this project's characters may be (by name/alias, role,
	 * gender, age). "Use identity" gives the project character the library
	 * identity -- its outfit stays -- and the library pictures as references.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { assetLibraryApi, assetUrl } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import Card from '$lib/components/ui/Card.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';

	interface Props {
		projectId: number;
	}
	let { projectId }: Props = $props();

	const client = useQueryClient();
	const matchesQuery = createQuery(
		toStore(() => ({ queryKey: ['library-matches', projectId], queryFn: () => assetLibraryApi.matches(projectId) })),
	);
	const rows = $derived($matchesQuery.data ?? []);
	let busy = $state<string | null>(null);
	// A hand-edited image prompt was kept; ask before replacing it.
	let keptFor = $state<{ assetId: number; characterId: number; name: string; update: boolean } | null>(null);
	let askOpen = $state(false);

	async function refresh() {
		await client.invalidateQueries({ queryKey: ['assets'] });
		await client.invalidateQueries({ queryKey: ['library-matches', projectId] });
		await client.invalidateQueries({ queryKey: ['asset-library'] });
	}

	async function use(assetId: number, characterId: number, name: string, update = false, replacePrompt = false) {
		busy = `${assetId}:${characterId}`;
		try {
			const r = update
				? await assetLibraryApi.updateFromLibrary(projectId, characterId, replacePrompt)
				: await assetLibraryApi.applyIdentity(assetId, projectId, characterId, replacePrompt);
			toast.success(t(update ? 'assets.match.updated' : 'assets.match.applied', { name }));
			if (r.prompt_kept) {
				keptFor = { assetId, characterId, name, update };
				askOpen = true;
			}
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = null;
		}
	}
</script>

<ConfirmDialog
	bind:open={askOpen}
	title={t('assets.match.promptTitle')}
	message={t('assets.match.promptBody', { name: keptFor?.name ?? '' })}
	confirmLabel={t('assets.match.promptReplace')}
	onconfirm={() => {
		const k = keptFor;
		keptFor = null;
		if (k) void use(k.assetId, k.characterId, k.name, k.update, true);
	}}
/>

{#if rows.length}
	<Card>
		<div class="head">
			<strong>{t('assets.match.title')}</strong>
			<span class="muted small">{t('assets.match.lead')}</span>
		</div>
		<div class="rows">
			{#each rows as row (row.character_id)}
				{#each row.matches as m (m.asset_id)}
					<div class="row">
						{#if m.image_path}<img src={assetUrl(m.image_path)} alt="" />{:else}<div class="noimg"></div>{/if}
						<div class="text">
							<span><strong>{row.character_name}</strong> ↔ {m.name}</span>
							<span class="muted small">{m.why.join(' · ')}</span>
						</div>
						{#if m.linked && m.outdated}
							<Button size="sm" variant="primary" loading={busy === `${m.asset_id}:${row.character_id}`}
								title={t('assets.match.outdatedHint')}
								onclick={() => use(m.asset_id, row.character_id, row.character_name, true)}>
								{t('assets.match.update')}
							</Button>
						{:else if m.linked}
							<span class="linked small">{t('assets.match.linked')}</span>
						{:else}
							<Button size="sm" variant="secondary" loading={busy === `${m.asset_id}:${row.character_id}`}
								onclick={() => use(m.asset_id, row.character_id, row.character_name)}>
								{t('assets.match.use')}
							</Button>
						{/if}
					</div>
				{/each}
			{/each}
		</div>
	</Card>
{/if}

<style>
	.head {
		display: flex;
		flex-direction: column;
		gap: 2px;
		margin-bottom: 10px;
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.row img,
	.noimg {
		width: 44px;
		height: 44px;
		border-radius: 6px;
		object-fit: cover;
		background: var(--surface-2, #222);
		flex: none;
	}
	.text {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-width: 0;
	}
	.linked {
		color: var(--accent);
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
