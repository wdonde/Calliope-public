<script lang="ts">
	/**
	 * Tags (and, for items, the category) of one asset card. Clicking a tag
	 * filters the Assets stage; "Edit" turns the chips into a comma list.
	 * "To library" copies the asset into the cross-project asset library.
	 */
	import { t } from '$lib/i18n.svelte';

	interface Props {
		tags: string[];
		category?: string | null;
		/** Show the category picker (items only). */
		withCategory?: boolean;
		active?: string | null;
		onfilter: (tag: string) => void;
		onsave: (fields: { tags: string[]; category?: string | null }) => Promise<void> | void;
		/** Omit to hide the "To library" link (e.g. in the library itself). */
		ontolibrary?: () => void;
		/** How many own pictures the asset has; with onfromimages shows "Re-render from pictures". */
		pictures?: number;
		onfromimages?: () => void;
		/** Tags read from the character sheet: shown, not edited here. */
		readonly?: boolean;
	}
	let { tags, category = null, withCategory = false, active = null, onfilter, onsave, ontolibrary, pictures = 0, onfromimages, readonly = false }: Props =
		$props();

	const CATEGORIES = ['weapon', 'vehicle', 'device', 'furniture', 'clothing', 'tool', 'creature', 'prop'];
	let editing = $state(false);
	let draft = $state('');
	let draftCategory = $state('');

	function edit() {
		draft = tags.join(', ');
		draftCategory = category ?? '';
		editing = true;
	}

	async function save() {
		const next = draft
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
		await onsave(withCategory ? { tags: next, category: draftCategory || null } : { tags: next });
		editing = false;
	}
</script>

<div class="asset-tags">
	{#if editing}
		{#if withCategory}
			<input class="field-input" list="item-categories" placeholder={t('assets.category')} bind:value={draftCategory} />
			<datalist id="item-categories">
				{#each CATEGORIES as c (c)}<option value={c}></option>{/each}
			</datalist>
		{/if}
		<input
			class="field-input"
			placeholder="alias:Ripper Eddy, faction:Union Guard"
			bind:value={draft}
			onkeydown={(e) => e.key === 'Enter' && save()}
		/>
		<div class="row">
			<button type="button" class="link" onclick={save}>{t('assets.saveTags')}</button>
			<button type="button" class="link muted" onclick={() => (editing = false)}>{t('common.cancel')}</button>
		</div>
	{:else}
		<div class="chips">
			{#if withCategory && category}
				<button type="button" class="chip category" class:active={active === `category:${category}`}
					onclick={() => onfilter(`category:${category}`)}>{category}</button>
			{/if}
			{#each tags as tag (tag)}
				<button type="button" class="chip" class:active={active === tag} onclick={() => onfilter(tag)}>{tag}</button>
			{/each}
			{#if readonly}
				<span class="from-sheet" title={t('assets.tagsFromSheet')}>{t('assets.tagsFromSheetShort')}</span>
			{:else}
				<button type="button" class="link" onclick={edit}>{tags.length || category ? t('assets.editTags') : t('assets.addTags')}</button>
			{/if}
			{#if ontolibrary}<button type="button" class="link" onclick={ontolibrary}>{t('assets.toLibrary')}</button>{/if}
			{#if pictures && onfromimages}
				<button type="button" class="link" onclick={onfromimages}>{t('assets.fromPictures', { n: pictures })}</button>
			{/if}
		</div>
	{/if}
</div>

<style>
	.asset-tags {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin: 4px 0 2px;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		align-items: center;
	}
	.chip {
		border: 1px solid var(--border);
		background: var(--surface-2, transparent);
		color: var(--text-secondary);
		border-radius: 999px;
		padding: 1px 8px;
		font-size: 11px;
		cursor: pointer;
	}
	.chip.category {
		color: var(--accent);
		border-color: var(--accent);
	}
	.chip.active {
		background: var(--accent);
		border-color: var(--accent);
		color: #fff;
	}
	.from-sheet {
		font-size: 11px;
		color: var(--text-muted);
	}
	.row {
		display: flex;
		gap: 12px;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent);
		cursor: pointer;
		font-size: 11px;
	}
	.link.muted {
		color: var(--text-muted);
	}
	.field-input {
		font-size: 12px;
	}
</style>
