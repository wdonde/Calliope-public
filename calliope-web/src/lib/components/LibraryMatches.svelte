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

	async function use(assetId: number, characterId: number, name: string) {
		busy = `${assetId}:${characterId}`;
		try {
			await assetLibraryApi.applyIdentity(assetId, projectId, characterId);
			toast.success(t('assets.match.applied', { name }));
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['library-matches', projectId] });
			await client.invalidateQueries({ queryKey: ['asset-library'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = null;
		}
	}
</script>

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
						{#if m.linked}
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
