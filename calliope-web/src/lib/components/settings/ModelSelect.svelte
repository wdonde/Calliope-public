<script lang="ts">
	/**
	 * Model picker for an agent role: a native <select> cannot hold styled
	 * capability badges, so this is a small listbox with the same keyboard basics.
	 */
	export interface ModelOption {
		id: string;
		label: string;
		caps: string[] | null;
		tag?: 'best' | 'ok' | 'avoid';
	}

	interface Props {
		value: string;
		options: ModelOption[];
		emptyLabel: string;
		onchange: (value: string) => void;
	}

	let { value, options, emptyLabel, onchange }: Props = $props();
	let open = $state(false);
	let root: HTMLDivElement;

	const current = $derived(options.find((o) => o.id === value));

	function pick(id: string) {
		open = false;
		if (id !== value) onchange(id);
	}

	function onWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}
</script>

<svelte:window onclick={onWindowClick} />

{#snippet badges(o: ModelOption)}
	{#each o.caps ?? [] as c (c)}<span class="cap">{c}</span>{/each}
	{#if o.tag === 'best'}<span class="tag tag-best">recommended</span>{/if}
	{#if o.tag === 'avoid'}<span class="tag tag-avoid">avoid</span>{/if}
{/snippet}

<div class="model-select" bind:this={root}>
	<button
		type="button"
		class="field-input trigger"
		aria-haspopup="listbox"
		aria-expanded={open}
		onclick={() => (open = !open)}
		onkeydown={(e) => e.key === 'Escape' && (open = false)}
	>
		<span class="label">{current ? current.label : emptyLabel}</span>
		{#if current}{@render badges(current)}{/if}
		<span class="chev" aria-hidden="true">▾</span>
	</button>
	{#if open}
		<ul class="menu" role="listbox">
			<li role="option" aria-selected={value === ''}>
				<button type="button" class:selected={value === ''} onclick={() => pick('')}>{emptyLabel}</button>
			</li>
			{#each options as o (o.id)}
				<li role="option" aria-selected={value === o.id}>
					<button type="button" class:selected={value === o.id} onclick={() => pick(o.id)}>
						<span class="label">{o.label}</span>
						{@render badges(o)}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.model-select {
		position: relative;
	}
	.trigger {
		display: flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		text-align: left;
		cursor: pointer;
	}
	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chev {
		margin-left: auto;
		color: var(--text-muted);
	}
	.menu {
		position: absolute;
		z-index: 20;
		left: 0;
		right: 0;
		top: calc(100% + 4px);
		margin: 0;
		padding: 4px;
		list-style: none;
		background: var(--bg-elevated, #18181b);
		border: 1px solid var(--border, #3f3f46);
		border-radius: 10px;
		box-shadow: 0 12px 32px rgb(0 0 0 / 0.45);
	}
	.menu button {
		display: flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		padding: 8px 10px;
		border: 0;
		border-radius: 6px;
		background: none;
		color: var(--text-primary);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.menu button:hover,
	.menu button.selected {
		background: color-mix(in srgb, var(--accent) 16%, transparent);
	}
	/* Capability badges in the style of ollama.com */
	.cap {
		flex: none;
		padding: 1px 8px;
		border-radius: 6px;
		background: color-mix(in srgb, #6366f1 18%, transparent);
		color: #a5b4fc;
		font-size: 11.5px;
		font-weight: 500;
	}
	.tag {
		flex: none;
		font-size: 11.5px;
		font-weight: 600;
	}
	.tag-best {
		color: #86efac;
	}
	.tag-avoid {
		color: #fca5a5;
	}
</style>
