<script lang="ts">
	/**
	 * A how-to at the top of a Settings section: what it is, the steps to set it
	 * up, examples and tips. Texts are i18n keys guide.<id>.(title|intro|stepN|exN|tipN|code);
	 * open by default, a closed guide stays closed for this browser.
	 */
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';

	interface Props {
		id: string;
		steps: number;
		examples?: number;
		tips?: number;
		/** Show guide.<id>.code as a code block after the examples. */
		code?: boolean;
	}
	let { id, steps, examples = 0, tips = 0, code = false }: Props = $props();

	const storeKey = $derived(`calliope.guide.${id}.closed`);
	let open = $state(true);
	$effect(() => {
		try {
			open = localStorage.getItem(storeKey) !== '1';
		} catch {
			open = true;
		}
	});

	function toggle() {
		open = !open;
		try {
			localStorage.setItem(storeKey, open ? '0' : '1');
		} catch {
			// private mode: only this page view remembers it
		}
	}

	const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1);
</script>

<div class="guide" class:open>
	<button type="button" class="head" aria-expanded={open} onclick={toggle}>
		<Icon name="info" size={16} />
		<strong>{t(`guide.${id}.title`)}</strong>
		<span class="toggle">{open ? t('guide.hide') : t('guide.show')}</span>
	</button>
	{#if open}
		<div class="body">
			<p>{t(`guide.${id}.intro`)}</p>
			<h4>{t('guide.steps')}</h4>
			<ol>
				{#each range(steps) as n (n)}<li>{t(`guide.${id}.step${n}`)}</li>{/each}
			</ol>
			{#if examples}
				<h4>{t('guide.examples')}</h4>
				<ul>
					{#each range(examples) as n (n)}<li>{t(`guide.${id}.ex${n}`)}</li>{/each}
				</ul>
			{/if}
			{#if code}<pre class="mono">{t(`guide.${id}.code`)}</pre>{/if}
			{#if tips}
				<h4>{t('guide.tips')}</h4>
				<ul class="tips">
					{#each range(tips) as n (n)}<li>{t(`guide.${id}.tip${n}`)}</li>{/each}
				</ul>
			{/if}
		</div>
	{/if}
</div>

<style>
	.guide {
		border: 1px solid var(--border);
		border-left: 3px solid var(--accent);
		border-radius: 10px;
		margin-bottom: 18px;
		background: var(--surface-2, transparent);
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 10px 14px;
		border: none;
		background: none;
		color: inherit;
		cursor: pointer;
		text-align: left;
		font-size: 14px;
	}
	.toggle {
		margin-left: auto;
		font-size: 12px;
		color: var(--accent);
	}
	.body {
		padding: 0 16px 14px;
		font-size: 13px;
		line-height: 1.55;
		color: var(--text-secondary);
	}
	.body p {
		margin: 0 0 8px;
	}
	h4 {
		margin: 12px 0 4px;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-primary, inherit);
	}
	ol,
	ul {
		margin: 0;
		padding-left: 20px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.tips li::marker {
		content: '→ ';
	}
	pre {
		margin: 10px 0 0;
		padding: 10px 12px;
		border-radius: 8px;
		border: 1px solid var(--border);
		font-size: 12px;
		white-space: pre-wrap;
		overflow-x: auto;
	}
</style>
