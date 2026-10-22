<script lang="ts">
	/**
	 * Which workflow does which job -- the image counterpart of Agent models.
	 * Each role lists only workflows with the right number of image inputs;
	 * Auto keeps the built-in choice (FLUX.2 Klein first). Saves on change.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { settings as settingsApi } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';

	const client = useQueryClient();
	const rolesQuery = createQuery({ queryKey: ['workflow-roles'], queryFn: settingsApi.workflowRoles });
	const roles = $derived($rolesQuery.data ?? []);

	async function assign(key: string, value: string) {
		const map: Record<string, number | null> = Object.fromEntries(roles.map((r) => [r.key, r.assigned]));
		map[key] = value === '' ? null : Number(value);
		try {
			await settingsApi.update({ workflow_assignments: map });
			await client.invalidateQueries({ queryKey: ['workflow-roles'] });
			await client.invalidateQueries({ queryKey: ['settings'] });
			toast.success(t('settings.wfRoles.saved'));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	const nameOf = (role: (typeof roles)[number], id: number | null) =>
		role.candidates.find((c) => c.id === id)?.name ?? t('settings.wfRoles.none');
</script>

<section class="panel">
	<h1>{t('settings.wfRoles.title')}</h1>
	<p class="lead">{t('settings.wfRoles.lead')}</p>
	<div class="roles">
		{#each roles as role (role.key)}
			<div class="role">
				<span class="field-label">
					{role.label}
					<span class="cap">{t('settings.wfRoles.inputs', { n: role.images })}</span>
				</span>
				<select class="field-select" value={role.assigned ?? ''} onchange={(e) => assign(role.key, e.currentTarget.value)}>
					<option value="">{t('settings.wfRoles.auto', { name: nameOf(role, role.resolved) })}</option>
					{#each role.candidates as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
				</select>
				<p class="field-hint">
					{role.hint}
					{#if role.assigned && role.assigned !== role.resolved}
						<span class="warn">{t('settings.wfRoles.unfit')}</span>
					{/if}
					{#if !role.candidates.length}<span class="warn">{t('settings.wfRoles.noneFits', { n: role.images })}</span>{/if}
				</p>
			</div>
		{/each}
	</div>
</section>

<style>
	.roles {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
		gap: 14px 20px;
	}
	.role {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.cap {
		margin-left: 6px;
		font-size: 10px;
		padding: 1px 6px;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--text-secondary);
		font-weight: 500;
	}
	.warn {
		color: var(--warning, #e0a040);
	}
</style>
