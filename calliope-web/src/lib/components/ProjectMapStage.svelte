<script lang="ts">
	/**
	 * The Map stage: the process map (what Calliope does with the project, step
	 * by step) and the content map (what the project is made of).
	 */
	import { t } from '$lib/i18n.svelte';
	import ProcessMap from '$lib/components/ProcessMap.svelte';
	import DependencyMap from '$lib/components/DependencyMap.svelte';

	interface Props {
		projectId: number;
	}
	let { projectId }: Props = $props();

	let view = $state<'process' | 'content'>('process');
</script>

<header class="stage-head">
	<div>
		<h2>{t('procmap.title')}</h2>
		<p class="muted">{view === 'process' ? t('procmap.lead') : t('depmap.lead')}</p>
	</div>
	<div class="switch" role="tablist">
		<button type="button" role="tab" aria-selected={view === 'process'} class:on={view === 'process'}
			onclick={() => (view = 'process')}>{t('procmap.viewProcess')}</button>
		<button type="button" role="tab" aria-selected={view === 'content'} class:on={view === 'content'}
			onclick={() => (view = 'content')}>{t('procmap.viewContent')}</button>
	</div>
</header>

{#if view === 'process'}
	<ProcessMap {projectId} />
{:else}
	<DependencyMap {projectId} />
{/if}

<style>
	.stage-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
	}
	.switch {
		display: flex;
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow: hidden;
		flex: none;
	}
	.switch button {
		padding: 6px 14px;
		border: none;
		background: none;
		color: var(--text-secondary);
		cursor: pointer;
		font-size: 13px;
	}
	.switch button.on {
		background: var(--accent);
		color: #fff;
	}
	.muted {
		color: var(--text-muted);
	}
</style>
