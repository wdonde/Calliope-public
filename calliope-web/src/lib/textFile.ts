/** File types the Story idea upload accepts (plain text only; read in the browser). */
export const TEXT_FILE_ACCEPT = '.txt,.md,.markdown,text/plain,text/markdown';

/**
 * Read a story file as text.
 *
 * UTF-8 first; a file that decodes with replacement characters is most likely
 * an older Windows export, so it is read again as Windows-1252 to keep umlauts.
 * Line endings are normalised to `\n`.
 */
export async function readTextFile(file: File): Promise<string> {
	const bytes = await file.arrayBuffer();
	let text = new TextDecoder('utf-8').decode(bytes);
	if (text.includes('�')) text = new TextDecoder('windows-1252').decode(bytes);
	return text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').trim();
}

/** "mein_roman-kapitel1.txt" -> "mein roman kapitel1" -- a title suggestion. */
export function titleFromFileName(name: string): string {
	return name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
}
