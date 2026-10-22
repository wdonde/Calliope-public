/**
 * Asset tags: "key:value", "key:value|value" (several values of one key, e.g.
 * "chest:large|full bust") or plain labels. Mirrors agent/character_identity.py.
 */

export interface TagGroup {
	/** Empty for plain labels. */
	key: string;
	values: string[];
}

/** Tags grouped by key, values split ("alias:Toyma" + "alias:Kao" -> alias: Toyma | Kao). */
export function groupTags(tags: string[]): TagGroup[] {
	const groups: TagGroup[] = [];
	for (const tag of tags) {
		const at = tag.indexOf(':');
		if (at < 0) {
			groups.push({ key: '', values: [tag] });
			continue;
		}
		const key = tag.slice(0, at);
		const values = tag.slice(at + 1).split('|').map((v) => v.trim()).filter(Boolean);
		const group = groups.find((g) => g.key === key);
		if (group) group.values.push(...values.filter((v) => !group.values.includes(v)));
		else groups.push({ key, values });
	}
	return groups;
}

/** Whether a tag fits a filter: "chest:large" matches "chest:large|full bust". */
export function tagMatches(tag: string, filter: string): boolean {
	const [key, ...rest] = tag.toLowerCase().split(':');
	const [fkey, ...frest] = filter.toLowerCase().split(':');
	const values = rest.join(':');
	const wanted = frest.join(':');
	if (!wanted) return tag.toLowerCase() === filter.toLowerCase();
	return key === fkey && (values === wanted || values.split('|').includes(wanted));
}
