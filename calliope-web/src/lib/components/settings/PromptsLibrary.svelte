<script lang="ts">
	/**
	 * Settings > Prompts: every prompt Calliope sends to a model -- name, area,
	 * what it does, where and when it is used, the model role, its code place,
	 * a picture and a link to that place. Fixed texts can be edited and reset;
	 * prompts the code assembles from story data are shown with what they are
	 * built from.
	 */
	import { createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { page } from '$app/state';
	import { tick } from 'svelte';
	import { assetUrl, projects as projectsApi, promptsApi, type PromptEntry } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { toast } from '$lib/toast';
	import Button from '$lib/components/ui/Button.svelte';

	const client = useQueryClient();
	const promptsQuery = createQuery({ queryKey: ['prompts'], queryFn: promptsApi.list });
	const projectsQuery = createQuery({ queryKey: ['projects'], queryFn: projectsApi.list });
	const entries = $derived($promptsQuery.data ?? []);
	// The process a project goes through, then what runs across it.
	const PHASES = [
		{ area: 'Story', step: 1 },
		{ area: 'Script', step: 2 },
		{ area: 'Assets', step: 3 },
		{ area: 'Panels', step: 4 },
		{ area: 'Video', step: 5 },
		{ area: 'Agent', step: 0 },
		{ area: 'Workflows', step: 0 },
		{ area: 'Common', step: 0 },
	];
	const KIND_ORDER = ['system', 'instruction', 'image', 'note'];

	let area = $state('');
	let query = $state('');
	let onlyEdited = $state(false);
	const shown = $derived(
		entries.filter(
			(e) =>
				(!area || e.area === area) &&
				(!onlyEdited || e.overridden) &&
				(!query.trim() ||
					[e.name, e.description, e.where, e.when, e.key, e.text]
						.join(' ')
						.toLowerCase()
						.includes(query.trim().toLowerCase())),
		),
	);

	// Links like /project/{project}?stage=story open in this project.
	let projectId = $state<number | ''>('');
	$effect(() => {
		try {
			const v = localStorage.getItem('calliope.prompts.project');
			if (v) projectId = Number(v);
		} catch {
			// no storage: pick it each time
		}
	});
	$effect(() => {
		if (projectId === '' && $projectsQuery.data?.length) projectId = $projectsQuery.data[0].id;
	});
	function rememberProject(v: string) {
		projectId = v ? Number(v) : '';
		try {
			localStorage.setItem('calliope.prompts.project', v);
		} catch {
			// fine
		}
	}
	const linkOf = (e: PromptEntry) =>
		e.link.includes('{project}') ? (projectId === '' ? '' : e.link.replace('{project}', String(projectId))) : e.link;

	const phases = $derived(
		PHASES.map((ph) => {
			const list = shown.filter((e) => e.area === ph.area);
			const groups = KIND_ORDER.map((k) => ({ kind: k, items: list.filter((e) => e.kind === k) })).filter((g) => g.items.length);
			return { ...ph, total: entries.filter((e) => e.area === ph.area).length, count: list.length, groups };
		}),
	);

	let openKey = $state<string | null>(null);
	let draft = $state('');
	let saving = $state(false);

	// ?key=story.system (from the process map) opens that prompt.
	let opened = '';
	$effect(() => {
		const key = page.url.searchParams.get('key');
		const e = entries.find((x) => x.key === key);
		if (!e || opened === e.key) return;
		opened = e.key;
		openKey = e.key;
		draft = e.text;
		void tick().then(() => document.getElementById(`prompt-${e.key}`)?.scrollIntoView({ block: 'center' }));
	});

	function toggle(e: PromptEntry) {
		openKey = openKey === e.key ? null : e.key;
		draft = e.text;
	}

	async function refresh() {
		await client.invalidateQueries({ queryKey: ['prompts'] });
	}

	async function save(e: PromptEntry) {
		saving = true;
		try {
			await promptsApi.update(e.key, { text: draft });
			await refresh();
			toast.success(t('prompts.saved', { name: e.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			saving = false;
		}
	}

	async function reset(e: PromptEntry) {
		try {
			const r = await promptsApi.update(e.key, { reset: true });
			draft = r.text;
			await refresh();
			toast.success(t('prompts.resetDone', { name: e.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function upload(e: PromptEntry, files: FileList | null) {
		const file = files?.[0];
		if (!file) return;
		try {
			await promptsApi.uploadImage(e.key, file);
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function removeImage(e: PromptEntry) {
		try {
			await promptsApi.update(e.key, { image_path: '' });
			await refresh();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}
</script>

<section class="panel">
	<h1>{t('prompts.title')}</h1>
	<p class="lead">{t('prompts.lead', { n: entries.length, e: entries.filter((e) => e.editable).length })}</p>

	<div class="filters">
		<input class="field-input search" placeholder={t('prompts.search')} bind:value={query} />
		<label class="check"><input type="checkbox" bind:checked={onlyEdited} /> {t('prompts.onlyEdited')}</label>
		<label class="proj">
			<span class="muted small">{t('prompts.linkProject')}</span>
			<select class="field-select" value={projectId} onchange={(e) => rememberProject(e.currentTarget.value)}>
				{#each $projectsQuery.data ?? [] as p (p.id)}<option value={p.id}>{p.title}</option>{/each}
			</select>
		</label>
	</div>

	<nav class="flow" aria-label={t('prompts.flow')}>
		<button type="button" class="step all" class:on={!area} onclick={() => (area = '')}>{t('prompts.all')}</button>
		{#each phases.filter((ph) => ph.step) as ph, i (ph.area)}
			{#if i}<span class="arrow">→</span>{/if}
			<button type="button" class="step" class:on={area === ph.area} onclick={() => (area = area === ph.area ? '' : ph.area)}>
				<span class="num">{ph.step}</span>{t(`prompts.phase.${ph.area}`)} <span class="cnt">{ph.total}</span>
			</button>
		{/each}
		<span class="sep"></span>
		{#each phases.filter((ph) => !ph.step) as ph (ph.area)}
			<button type="button" class="step side" class:on={area === ph.area} onclick={() => (area = area === ph.area ? '' : ph.area)}>
				{t(`prompts.phase.${ph.area}`)} <span class="cnt">{ph.total}</span>
			</button>
		{/each}
	</nav>

	{#each phases.filter((ph) => ph.count) as ph (ph.area)}
		<section class="phase">
			<h2>
				{#if ph.step}<span class="num">{ph.step}</span>{/if}
				{t(`prompts.phase.${ph.area}`)}
				<span class="muted small">{t(`prompts.phaseHint.${ph.area}`)}</span>
			</h2>
			{#each ph.groups as g (g.kind)}
				<h3 class="group">{t(`prompts.kind.${g.kind}`)} <span class="cnt">{g.items.length}</span></h3>
				<div class="list">
					{#each g.items as e (e.key)}
						{@render entryCard(e)}
					{/each}
				</div>
			{/each}
		</section>
	{:else}
		<p class="muted">{$promptsQuery.isLoading ? '…' : t('prompts.none')}</p>
	{/each}
</section>

{#snippet entryCard(e: PromptEntry)}
			<article class="entry" id={`prompt-${e.key}`} class:open={openKey === e.key}>
				<button type="button" class="row" onclick={() => toggle(e)}>
					{#if e.image_path}
						<img class="thumb" src={assetUrl(e.image_path)} alt="" />
					{:else}
						<div class="thumb empty">{e.area.slice(0, 2)}</div>
					{/if}
					<div class="text">
						<span class="title">
							<strong>{e.name}</strong>
							<span class="badge">{e.area}</span>
							{#if !e.editable}<span class="badge muted-b">{t('prompts.assembled')}</span>{/if}
							{#if e.overridden}<span class="badge edited">{t('prompts.edited')}</span>{/if}
						</span>
						<span class="muted small">{e.description}</span>
						<span class="small meta">
							<span><b>{t('prompts.where')}</b> {e.where}</span>
							<span><b>{t('prompts.when')}</b> {e.when}</span>
						</span>
					</div>
				</button>
				{#if openKey === e.key}
					<div class="detail">
						<div class="facts small">
							<span><b>{t('prompts.role')}</b> {e.role}</span>
							<span><b>{t('prompts.code')}</b> <code>{e.source}</code></span>
							{#if e.placeholders.length}
								<span><b>{t('prompts.placeholders')}</b> {e.placeholders.map((p) => `{${p}}`).join(', ')}</span>
							{/if}
							{#if linkOf(e)}<a href={linkOf(e)}>{t('prompts.open')} →</a>{/if}
						</div>
						{#if e.editable}
							<textarea class="field-textarea mono" rows={Math.min(22, Math.max(4, draft.split('\n').length + 1))}
								bind:value={draft}></textarea>
							<div class="actions">
								{#if e.overridden}
									<Button size="sm" variant="ghost" onclick={() => reset(e)}>{t('prompts.reset')}</Button>
								{/if}
								<span class="grow"></span>
								<Button size="sm" variant="primary" loading={saving} disabled={draft === e.text}
									onclick={() => save(e)}>{t('common.save')}</Button>
							</div>
						{:else}
							<p class="small"><b>{t('prompts.builtFrom')}</b> {e.built_from}</p>
							{#if e.default}<pre class="mono small">{e.default}</pre>{/if}
						{/if}
						<div class="pic small">
							<span class="muted">{t('prompts.picture')}</span>
							<label class="upload">
								{e.image_path ? t('prompts.replacePicture') : t('prompts.addPicture')}
								<input type="file" accept="image/png,image/jpeg,image/webp" hidden
									onchange={(ev) => upload(e, ev.currentTarget.files)} />
							</label>
							{#if e.image_path}
								<button type="button" class="link" onclick={() => removeImage(e)}>{t('prompts.removePicture')}</button>
							{/if}
						</div>
						{#if e.image_path}<img class="big" src={assetUrl(e.image_path)} alt={e.where} />{/if}
					</div>
				{/if}
			</article>
{/snippet}

<style>
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		align-items: center;
		margin-bottom: 14px;
	}
	.search {
		max-width: 260px;
	}
	.flow {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		margin: 0 0 18px;
		padding: 10px;
		border: 1px solid var(--border);
		border-radius: 10px;
	}
	.step {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 5px 12px;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: none;
		color: var(--text-secondary);
		cursor: pointer;
		font-size: 13px;
	}
	.step.side {
		border-style: dashed;
	}
	.step.on {
		background: var(--accent);
		border-color: var(--accent);
		color: #fff;
	}
	.num {
		display: inline-grid;
		place-items: center;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		background: var(--accent);
		color: #fff;
		font-size: 11px;
		font-weight: 700;
	}
	.step.on .num {
		background: #fff;
		color: var(--accent);
	}
	.cnt {
		font-size: 11px;
		opacity: 0.7;
	}
	.arrow {
		color: var(--text-muted);
	}
	.sep {
		width: 1px;
		height: 22px;
		background: var(--border);
		margin: 0 6px;
	}
	.phase {
		margin-bottom: 22px;
	}
	.phase h2 {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 17px;
		margin: 0 0 6px;
		flex-wrap: wrap;
	}
	.group {
		margin: 10px 0 6px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-secondary);
	}
	.check,
	.proj {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
	}
	.proj select {
		width: auto;
		max-width: 220px;
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.entry {
		border: 1px solid var(--border);
		border-radius: 10px;
	}
	.entry.open {
		border-color: var(--accent);
	}
	.row {
		display: flex;
		gap: 12px;
		width: 100%;
		padding: 10px 12px;
		border: none;
		background: none;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}
	.thumb {
		width: 72px;
		height: 48px;
		flex: none;
		border-radius: 6px;
		object-fit: cover;
		border: 1px solid var(--border);
	}
	.thumb.empty {
		display: grid;
		place-items: center;
		color: var(--text-muted);
		font-weight: 700;
		font-size: 13px;
		text-transform: uppercase;
	}
	.text {
		display: flex;
		flex-direction: column;
		gap: 3px;
		min-width: 0;
	}
	.title {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
	}
	.badge {
		font-size: 10px;
		padding: 1px 7px;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--text-secondary);
	}
	.badge.edited {
		border-color: var(--accent);
		color: var(--accent);
	}
	.badge.muted-b {
		color: var(--text-muted);
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		color: var(--text-secondary);
	}
	.detail {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 0 12px 12px 96px;
	}
	@media (max-width: 720px) {
		.detail {
			padding-left: 12px;
		}
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 16px;
		align-items: baseline;
	}
	.facts a,
	.link,
	.upload {
		color: var(--accent);
		cursor: pointer;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		font-size: inherit;
	}
	.actions {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.grow {
		flex: 1;
	}
	pre {
		margin: 0;
		padding: 10px;
		border: 1px solid var(--border);
		border-radius: 8px;
		white-space: pre-wrap;
	}
	.pic {
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.big {
		max-width: 100%;
		max-height: 260px;
		object-fit: contain;
		align-self: flex-start;
		border-radius: 8px;
		border: 1px solid var(--border);
	}
	.muted {
		color: var(--text-muted);
	}
	.small {
		font-size: 12px;
	}
</style>
