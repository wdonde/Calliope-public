<script lang="ts">
	/**
	 * Settings > Styles: looks a project can pick, grouped by category
	 * (Anime, Manga, 3D, ...). A style has a style text that leads every
	 * image prompt, a negative prompt, and optionally a workflow per number of
	 * references; "Workflow roles" fills what it leaves open.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { renderStylesApi, workflows as workflowsApi, type RenderStyle } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';

	const client = useQueryClient();
	const stylesQuery = createQuery({ queryKey: ['render-styles'], queryFn: renderStylesApi.list });
	const wfQuery = createQuery({ queryKey: ['workflows'], queryFn: workflowsApi.list });
	const styles = $derived($stylesQuery.data ?? []);
	const categories = $derived([...new Set(styles.map((s) => s.category))]);
	/** Enabled image workflows by number of image inputs. */
	const byRefs = $derived.by(() => {
		const out: Record<string, { id: number; name: string }[]> = { '0': [], '1': [], '2': [] };
		for (const w of $wfQuery.data ?? []) {
			if (w.kind !== 'image' || !w.is_enabled) continue;
			const n = String(w.input_schema.filter((i) => i.kind === 'image').length);
			out[n]?.push({ id: w.id, name: w.name });
		}
		return out;
	});

	type Draft = Omit<RenderStyle, 'id'> & { id: number | null };
	let draft = $state<Draft | null>(null);
	let saving = $state(false);
	let deleteOpen = $state(false);

	function edit(s: RenderStyle | null) {
		draft = s
			? { ...s, workflows: { ...s.workflows } }
			: { id: null, category: categories[0] ?? 'Anime', name: '', style_prompt: '', negative_prompt: '', workflows: {} };
	}

	async function save() {
		if (!draft) return;
		saving = true;
		try {
			const { id, ...body } = draft;
			if (id === null) await renderStylesApi.create(body);
			else await renderStylesApi.update(id, body);
			await client.invalidateQueries({ queryKey: ['render-styles'] });
			toast.success(t('styles.saved'));
			draft = null;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (draft?.id == null) return;
		try {
			await renderStylesApi.delete(draft.id);
			await client.invalidateQueries({ queryKey: ['render-styles'] });
			draft = null;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	const wfName = (id: number | undefined) =>
		id ? (($wfQuery.data ?? []).find((w) => w.id === id)?.name ?? `#${id}`) : t('styles.byRole');
</script>

<ConfirmDialog
	bind:open={deleteOpen}
	title={t('styles.deleteTitle')}
	message={t('styles.deleteBody', { name: draft?.name ?? '' })}
	confirmLabel={t('common.delete')}
	onconfirm={remove}
/>

<section class="panel">
	<div class="head">
		<h1>{t('styles.title')}</h1>
		<Button size="sm" variant="primary" onclick={() => edit(null)}>{t('styles.new')}</Button>
	</div>
	<p class="lead">{t('styles.lead')}</p>

	{#if draft}
		<div class="editor">
			<div class="row">
				<label>
					<span class="field-label">{t('styles.category')}</span>
					<input class="field-input" list="style-categories" bind:value={draft.category} />
					<datalist id="style-categories">
						{#each [...new Set(['Anime', 'Manga', 'Webtoon', '3D', 'Realistic', ...categories])] as c (c)}<option value={c}></option>{/each}
					</datalist>
				</label>
				<label>
					<span class="field-label">{t('styles.name')}</span>
					<input class="field-input" bind:value={draft.name} />
				</label>
			</div>
			<label>
				<span class="field-label">{t('styles.stylePrompt')}</span>
				<textarea class="field-textarea" rows="2" bind:value={draft.style_prompt}></textarea>
				<span class="field-hint">{t('styles.stylePromptHint')}</span>
			</label>
			<label>
				<span class="field-label">{t('styles.negative')}</span>
				<textarea class="field-textarea" rows="2" bind:value={draft.negative_prompt}></textarea>
				<span class="field-hint">{t('styles.negativeHint')}</span>
			</label>
			<div class="row three">
				{#each ['0', '1', '2'] as n (n)}
					<label>
						<span class="field-label">{t(`styles.wf${n}`)}</span>
						<select
							class="field-select"
							value={draft.workflows[n] ?? ''}
							onchange={(e) => {
								if (!draft) return;
								const v = e.currentTarget.value;
								const next = { ...draft.workflows };
								if (v) next[n] = Number(v);
								else delete next[n];
								draft.workflows = next;
							}}
						>
							<option value="">{t('styles.byRole')}</option>
							{#each byRefs[n] as w (w.id)}<option value={w.id}>{w.name}</option>{/each}
						</select>
					</label>
				{/each}
			</div>
			<div class="actions">
				{#if draft.id !== null}
					<Button size="sm" variant="ghost" onclick={() => (deleteOpen = true)}>{t('common.delete')}</Button>
				{/if}
				<span class="grow"></span>
				<Button size="sm" variant="secondary" onclick={() => (draft = null)}>{t('common.cancel')}</Button>
				<Button size="sm" variant="primary" loading={saving}
					disabled={!draft.category.trim() || !draft.name.trim()} onclick={save}>{t('common.save')}</Button>
			</div>
		</div>
	{/if}

	{#each categories as cat (cat)}
		<h3 class="cat">{cat}</h3>
		<div class="list">
			{#each styles.filter((s) => s.category === cat) as s (s.id)}
				<button type="button" class="style" class:on={draft?.id === s.id} onclick={() => edit(s)}>
					<strong>{s.name}</strong>
					<span class="muted small">{s.style_prompt || '—'}</span>
					<span class="muted small wfs">
						{#each ['0', '1', '2'] as n (n)}<span>{n}: {wfName(s.workflows[n])}</span>{/each}
					</span>
				</button>
			{/each}
		</div>
	{:else}
		<p class="muted">{t('styles.empty')}</p>
	{/each}
</section>

<style>
	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.editor {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px;
		margin-bottom: 16px;
		border: 1px solid var(--accent);
		border-radius: 10px;
	}
	.editor label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
	.row.three {
		grid-template-columns: repeat(3, 1fr);
	}
	@media (max-width: 720px) {
		.row,
		.row.three {
			grid-template-columns: 1fr;
		}
	}
	.actions {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.grow {
		flex: 1;
	}
	.cat {
		margin: 16px 0 6px;
		font-size: 13px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-secondary);
	}
	.list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 8px;
	}
	.style {
		display: flex;
		flex-direction: column;
		gap: 4px;
		text-align: left;
		padding: 10px 12px;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--surface-2, transparent);
		color: inherit;
		cursor: pointer;
		min-width: 0;
	}
	.style.on {
		border-color: var(--accent);
	}
	.style span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.wfs {
		display: flex;
		flex-direction: column;
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
