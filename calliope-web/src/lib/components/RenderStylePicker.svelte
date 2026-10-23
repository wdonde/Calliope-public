<script lang="ts">
	/**
	 * The project's render style (Settings > Styles), grouped by category.
	 * Panels and the project's asset renders use its style text, negative
	 * prompt and workflows; "Default" keeps the format's built-in look.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { toStore } from 'svelte/store';
	import { projects as projectsApi, renderStylesApi } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';

	interface Props {
		projectId: number;
	}
	let { projectId }: Props = $props();

	const client = useQueryClient();
	const stylesQuery = createQuery({ queryKey: ['render-styles'], queryFn: renderStylesApi.list });
	const projectQuery = createQuery(
		toStore(() => ({ queryKey: ['project', projectId], queryFn: () => projectsApi.get(projectId) })),
	);
	const styles = $derived($stylesQuery.data ?? []);
	const categories = $derived([...new Set(styles.map((s) => s.category))]);
	const current = $derived($projectQuery.data?.render_style_id ?? null);

	async function pick(value: string) {
		try {
			await projectsApi.update(projectId, { render_style_id: value ? Number(value) : null });
			await client.invalidateQueries({ queryKey: ['project', projectId] });
			const s = styles.find((x) => x.id === Number(value));
			toast.success(s ? t('styles.picked', { name: `${s.category} · ${s.name}` }) : t('styles.pickedDefault'));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}
</script>

<label class="picker">
	<span class="field-label">{t('styles.project')}</span>
	<select class="field-select" value={current ?? ''} onchange={(e) => pick(e.currentTarget.value)}>
		<option value="">{t('styles.default')}</option>
		{#each categories as cat (cat)}
			<optgroup label={cat}>
				{#each styles.filter((s) => s.category === cat) as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
			</optgroup>
		{/each}
	</select>
	<a class="small" href="/settings?tab=styles">{t('styles.manage')}</a>
</label>

<style>
	.picker {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.picker select {
		width: auto;
		min-width: 200px;
	}
	.small {
		font-size: 12px;
		color: var(--accent);
	}
</style>
