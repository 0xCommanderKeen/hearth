/** How Townhall names a runtime, entirely from what the server said it is called.
 *
 * A household may run on more than one provider at once, and a finished run keeps the
 * runtime it was worked by whatever the household becomes afterwards. So nothing here
 * compares a kind against a provider's name: the snapshot's runtime table is the only
 * source, and a kind it does not name is shown as itself rather than as somebody else.
 */
import type { Runtimes } from "./client";

/** The runtime's full name: "Codex subscription". The store's default when unpinned. */
export function runtimeLabel(runtimes: Runtimes, kind?: string | null): string {
  const named = kind ?? runtimes.default;
  return runtimes.kinds[named]?.label ?? named;
}

/** Just the provider: "Codex". Enough for a chip, where the whole name will not fit.
 *
 * Only a live provider's name shortens. A kind that was never a provider is named in
 * full, because every retired one is called "retired <something>" and the first word
 * of that is not a name at all -- a restored copy still records one as its default.
 */
export function providerName(runtimes: Runtimes, kind?: string | null): string {
  const named = kind ?? runtimes.default;
  const label = runtimeLabel(runtimes, named);
  return runtimes.kinds[named]?.live ? label.split(" ")[0] : label;
}

/** Every runtime this household is configured for, its own default first.
 *
 * The default is always one of them, whatever the server listed: it is the runtime
 * every resident that has chosen nothing is admitted to.
 */
export function configuredKinds(runtimes: Runtimes): string[] {
  return [
    runtimes.default,
    ...runtimes.configured.filter((kind) => kind !== runtimes.default),
  ];
}

/** The household's providers, its own default first, for a line that names them all. */
export function runtimeNames(runtimes: Runtimes): string[] {
  return configuredKinds(runtimes).map((kind) => providerName(runtimes, kind));
}

/** What produced the summary an operator is reading, over the result itself.
 *
 * A run worked by a live provider is that provider's result. Anything else — a kind
 * from a release that only pretended to run work, or one this interface has never
 * heard of — is never presented as a provider's answer.
 */
export function resultEyebrow(
  runtimes: Runtimes,
  kind?: string | null,
): string {
  const named = kind ?? runtimes.default;
  return runtimes.kinds[named]?.live
    ? `${providerName(runtimes, named).toUpperCase()} RESULT`
    : "SIMULATED ARTIFACT";
}
