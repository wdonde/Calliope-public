<script lang="ts">
	import { goto } from '$app/navigation';
	import { toStore } from 'svelte/store';
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import {
		assetUrl,
		jobsApi,
		playgroundApi,
		projects,
		workflows,
		type Character,
		type Item,
		type Job,
		type Location,
		type Workflow,
	} from '$lib/api';
	import ComfyDynamicForm from '$lib/components/ComfyDynamicForm.svelte';
	import AssetThumb from '$lib/components/AssetThumb.svelte';
	import AssetTags from '$lib/components/AssetTags.svelte';
	import LibraryPicker from '$lib/components/LibraryPicker.svelte';
	import NewAssetDialog from '$lib/components/NewAssetDialog.svelte';
	import LibraryMatches from '$lib/components/LibraryMatches.svelte';
	import RenderQueue from '$lib/components/RenderQueue.svelte';
	import ImageLightbox from '$lib/components/ImageLightbox.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Card from '$lib/components/ui/Card.svelte';
	import ProgressBar from '$lib/components/ui/ProgressBar.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import {
		compactInputValues,
		withoutPromptInputs,
		workflowHasPromptInput,
	} from '$lib/comfy/promptInput';
	import {
		characterSheetTemplate,
		itemReferenceTemplate,
		locationReferenceTemplate,
	} from '$lib/promptTemplates';
	import { assetLibraryApi, settings as settingsApi, type AssetKind } from '$lib/api';
	import { toast } from '$lib/toast';
	import { t } from '$lib/i18n.svelte';

	interface Props {
		projectId: number;
	}

	let { projectId }: Props = $props();
	const client = useQueryClient();
	let tab = $state<'characters' | 'locations' | 'items'>('characters');

	// ── search, tag filter, library ──
	let search = $state('');
	let activeTag = $state<string | null>(null);
	let pickerOpen = $state(false);
	let newOpen = $state(false);
	let suggesting = $state(false);
	const KIND_OF_TAB: Record<typeof tab, AssetKind> = { characters: 'character', locations: 'location', items: 'item' };

	type Taggable = { name: string; tags?: string[]; category?: string | null };
	function shown<T extends Taggable>(list: T[], text: (e: T) => string): T[] {
		const words = search.toLowerCase().split(/\s+/).filter(Boolean);
		return list.filter((e) => {
			const tags = e.tags ?? [];
			if (activeTag) {
				const ok = activeTag.startsWith('category:')
					? `category:${e.category ?? ''}` === activeTag
					: tags.includes(activeTag);
				if (!ok) return false;
			}
			const hay = [e.name, e.category ?? '', ...tags, text(e)].join(' ').toLowerCase();
			return words.every((w) => hay.includes(w));
		});
	}

	function toggleTag(tag: string) {
		activeTag = activeTag === tag ? null : tag;
	}

	async function saveTags(kind: AssetKind, id: number, fields: { tags: string[]; category?: string | null }) {
		try {
			if (kind === 'character') await projects.updateCharacter(projectId, id, fields);
			else if (kind === 'location') await projects.updateLocation(projectId, id, fields);
			else await projects.updateItem(projectId, id, fields);
			await client.invalidateQueries({ queryKey: ['assets'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function toLibrary(kind: AssetKind, id: number, name: string) {
		try {
			await assetLibraryApi.save(projectId, kind, id);
			toast.success(t('assets.savedToLibrary', { name }));
			await client.invalidateQueries({ queryKey: ['asset-library'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	let describing = $state<string | null>(null);
	async function describeAsset(kind: AssetKind, id: number) {
		describing = `${kind}:${id}`;
		try {
			await assetLibraryApi.describeProject(projectId, kind, id);
			await client.invalidateQueries({ queryKey: ['assets'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			describing = null;
		}
	}

	async function fromImages(kind: AssetKind, id: number) {
		try {
			await assetLibraryApi.fromImages(projectId, kind, id);
			toast.success(t('assets.fromPicturesQueued'));
			await client.invalidateQueries({ queryKey: ['jobs'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function suggestTags() {
		suggesting = true;
		try {
			const r = await assetLibraryApi.suggestTags(projectId);
			const n = Object.values(r).reduce((a, b) => a + b, 0);
			toast.success(t('assets.tagsSuggested', { n }));
			await client.invalidateQueries({ queryKey: ['assets'] });
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			suggesting = false;
		}
	}
	let drafts = $state<Record<string, string>>({});
	let savingKey = $state<string | null>(null);
	let sheetWorkflowId = $state<number | ''>('');
	let envWorkflowId = $state<number | ''>('');
	let itemWorkflowId = $state<number | ''>('');
	let sheetInputValues = $state<Record<string, string | number>>({});
	let envInputValues = $state<Record<string, string | number>>({});
	let itemInputValues = $state<Record<string, string | number>>({});
	let sheetMissing = $state<string[]>([]);
	let envMissing = $state<string[]>([]);
	let itemMissing = $state<string[]>([]);
	let sheetAttempted = $state(false);
	let envAttempted = $state(false);
	let itemAttempted = $state(false);
	let lastSheetWf = $state<number | '' | null>(null);
	let previewSrc = $state<string | null>(null);
	let previewAlt = $state('');

	function openPreview(src: string | null | undefined, alt: string) {
		if (!src) return;
		previewSrc = src;
		previewAlt = alt;
	}
	let lastEnvWf = $state<number | '' | null>(null);
	let lastItemWf = $state<number | '' | null>(null);
	let bulkPending = $state(false);

	type DeleteTarget = { kind: 'character' | 'location' | 'item'; id: number; name: string };
	let deleteTarget = $state<DeleteTarget | null>(null);
	let deleteOpen = $state(false);
	let fileInput: HTMLInputElement | null = $state(null);
	let uploadTarget = $state<DeleteTarget | null>(null);
	let uploadingKey = $state<string | null>(null);

	const assetsQuery = createQuery(
		toStore(() => ({
			queryKey: ['assets', projectId],
			queryFn: () => projects.getAssets(projectId),
		})),
	);
	const shownCharacters = $derived(
		shown($assetsQuery.data?.characters ?? [], (c) => `${c.role ?? ''} ${c.appearance ?? ''} ${c.personality ?? ''}`),
	);
	const shownLocations = $derived(shown($assetsQuery.data?.locations ?? [], (l) => l.description ?? ''));
	// Items grouped by category (uncategorised last), then by name.
	const shownItems = $derived(
		shown($assetsQuery.data?.items ?? [], (i) => i.description ?? '').sort(
			(a, b) =>
				(a.category ?? '~').localeCompare(b.category ?? '~') || a.name.localeCompare(b.name),
		),
	);
	const filtering = $derived(Boolean(search.trim() || activeTag));

	const workflowsQuery = createQuery({
		queryKey: ['workflows'],
		queryFn: workflows.list,
	});

	// Shared cache key with QueueStage — refreshed via SSE invalidation from
	// the project page (job.* events), not polling.
	const jobsQuery = createQuery(
		toStore(() => ({
			queryKey: ['jobs', projectId],
			queryFn: () => jobsApi.list(projectId),
			// Keep the cards' "generating" state current while something renders.
			refetchInterval: (q: { state: { data?: Job[] } }) =>
				(q.state.data ?? []).some((j) => j.status === 'pending' || j.status === 'running') ? 4000 : false,
		})),
	);

	// Project cover: full asset path (as stored in the DB) chosen as the project thumbnail.
	const projectQuery = createQuery(
		toStore(() => ({
			queryKey: ['project', projectId],
			queryFn: () => projects.get(projectId),
		})),
	);

	const coverPath = $derived($projectQuery.data?.cover_path ?? null);

	const coverMutation = createMutation({
		mutationFn: (path: string | null) => projects.update(projectId, { cover_path: path }),
		onSuccess: async (updated) => {
			await client.invalidateQueries({ queryKey: ['project'] });
			await client.invalidateQueries({ queryKey: ['projects'] });
			toast.success(
				updated.cover_path ? t('assets.toast.coverUpdated') : t('assets.toast.coverRemoved'),
			);
		},
		onError: (err) => {
			toast.error(err instanceof Error ? err.message : t('assets.toast.coverFail'));
		},
	});

	/** True when this asset image is the current project cover. */
	function isCover(path: string | null): boolean {
		return path !== null && coverPath === path;
	}

	/** Same action sets and clears: clicking the current cover removes it. */
	function toggleCover(path: string | null) {
		if (!path) return;
		$coverMutation.mutate(coverPath === path ? null : path);
	}

	const imageWorkflows = $derived(
		(($workflowsQuery.data ?? []) as Workflow[]).filter((w) => w.is_enabled && w.kind === 'image'),
	);

	const sheetWorkflow = $derived(
		imageWorkflows.find((w) => w.id === sheetWorkflowId) ?? null,
	);
	const envWorkflow = $derived(imageWorkflows.find((w) => w.id === envWorkflowId) ?? null);
	const itemWorkflow = $derived(imageWorkflows.find((w) => w.id === itemWorkflowId) ?? null);

	const sheetHasPrompt = $derived(workflowHasPromptInput(sheetWorkflow?.input_schema));
	const envHasPrompt = $derived(workflowHasPromptInput(envWorkflow?.input_schema));
	const itemHasPrompt = $derived(workflowHasPromptInput(itemWorkflow?.input_schema));
	const showCharPrompt = $derived(sheetHasPrompt);

	const jobs = $derived($jobsQuery.data ?? []);

	const assetOptions = $derived.by(() => {
		const chars = $assetsQuery.data?.characters ?? [];
		const locs = $assetsQuery.data?.locations ?? [];
		const items = $assetsQuery.data?.items ?? [];
		const opts: Array<{ label: string; path: string; group?: 'character' | 'location' | 'item' }> =
			[];
		for (const c of chars) {
			if (c.sheet_path) {
				opts.push({
					label: `${c.name} · ${t('assets.optionSheet')}`,
					path: c.sheet_path,
					group: 'character',
				});
			}
		}
		for (const loc of locs) {
			if (loc.reference_image_path) {
				opts.push({
					label: `${loc.name} · ${t('assets.optionEnvironment')}`,
					path: loc.reference_image_path,
					group: 'location',
				});
			}
		}
		for (const it of items) {
			if (it.reference_image_path) {
				opts.push({
					label: `${it.name} · ${t('assets.optionItem')}`,
					path: it.reference_image_path,
					group: 'item',
				});
			}
		}
		return opts;
	});

	// Defaults come from Settings > Workflows roles (else the first workflow).
	const rolesQuery = createQuery({ queryKey: ['workflow-roles'], queryFn: settingsApi.workflowRoles });
	$effect(() => {
		const list = imageWorkflows;
		if (list.length === 0 || $rolesQuery.isLoading) return;
		const roleOf = (key: string) => {
			const id = ($rolesQuery.data ?? []).find((r) => r.key === key)?.resolved;
			return list.some((w) => w.id === id) ? (id as number) : list[0].id;
		};
		if (sheetWorkflowId === '') sheetWorkflowId = roleOf('asset_sheet');
		if (envWorkflowId === '') envWorkflowId = roleOf('asset_location');
		if (itemWorkflowId === '') itemWorkflowId = roleOf('asset_item');
	});

	$effect(() => {
		if (sheetWorkflowId === lastSheetWf) return;
		lastSheetWf = sheetWorkflowId;
		sheetInputValues = {};
		sheetAttempted = false;
	});
	$effect(() => {
		if (envWorkflowId === lastEnvWf) return;
		lastEnvWf = envWorkflowId;
		envInputValues = {};
		envAttempted = false;
	});
	$effect(() => {
		if (itemWorkflowId === lastItemWf) return;
		lastItemWf = itemWorkflowId;
		itemInputValues = {};
		itemAttempted = false;
	});

	const generateMutation = createMutation({
		mutationFn: (payload: {
			missing_only?: boolean;
			character_ids?: number[];
			location_ids?: number[];
			item_ids?: number[];
			workflow_id?: number;
			input_values?: Record<string, unknown>;
			asset_target?: 'sheet';
			prompt?: string;
		}) => projects.generateAssets(projectId, payload),
		onSuccess: async () => {
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['jobs'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			toast.success(t('assets.toast.queued'));
		},
		onError: (err) => {
			toast.error(err instanceof Error ? err.message : String(err));
		},
	});

	const retryJobMutation = createMutation({
		mutationFn: (jobId: number) => jobsApi.retry(jobId),
		onSuccess: () => {
			client.invalidateQueries({ queryKey: ['jobs'] });
			toast.success(t('assets.toast.jobRequeued'));
		},
		onError: (err) => {
			toast.error(err instanceof Error ? err.message : t('assets.toast.retryFailed'));
		},
	});

	const deleteEntityMutation = createMutation({
		mutationFn: (target: DeleteTarget) =>
			target.kind === 'character'
				? projects.deleteCharacter(projectId, target.id)
				: target.kind === 'location'
					? projects.deleteLocation(projectId, target.id)
					: projects.deleteItem(projectId, target.id),
		onSuccess: async (_data, target) => {
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			await client.invalidateQueries({ queryKey: ['scenes'] });
			toast.success(t('assets.toast.deleted', { name: target.name }));
		},
		onError: (err) => {
			toast.error(err instanceof Error ? err.message : t('assets.toast.deleteFailed'));
		},
	});

	const busy = $derived(bulkPending || $generateMutation.isPending);

	/** Latest image job for one entity — drives the on-card generation state. */
	function entityJob(
		list: Job[],
		key: 'character_id' | 'location_id' | 'item_id',
		id: number,
	): Job | undefined {
		const matches = list.filter((j) => j.kind === 'image' && j.payload?.[key] === id);
		if (matches.length === 0) return undefined;
		return matches.sort((a, b) => b.id - a.id)[0];
	}

	function jobStateOf(job: Job | undefined): 'generating' | 'failed' | null {
		if (!job) return null;
		if (job.status === 'pending' || job.status === 'running') return 'generating';
		if (job.status === 'failed') return 'failed';
		return null;
	}

	function charKey(id: number) {
		return `c:${id}`;
	}
	function locKey(id: number) {
		return `l:${id}`;
	}
	function itemKey(id: number) {
		return `i:${id}`;
	}

	function promptForChar(c: Character): string {
		const k = charKey(c.id);
		if (drafts[k] !== undefined) return drafts[k];
		return c.consistency_prompt?.trim() || characterSheetTemplate(c);
	}

	function promptForLoc(loc: Location): string {
		const k = locKey(loc.id);
		if (drafts[k] !== undefined) return drafts[k];
		return loc.consistency_prompt?.trim() || locationReferenceTemplate(loc);
	}

	function promptForItem(item: Item): string {
		const k = itemKey(item.id);
		if (drafts[k] !== undefined) return drafts[k];
		return item.consistency_prompt?.trim() || itemReferenceTemplate(item);
	}

	function setDraft(key: string, value: string) {
		drafts = { ...drafts, [key]: value };
	}

	function requireWorkflow(id: number | '', needKey: string): number | null {
		if (id === '') {
			toast.error(t(needKey));
			return null;
		}
		return Number(id);
	}

	function requireComplete(missing: string[], markAttempted: () => void): boolean {
		if (missing.length === 0) return true;
		markAttempted();
		toast.error(t('assets.wf.missingInputs', { list: missing.join(', ') }));
		return false;
	}

	function kindNoun(kind: DeleteTarget['kind']): string {
		return kind === 'character'
			? t('assets.kind.character')
			: kind === 'location'
				? t('assets.kind.environment')
				: t('assets.kind.item');
	}

	function requestDelete(target: DeleteTarget) {
		deleteTarget = target;
		deleteOpen = true;
	}

	async function saveCharacterPrompt(c: Character) {
		const key = charKey(c.id);
		const text = promptForChar(c);
		savingKey = key;
		try {
			await projects.updateCharacter(projectId, c.id, { consistency_prompt: text });
			const { [key]: _, ...rest } = drafts;
			drafts = rest;
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			toast.success(t('assets.toast.savedPrompt', { name: c.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : t('assets.toast.savePromptFailed'));
		} finally {
			savingKey = null;
		}
	}

	async function saveLocationPrompt(loc: Location) {
		const key = locKey(loc.id);
		const text = promptForLoc(loc);
		savingKey = key;
		try {
			await projects.updateLocation(projectId, loc.id, { consistency_prompt: text });
			const { [key]: _, ...rest } = drafts;
			drafts = rest;
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			toast.success(t('assets.toast.savedPrompt', { name: loc.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : t('assets.toast.savePromptFailed'));
		} finally {
			savingKey = null;
		}
	}

	async function saveItemPrompt(item: Item) {
		const key = itemKey(item.id);
		const text = promptForItem(item);
		savingKey = key;
		try {
			await projects.updateItem(projectId, item.id, { consistency_prompt: text });
			const { [key]: _, ...rest } = drafts;
			drafts = rest;
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			toast.success(t('assets.toast.savedPrompt', { name: item.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : t('assets.toast.savePromptFailed'));
		} finally {
			savingKey = null;
		}
	}

	async function generateCharacter(c: Character) {
		const wfId = requireWorkflow(sheetWorkflowId, 'assets.wf.needSheet');
		if (wfId == null) return;
		if (!requireComplete(sheetMissing, () => (sheetAttempted = true))) return;
		const prompt = sheetHasPrompt ? promptForChar(c) : '';
		if (sheetHasPrompt) {
			await projects.updateCharacter(projectId, c.id, { consistency_prompt: prompt });
			const { [charKey(c.id)]: _, ...rest } = drafts;
			drafts = rest;
		}
		$generateMutation.mutate({
			missing_only: false,
			character_ids: [c.id],
			location_ids: [],
			asset_target: 'sheet',
			workflow_id: wfId,
			input_values: compactInputValues(sheetInputValues),
			prompt: prompt || undefined,
		});
	}

	async function generateLocation(loc: Location) {
		const wfId = requireWorkflow(envWorkflowId, 'assets.wf.needEnv');
		if (wfId == null) return;
		if (!requireComplete(envMissing, () => (envAttempted = true))) return;
		const prompt = envHasPrompt ? promptForLoc(loc) : '';
		if (envHasPrompt) {
			await projects.updateLocation(projectId, loc.id, { consistency_prompt: prompt });
			const { [locKey(loc.id)]: _, ...rest } = drafts;
			drafts = rest;
		}
		$generateMutation.mutate({
			missing_only: false,
			character_ids: [],
			location_ids: [loc.id],
			workflow_id: wfId,
			input_values: compactInputValues(envInputValues),
			prompt: prompt || undefined,
		});
	}

	async function generateItem(item: Item) {
		const wfId = requireWorkflow(itemWorkflowId, 'assets.wf.needItem');
		if (wfId == null) return;
		if (!requireComplete(itemMissing, () => (itemAttempted = true))) return;
		const prompt = itemHasPrompt ? promptForItem(item) : '';
		if (itemHasPrompt) {
			await projects.updateItem(projectId, item.id, { consistency_prompt: prompt });
			const { [itemKey(item.id)]: _, ...rest } = drafts;
			drafts = rest;
		}
		$generateMutation.mutate({
			missing_only: false,
			character_ids: [],
			location_ids: [],
			item_ids: [item.id],
			workflow_id: wfId,
			input_values: compactInputValues(itemInputValues),
			prompt: prompt || undefined,
		});
	}

	async function generateMissing() {
		const chars = $assetsQuery.data?.characters ?? [];
		const locs = $assetsQuery.data?.locations ?? [];
		const items = $assetsQuery.data?.items ?? [];
		const sheetWf = sheetWorkflowId === '' ? null : Number(sheetWorkflowId);
		const envWf = envWorkflowId === '' ? null : Number(envWorkflowId);
		const itemWf = itemWorkflowId === '' ? null : Number(itemWorkflowId);

		if (sheetWf == null && envWf == null && itemWf == null) {
			toast.error(t('assets.wf.needWorkflow'));
			return;
		}

		bulkPending = true;
		try {
			let total = 0;
			if (sheetWf != null && chars.length > 0) {
				const r = await projects.generateAssets(projectId, {
					missing_only: true,
					asset_target: 'sheet',
					workflow_id: sheetWf,
					character_ids: chars.map((c) => c.id),
					input_values: compactInputValues(sheetInputValues),
				});
				total += r.jobs.length;
			}
			if (envWf != null && locs.length > 0) {
				const r = await projects.generateAssets(projectId, {
					missing_only: true,
					workflow_id: envWf,
					character_ids: [],
					location_ids: locs.map((l) => l.id),
					input_values: compactInputValues(envInputValues),
				});
				total += r.jobs.length;
			}
			if (itemWf != null && items.length > 0) {
				const r = await projects.generateAssets(projectId, {
					missing_only: true,
					workflow_id: itemWf,
					character_ids: [],
					location_ids: [],
					item_ids: items.map((it) => it.id),
					input_values: compactInputValues(itemInputValues),
				});
				total += r.jobs.length;
			}
			await client.invalidateQueries({ queryKey: ['assets'] });
			await client.invalidateQueries({ queryKey: ['jobs'] });
			await client.invalidateQueries({ queryKey: ['story'] });
			toast.success(
				total > 0
					? total === 1
						? t('assets.toast.queuedOne')
						: t('assets.toast.queuedMany', { count: total })
					: t('assets.toast.nothingMissing'),
			);
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			bulkPending = false;
		}
	}

	function goToStory() {
		goto('?stage=story', { keepFocus: true, noScroll: true });
	}

	function uploadKey(kind: DeleteTarget['kind'], id: number) {
		if (kind === 'character') return charKey(id);
		if (kind === 'location') return locKey(id);
		return itemKey(id);
	}

	function openOwnImage(target: DeleteTarget) {
		uploadTarget = target;
		fileInput?.click();
	}

	async function onOwnImageChosen(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		const target = uploadTarget;
		uploadTarget = null;
		if (!file || !target) return;
		if (!file.type.startsWith('image/')) {
			toast.error(t('assets.toast.chooseImage'));
			return;
		}
		const key = uploadKey(target.kind, target.id);
		uploadingKey = key;
		try {
			const uploaded = await playgroundApi.upload(file);
			if (uploaded.kind !== 'image') {
				toast.error(t('assets.toast.notImage'));
				return;
			}
			if (target.kind === 'character') {
				await projects.updateCharacter(projectId, target.id, { sheet_path: uploaded.path });
			} else if (target.kind === 'location') {
				await projects.updateLocation(projectId, target.id, {
					reference_image_path: uploaded.path,
				});
			} else {
				await projects.updateItem(projectId, target.id, { reference_image_path: uploaded.path });
			}
			await client.invalidateQueries({ queryKey: ['assets', projectId] });
			await client.invalidateQueries({ queryKey: ['project', projectId] });
			await client.invalidateQueries({ queryKey: ['projects'] });
			toast.success(t('assets.toast.imageAdded', { name: target.name }));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : t('assets.toast.addImageFailed'));
		} finally {
			uploadingKey = null;
		}
	}
</script>

<input
	bind:this={fileInput}
	type="file"
	class="sr-only"
	accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
	onchange={onOwnImageChosen}
/>

<header class="stage-header">
	<div>
		<h2>{t('assets.title')}</h2>
		<p class="lead">{t('assets.lead')}</p>
	</div>
	<div class="stage-actions">
		<Button
			variant="primary"
			disabled={busy}
			title={t('assets.generateMissingTitle')}
			onclick={generateMissing}
		>
			{busy ? t('assets.queuing') : t('assets.generateMissing')}
		</Button>
	</div>
</header>

<div class="tabs-row">
	<div class="tabs" role="tablist" aria-label={t('assets.tabAria')}>
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'characters'}
			class:active={tab === 'characters'}
			onclick={() => (tab = 'characters')}
			>{t('assets.tabCharacters')}{#if $assetsQuery.data}
				· {$assetsQuery.data.characters.length}{/if}</button
		>
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'locations'}
			class:active={tab === 'locations'}
			onclick={() => (tab = 'locations')}
			>{t('assets.tabEnvironments')}{#if $assetsQuery.data}
				· {$assetsQuery.data.locations.length}{/if}</button
		>
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'items'}
			class:active={tab === 'items'}
			onclick={() => (tab = 'items')}
			>{t('assets.tabItems')}{#if $assetsQuery.data}
				· {$assetsQuery.data.items.length}{/if}</button
		>
	</div>
</div>

<div class="asset-toolbar">
	<input class="field-input" type="search" placeholder={t('assets.searchPlaceholder')} bind:value={search} />
	{#if activeTag}
		<button type="button" class="tag-filter" onclick={() => (activeTag = null)}>{activeTag} ×</button>
	{/if}
	<Button size="sm" variant="secondary" loading={suggesting} onclick={suggestTags}>{t('assets.suggestTags')}</Button>
	<Button size="sm" variant="secondary" onclick={() => (pickerOpen = true)}>{t('assets.fromLibrary')}</Button>
	<Button size="sm" variant="primary" onclick={() => (newOpen = true)}>{t(`assets.new.${KIND_OF_TAB[tab]}`)}</Button>
	<span class="queue-end"><RenderQueue {projectId} /></span>
</div>

<NewAssetDialog
	bind:open={newOpen}
	{projectId}
	kind={KIND_OF_TAB[tab]}
	oncreated={() => {
		client.invalidateQueries({ queryKey: ['assets'] });
		client.invalidateQueries({ queryKey: ['jobs'] });
	}}
/>

{#if tab === 'characters'}
	<div class="matches-wrap"><LibraryMatches {projectId} /></div>
{/if}

<LibraryPicker
	bind:open={pickerOpen}
	{projectId}
	kind={KIND_OF_TAB[tab]}
	onadded={() => client.invalidateQueries({ queryKey: ['assets'] })}
/>

{#if imageWorkflows.length === 0}
	<p class="hint-warn">
		{t('assets.noWorkflowPre')} <a href="/settings?tab=workflows">{t('assets.noWorkflowLink')}</a>
		{t('assets.noWorkflowPost')}
	</p>
{/if}

<div class="settings-wrap">
	<Card>
	{#snippet header()}
		<h3 class="card-h">{t('assets.genSettings')}</h3>
		<span class="muted small"
			>{tab === 'characters'
				? t('assets.charSheets')
				: tab === 'locations'
					? t('assets.envRefs')
					: t('assets.itemRefs')}</span
		>
	{/snippet}
	{#if tab === 'characters'}
		<label class="wf-field">
			<span class="field-label">{t('assets.wf.sheet')}</span>
			<select
				class="field-select"
				value={sheetWorkflowId}
				onchange={(e) => {
					const v = e.currentTarget.value;
					sheetWorkflowId = v ? Number(v) : '';
				}}
			>
				{#if imageWorkflows.length === 0}
					<option value="">{t('assets.wf.none')}</option>
				{:else}
					{#each imageWorkflows as w}
						<option value={w.id}>{w.name}</option>
					{/each}
				{/if}
			</select>
		</label>
		{#if sheetWorkflow}
			<ComfyDynamicForm
				inputs={withoutPromptInputs(sheetWorkflow.input_schema)}
				bind:values={sheetInputValues}
				assetOptions={assetOptions}
				showErrors={sheetAttempted}
				onValidityChange={(m) => (sheetMissing = m)}
			/>
		{:else}
			<p class="muted small">{t('assets.wf.selectSheet')}</p>
		{/if}
	{:else if tab === 'locations'}
		<label class="wf-field">
			<span class="field-label">{t('assets.wf.environment')}</span>
			<select
				class="field-select"
				value={envWorkflowId}
				onchange={(e) => {
					const v = e.currentTarget.value;
					envWorkflowId = v ? Number(v) : '';
				}}
			>
				{#if imageWorkflows.length === 0}
					<option value="">{t('assets.wf.none')}</option>
				{:else}
					{#each imageWorkflows as w}
						<option value={w.id}>{w.name}</option>
					{/each}
				{/if}
			</select>
		</label>
		{#if envWorkflow}
			<ComfyDynamicForm
				inputs={withoutPromptInputs(envWorkflow.input_schema)}
				bind:values={envInputValues}
				assetOptions={assetOptions}
				showErrors={envAttempted}
				onValidityChange={(m) => (envMissing = m)}
			/>
		{:else}
			<p class="muted small">{t('assets.wf.selectEnv')}</p>
		{/if}
	{:else}
		<label class="wf-field">
			<span class="field-label">{t('assets.wf.item')}</span>
			<select
				class="field-select"
				value={itemWorkflowId}
				onchange={(e) => {
					const v = e.currentTarget.value;
					itemWorkflowId = v ? Number(v) : '';
				}}
			>
				{#if imageWorkflows.length === 0}
					<option value="">{t('assets.wf.none')}</option>
				{:else}
					{#each imageWorkflows as w}
						<option value={w.id}>{w.name}</option>
					{/each}
				{/if}
			</select>
		</label>
		{#if itemWorkflow}
			<ComfyDynamicForm
				inputs={withoutPromptInputs(itemWorkflow.input_schema)}
				bind:values={itemInputValues}
				assetOptions={assetOptions}
				showErrors={itemAttempted}
				onValidityChange={(m) => (itemMissing = m)}
			/>
		{:else}
			<p class="muted small">{t('assets.wf.selectItem')}</p>
		{/if}
	{/if}
	</Card>
</div>

{#if $assetsQuery.isLoading}
	<div class="grid">
		{#each [0, 1, 2] as n (n)}
			<div class="entity-card">
				<Skeleton height="225px" />
				<div class="skel-body">
					<Skeleton width="55%" height="15px" />
					<Skeleton width="85%" />
					<Skeleton width="70%" />
				</div>
			</div>
		{/each}
	</div>
{:else if $assetsQuery.data}
	{#if tab === 'characters'}
		{#if $assetsQuery.data.characters.length === 0}
			<EmptyState
				title={t('assets.noCharsTitle')}
				body={t('assets.noCharsBody')}
			>
				{#snippet icon()}
					<Icon name="assets" size={28} />
				{/snippet}
				{#snippet action()}
					<Button variant="primary" onclick={goToStory}>{t('assets.goToStory')}</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<div class="grid">
				{#if filtering && shownCharacters.length === 0}<p class="muted small">{t('assets.noMatches')}</p>{/if}
				{#each shownCharacters as char (char.id)}
					{@const key = charKey(char.id)}
					{@const job = entityJob(jobs, 'character_id', char.id)}
					{@const jstate = jobStateOf(job)}
					<article class="entity-card">
						<div class="media">
							<AssetThumb
								src={assetUrl(char.sheet_path)}
								alt={t('assets.charSheetAlt', { name: char.name })}
								placeholder={t('assets.charSheetPlaceholder')}
								tall
								jobState={jstate}
								onPreview={(url) => openPreview(url, t('assets.charSheetAlt', { name: char.name }))}
							/>
							{#if isCover(char.sheet_path) && jstate !== 'generating'}
								<span class="cover-chip"><Icon name="image" size={11} /> {t('assets.cover')}</span>
							{/if}
							<div class="quick-actions">
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.viewFull')}
									disabled={!char.sheet_path}
									onclick={() =>
										openPreview(assetUrl(char.sheet_path), t('assets.charSheetAlt', { name: char.name }))}
								>
									<Icon name="zoom-in" size={14} /><span class="sr-only"
										>{t('assets.viewCharFull', { name: char.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.regenSheet')}
									disabled={busy || sheetWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateCharacter(char)}
								>
									<Icon name="retry" size={14} /><span class="sr-only"
										>{t('assets.regenSheetFor', { name: char.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={isCover(char.sheet_path) ? t('assets.removeCover') : t('assets.setCover')}
									disabled={!char.sheet_path}
									onclick={() => toggleCover(char.sheet_path)}
								>
									<Icon name="image" size={14} /><span class="sr-only"
										>{isCover(char.sheet_path)
											? t('assets.removeCoverChar', { name: char.name })
											: t('assets.setCoverChar', { name: char.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.deleteCharacter')}
									onclick={() =>
										requestDelete({ kind: 'character', id: char.id, name: char.name })}
								>
									<Icon name="trash" size={14} /><span class="sr-only"
										>{t('assets.deleteFor', { name: char.name })}</span
									>
								</Button>
							</div>
							{#if jstate === 'generating'}
								<div class="gen-bar">
									<ProgressBar indeterminate size="sm" label="{t('assets.generating')}" />
								</div>
							{/if}
						</div>

						<div class="entity-body">
							<div class="title-row">
								<strong>{char.name}</strong>
								{#if char.role}
									<span class="muted small"
										>{char.role}{#if char.age} · {char.age}{/if}</span
									>
								{:else if char.age}
									<span class="muted small">{char.age}</span>
								{/if}
							</div>
							<AssetTags
								tags={char.tags ?? []}
								
								active={activeTag}
								onfilter={toggleTag}
								onsave={(f) => saveTags('character', char.id, f)}
								ontolibrary={() => toLibrary('character', char.id, char.name)}
								pictures={char.source_images?.length ?? 0}
								onfromimages={() => fromImages('character', char.id)}
								readonly={(char.appearance ?? '').trimStart().startsWith('CHARACTER INFORMATION')}
							/>
							{#if !char.appearance}
								<Button size="sm" variant="ghost" loading={describing === 'character:' + char.id}
									onclick={() => describeAsset('character', char.id)}>{t('assets.generateDescription')}</Button>
							{/if}
							{#if char.appearance}
								<p class="facts" title={char.appearance}>
									<span class="k">{t('assets.appearance')}</span> {char.appearance}
								</p>
							{/if}
							{#if char.personality}
								<p class="facts" title={char.personality}>
									<span class="k">{t('assets.personality')}</span> {char.personality}
								</p>
							{/if}
							{#if jstate === 'failed' && job}
								{#if job.error}
									<p class="err-line" title={job.error}>{job.error}</p>
								{/if}
							{/if}

							<div class="row">
								<Button
									variant="primary"
									size="sm"
									disabled={busy || sheetWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateCharacter(char)}
								>
									{jstate === 'generating'
										? t('assets.generatingEllipsis')
										: char.sheet_path
											? t('assets.regenSheet')
											: t('assets.generateSheet')}
								</Button>
								<Button
									variant="secondary"
									size="sm"
									disabled={jstate === 'generating' || uploadingKey === charKey(char.id)}
									loading={uploadingKey === charKey(char.id)}
									onclick={() =>
										openOwnImage({ kind: 'character', id: char.id, name: char.name })}
								>
									<Icon name="upload" size={13} />
									{char.sheet_path ? t('assets.replaceImage') : t('assets.uploadImage')}
								</Button>
								{#if jstate === 'failed' && job}
									<Button
										variant="danger"
										size="sm"
										loading={$retryJobMutation.isPending}
										onclick={() => $retryJobMutation.mutate(job.id)}
									>
										<Icon name="retry" size={13} /> {t('common.retry')}
									</Button>
								{/if}
							</div>

							{#if showCharPrompt}
								<details class="prompt-fold">
									<summary>{t('assets.editPrompt')}</summary>
									<label class="prompt-field">
										<span class="field-label">{t('assets.sentToComfy')}</span>
										<textarea
											class="field-textarea prompt-area"
											rows="8"
											value={promptForChar(char)}
											oninput={(e) => setDraft(key, e.currentTarget.value)}
										></textarea>
									</label>
									<div class="row">
										<Button
											variant="ghost"
											size="sm"
											onclick={() => setDraft(key, characterSheetTemplate(char))}
										>
											{t('assets.resetSheetTemplate')}
										</Button>
										<Button
											variant="secondary"
											size="sm"
											loading={savingKey === key}
											onclick={() => saveCharacterPrompt(char)}
										>
											{t('assets.savePrompt')}
										</Button>
									</div>
								</details>
							{:else}
								<p class="muted small prompt-note">
		{t('assets.wf.noPromptPre')} <code>(Input:prompt)</code>{t('assets.wf.noPromptPost')}
	</p>
							{/if}
						</div>
					</article>
				{/each}
			</div>
		{/if}
	{:else if tab === 'locations'}
		{#if $assetsQuery.data.locations.length === 0}
			<EmptyState
				title={t('assets.noEnvsTitle')}
				body={t('assets.noEnvsBody')}
			>
				{#snippet icon()}
					<Icon name="folder" size={28} />
				{/snippet}
				{#snippet action()}
					<Button variant="primary" onclick={goToStory}>{t('assets.goToStory')}</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<div class="grid">
				{#if filtering && shownLocations.length === 0}<p class="muted small">{t('assets.noMatches')}</p>{/if}
				{#each shownLocations as loc (loc.id)}
					{@const key = locKey(loc.id)}
					{@const job = entityJob(jobs, 'location_id', loc.id)}
					{@const jstate = jobStateOf(job)}
					<article class="entity-card">
						<div class="media">
							<AssetThumb
								src={assetUrl(loc.reference_image_path)}
								alt={loc.name}
								placeholder={t('assets.refPlaceholder')}
								tall
								jobState={jstate}
								onPreview={(url) => openPreview(url, loc.name)}
							/>
							{#if isCover(loc.reference_image_path) && jstate !== 'generating'}
								<span class="cover-chip"><Icon name="image" size={11} /> {t('assets.cover')}</span>
							{/if}
							<div class="quick-actions">
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.viewFull')}
									disabled={!loc.reference_image_path}
									onclick={() => openPreview(assetUrl(loc.reference_image_path), loc.name)}
								>
									<Icon name="zoom-in" size={14} /><span class="sr-only"
										>{t('assets.viewRefFull', { name: loc.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.regenImage')}
									disabled={busy || envWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateLocation(loc)}
								>
									<Icon name="retry" size={14} /><span class="sr-only"
										>{t('assets.regenImageFor', { name: loc.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={isCover(loc.reference_image_path) ? t('assets.removeCover') : t('assets.setCover')}
									disabled={!loc.reference_image_path}
									onclick={() => toggleCover(loc.reference_image_path)}
								>
									<Icon name="image" size={14} /><span class="sr-only"
										>{isCover(loc.reference_image_path)
											? t('assets.removeCoverRef', { name: loc.name })
											: t('assets.setCoverRef', { name: loc.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.deleteEnvironment')}
									onclick={() =>
										requestDelete({ kind: 'location', id: loc.id, name: loc.name })}
								>
									<Icon name="trash" size={14} /><span class="sr-only">{t('assets.deleteFor', { name: loc.name })}</span>
								</Button>
							</div>
							{#if jstate === 'generating'}
								<div class="gen-bar">
									<ProgressBar indeterminate size="sm" label="{t('assets.generating')}" />
								</div>
							{/if}
						</div>

						<div class="entity-body">
							<div class="title-row">
								<strong>{loc.name}</strong>
							</div>
							<AssetTags
								tags={loc.tags ?? []}
								
								active={activeTag}
								onfilter={toggleTag}
								onsave={(f) => saveTags('location', loc.id, f)}
								ontolibrary={() => toLibrary('location', loc.id, loc.name)}
								pictures={loc.source_images?.length ?? 0}
								onfromimages={() => fromImages('location', loc.id)}
							/>
							{#if !loc.description}
								<Button size="sm" variant="ghost" loading={describing === 'location:' + loc.id}
									onclick={() => describeAsset('location', loc.id)}>{t('assets.generateDescription')}</Button>
							{/if}
							{#if loc.description}
								<p class="facts" title={loc.description}>
									<span class="k">{t('assets.description')}</span> {loc.description}
								</p>
							{/if}
							{#if jstate === 'failed' && job}
								{#if job.error}
									<p class="err-line" title={job.error}>{job.error}</p>
								{/if}
							{/if}

							<div class="row">
								<Button
									variant="primary"
									size="sm"
									disabled={busy || envWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateLocation(loc)}
								>
									{jstate === 'generating'
										? t('assets.generatingEllipsis')
										: loc.reference_image_path
											? t('assets.regenImage')
											: t('assets.generateImage')}
								</Button>
								<Button
									variant="secondary"
									size="sm"
									disabled={jstate === 'generating' || uploadingKey === locKey(loc.id)}
									loading={uploadingKey === locKey(loc.id)}
									onclick={() =>
										openOwnImage({ kind: 'location', id: loc.id, name: loc.name })}
								>
									<Icon name="upload" size={13} />
									{loc.reference_image_path ? t('assets.replaceImage') : t('assets.uploadImage')}
								</Button>
								{#if jstate === 'failed' && job}
									<Button
										variant="danger"
										size="sm"
										loading={$retryJobMutation.isPending}
										onclick={() => $retryJobMutation.mutate(job.id)}
									>
										<Icon name="retry" size={13} /> {t('common.retry')}
									</Button>
								{/if}
							</div>

							{#if envHasPrompt}
								<details class="prompt-fold">
									<summary>{t('assets.editPrompt')}</summary>
									<label class="prompt-field">
										<span class="field-label">{t('assets.sentToComfy')}</span>
										<textarea
											class="field-textarea prompt-area"
											rows="7"
											value={promptForLoc(loc)}
											oninput={(e) => setDraft(key, e.currentTarget.value)}
										></textarea>
									</label>
									<div class="row">
										<Button
											variant="ghost"
											size="sm"
											onclick={() => setDraft(key, locationReferenceTemplate(loc))}
										>
											{t('assets.resetEnvTemplate')}
										</Button>
										<Button
											variant="secondary"
											size="sm"
											loading={savingKey === key}
											onclick={() => saveLocationPrompt(loc)}
										>
											{t('assets.savePrompt')}
										</Button>
									</div>
								</details>
							{:else}
								<p class="muted small prompt-note">
		{t('assets.wf.noPromptPre')} <code>(Input:prompt)</code>{t('assets.wf.noPromptPost')}
	</p>
							{/if}
						</div>
					</article>
				{/each}
			</div>
		{/if}
	{:else}
		{#if $assetsQuery.data.items.length === 0}
			<EmptyState
				title={t('assets.noItemsTitle')}
				body={t('assets.noItemsBody')}
			>
				{#snippet icon()}
					<Icon name="folder" size={28} />
				{/snippet}
				{#snippet action()}
					<Button variant="primary" onclick={goToStory}>{t('assets.goToStory')}</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<div class="grid">
				{#if filtering && shownItems.length === 0}<p class="muted small">{t('assets.noMatches')}</p>{/if}
				{#each shownItems as item (item.id)}
					{@const key = itemKey(item.id)}
					{@const job = entityJob(jobs, 'item_id', item.id)}
					{@const jstate = jobStateOf(job)}
					<article class="entity-card">
						<div class="media">
							<AssetThumb
								src={assetUrl(item.reference_image_path)}
								alt={item.name}
								placeholder={t('assets.refPlaceholder')}
								tall
								jobState={jstate}
								onPreview={(url) => openPreview(url, item.name)}
							/>
							{#if isCover(item.reference_image_path) && jstate !== 'generating'}
								<span class="cover-chip"><Icon name="image" size={11} /> {t('assets.cover')}</span>
							{/if}
							<div class="quick-actions">
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.viewFull')}
									disabled={!item.reference_image_path}
									onclick={() => openPreview(assetUrl(item.reference_image_path), item.name)}
								>
									<Icon name="zoom-in" size={14} /><span class="sr-only"
										>{t('assets.viewRefFull', { name: item.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.regenImage')}
									disabled={busy || itemWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateItem(item)}
								>
									<Icon name="retry" size={14} /><span class="sr-only"
										>{t('assets.regenImageFor', { name: item.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={isCover(item.reference_image_path) ? t('assets.removeCover') : t('assets.setCover')}
									disabled={!item.reference_image_path}
									onclick={() => toggleCover(item.reference_image_path)}
								>
									<Icon name="image" size={14} /><span class="sr-only"
										>{isCover(item.reference_image_path)
											? t('assets.removeCoverRef', { name: item.name })
											: t('assets.setCoverRef', { name: item.name })}</span
									>
								</Button>
								<Button
									variant="secondary"
									size="sm"
									title={t('assets.deleteItem')}
									onclick={() =>
										requestDelete({ kind: 'item', id: item.id, name: item.name })}
								>
									<Icon name="trash" size={14} /><span class="sr-only">{t('assets.deleteFor', { name: item.name })}</span>
								</Button>
							</div>
							{#if jstate === 'generating'}
								<div class="gen-bar">
									<ProgressBar indeterminate size="sm" label="{t('assets.generating')}" />
								</div>
							{/if}
						</div>

						<div class="entity-body">
							<div class="title-row">
								<strong>{item.name}</strong>
							</div>
							<AssetTags
								tags={item.tags ?? []}
								category={item.category} withCategory
								active={activeTag}
								onfilter={toggleTag}
								onsave={(f) => saveTags('item', item.id, f)}
								ontolibrary={() => toLibrary('item', item.id, item.name)}
								pictures={item.source_images?.length ?? 0}
								onfromimages={() => fromImages('item', item.id)}
							/>
							{#if !item.description}
								<Button size="sm" variant="ghost" loading={describing === 'item:' + item.id}
									onclick={() => describeAsset('item', item.id)}>{t('assets.generateDescription')}</Button>
							{/if}
							{#if item.description}
								<p class="facts" title={item.description}>
									<span class="k">{t('assets.description')}</span> {item.description}
								</p>
							{/if}
							{#if jstate === 'failed' && job}
								{#if job.error}
									<p class="err-line" title={job.error}>{job.error}</p>
								{/if}
							{/if}

							<div class="row">
								<Button
									variant="primary"
									size="sm"
									disabled={busy || itemWorkflowId === '' || jstate === 'generating'}
									onclick={() => generateItem(item)}
								>
									{jstate === 'generating'
										? t('assets.generatingEllipsis')
										: item.reference_image_path
											? t('assets.regenImage')
											: t('assets.generateImage')}
								</Button>
								<Button
									variant="secondary"
									size="sm"
									disabled={jstate === 'generating' || uploadingKey === itemKey(item.id)}
									loading={uploadingKey === itemKey(item.id)}
									onclick={() =>
										openOwnImage({ kind: 'item', id: item.id, name: item.name })}
								>
									<Icon name="upload" size={13} />
									{item.reference_image_path ? t('assets.replaceImage') : t('assets.uploadImage')}
								</Button>
								{#if jstate === 'failed' && job}
									<Button
										variant="danger"
										size="sm"
										loading={$retryJobMutation.isPending}
										onclick={() => $retryJobMutation.mutate(job.id)}
									>
										<Icon name="retry" size={13} /> {t('common.retry')}
									</Button>
								{/if}
							</div>

							{#if itemHasPrompt}
								<details class="prompt-fold">
									<summary>{t('assets.editPrompt')}</summary>
									<label class="prompt-field">
										<span class="field-label">{t('assets.sentToComfy')}</span>
										<textarea
											class="field-textarea prompt-area"
											rows="7"
											value={promptForItem(item)}
											oninput={(e) => setDraft(key, e.currentTarget.value)}
										></textarea>
									</label>
									<div class="row">
										<Button
											variant="ghost"
											size="sm"
											onclick={() => setDraft(key, itemReferenceTemplate(item))}
										>
											{t('assets.resetItemTemplate')}
										</Button>
										<Button
											variant="secondary"
											size="sm"
											loading={savingKey === key}
											onclick={() => saveItemPrompt(item)}
										>
											{t('assets.savePrompt')}
										</Button>
									</div>
								</details>
							{:else}
								<p class="muted small prompt-note">
		{t('assets.wf.noPromptPre')} <code>(Input:prompt)</code>{t('assets.wf.noPromptPost')}
	</p>
							{/if}
						</div>
					</article>
				{/each}
			</div>
		{/if}
	{/if}
{/if}

<ImageLightbox
	src={previewSrc}
	alt={previewAlt}
	onClose={() => {
		previewSrc = null;
		previewAlt = '';
	}}
/>

<ConfirmDialog
	bind:open={deleteOpen}
	title={deleteTarget ? t('assets.confirmDeleteTitle', { kind: kindNoun(deleteTarget.kind) }) : t('assets.confirmDeleteFallback')}
	message={deleteTarget
		? t('assets.confirmDeleteBody', { kind: kindNoun(deleteTarget.kind), name: deleteTarget.name })
		: ''}
	confirmLabel={t('assets.confirmDeleteLabel')}
	danger
	onconfirm={() => {
		const target = deleteTarget;
		deleteTarget = null;
		if (target) $deleteEntityMutation.mutate(target);
	}}
	oncancel={() => (deleteTarget = null)}
/>

<style>
	.stage-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		margin-bottom: var(--space-md);
		gap: var(--space-md);
	}
	.stage-header h2 {
		margin: 0 0 6px;
		font-size: 22px;
		font-weight: 700;
	}
	.lead {
		margin: 0;
		color: var(--text-secondary);
		font-size: 13px;
		max-width: 52ch;
		line-height: 1.45;
	}
	.stage-actions {
		display: flex;
		gap: var(--space-sm);
		flex-shrink: 0;
	}
	.muted {
		color: var(--text-secondary);
		margin: 0;
	}
	.small {
		font-size: 12px;
	}
	.tabs-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-md);
		margin-bottom: var(--space-md);
	}
	.tabs {
		display: flex;
		gap: 8px;
	}
	.tabs button {
		background: var(--bg-elevated);
		border: 1px solid var(--border);
		color: var(--text-secondary);
		border-radius: var(--radius-sm);
		padding: 8px 14px;
		cursor: pointer;
		min-height: 36px;
	}
	.tabs button.active {
		background: var(--accent);
		border-color: var(--accent);
		color: white;
	}
	.tabs button:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
	.hint-warn {
		margin: 0 0 var(--space-md);
		font-size: 13px;
		color: var(--warning);
	}
	.hint-warn a {
		color: var(--accent);
	}
	.settings-wrap {
		margin-bottom: var(--space-lg);
	}
	.card-h {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--text-primary);
	}
	.wf-field {
		display: flex;
		flex-direction: column;
		gap: 4px;
		margin-bottom: var(--space-md);
	}
	.wf-field .field-select {
		width: 100%;
		max-width: 100%;
	}
	.matches-wrap {
		margin-bottom: 12px;
	}
	.asset-toolbar {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
		margin: 0 0 12px;
	}
	.queue-end {
		margin-left: auto;
		/* Some room to the edge of the row. */
		margin-right: 16px;
	}
	.asset-toolbar input {
		flex: 1;
		min-width: 200px;
		max-width: 420px;
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
		grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
		gap: var(--space-md);
	}
	.entity-card {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
		overflow: hidden;
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.skel-body {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px;
	}
	.media {
		position: relative;
	}
	.media :global(.thumb) {
		border: none;
		border-radius: 0;
		border-bottom: 1px solid var(--border);
	}
	.quick-actions {
		position: absolute;
		top: 8px;
		right: 8px;
		display: flex;
		gap: 6px;
		opacity: 0;
		transform: translateY(-4px);
		transition:
			opacity 150ms ease,
			transform 150ms ease;
	}
	.media:hover .quick-actions,
	.media:focus-within .quick-actions {
		opacity: 1;
		transform: none;
	}
	@media (hover: none) {
		.quick-actions {
			opacity: 1;
			transform: none;
		}
	}
	.cover-chip {
		position: absolute;
		left: 8px;
		bottom: 8px;
		z-index: 1;
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 3px 9px;
		border-radius: 999px;
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		line-height: 1.4;
		color: var(--accent);
		background: color-mix(in srgb, var(--accent) 22%, rgba(5, 5, 8, 0.72));
		border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
		filter: drop-shadow(0 1px 3px rgba(0, 0, 0, 0.6));
	}
	.gen-bar {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		padding: 10px 12px 12px;
		background: linear-gradient(transparent, rgba(5, 5, 8, 0.85));
	}
	.entity-body {
		padding: 12px 14px 14px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-width: 0;
	}
	.title-row {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 10px;
	}
	.facts {
		margin: 0;
		font-size: 12px;
		color: var(--text-secondary);
		line-height: 1.4;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.facts .k {
		color: var(--text-muted);
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		margin-right: 6px;
	}
	.err-line {
		margin: 0;
		font-size: 12px;
		color: var(--error);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	.prompt-note {
		margin: 0;
	}
	.prompt-fold {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--bg-elevated);
	}
	.prompt-fold summary {
		cursor: pointer;
		padding: 10px 12px;
		font-size: 13px;
		font-weight: 600;
		color: var(--text-secondary);
		list-style: none;
		user-select: none;
	}
	.prompt-fold summary::-webkit-details-marker {
		display: none;
	}
	.prompt-fold summary::before {
		content: '▸';
		display: inline-block;
		margin-right: 8px;
		color: var(--text-muted);
		transition: transform 0.15s;
	}
	.prompt-fold summary:hover {
		color: var(--text-primary);
	}
	.prompt-fold[open] summary::before {
		transform: rotate(90deg);
	}
	.prompt-fold[open] summary {
		border-bottom: 1px solid var(--border);
		color: var(--text-primary);
	}
	.prompt-fold .prompt-field {
		display: block;
		padding: 12px;
		margin: 0;
	}
	.prompt-fold .row {
		padding: 0 12px 12px;
	}
	.prompt-area {
		font-family: var(--font-mono);
		font-size: 13px;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
</style>
