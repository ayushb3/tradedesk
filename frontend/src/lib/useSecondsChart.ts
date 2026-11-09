import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";

import { api, type Candle, type ReplaySecondsWindow } from "@/lib/api";
import { CHUNK_SEC, chunksFor, resSecOf, rollUpSeconds, type SecondsRes } from "@/lib/secondsChart";

export interface SecondsChart {
  candles: Candle[];
  /** First chunk start (epoch sec). */
  startSec: number;
  /** Last bar open (last chunk end - resSec). */
  endSec: number;
  sourceLabel: string | null;
  loading: boolean;
  /** Unavailable reason when every chunk came back available=false. */
  reason: string | null;
}

/**
 * Second bars for the chart: 1s bars for the playhead's 30-minute chunk and the one before,
 * rolled up client-side to `res` and cut at `playSec` (no look-ahead).
 */
export function useSecondsChart({
  symbol,
  playSec,
  res,
  enabled,
}: {
  symbol: string | null | undefined;
  playSec: number | null | undefined;
  res: SecondsRes | null;
  enabled: boolean;
}): SecondsChart {
  const active = Boolean(enabled && res && symbol && playSec);
  const chunkKey = active && playSec ? Math.floor(playSec / CHUNK_SEC) : null;
  // Stable across playSec changes inside the same chunk, so no refetch and no new query array.
  // In the last two minutes of a chunk, fetch the next one too, so playback crosses without a gap.
  const nearEnd = active && playSec != null && playSec % CHUNK_SEC >= CHUNK_SEC - 120;
  const chunks = useMemo(() => {
    if (chunkKey == null) return [];
    const cs = chunksFor(chunkKey * CHUNK_SEC);
    return nearEnd ? [...cs, { start: cs[1].end, end: cs[1].end + CHUNK_SEC }] : cs;
  }, [chunkKey, nearEnd]);
  const shown = nearEnd ? chunks.slice(0, 2) : chunks;

  const results = useQueries({
    queries: chunks.map((c) => ({
      queryKey: ["replay-seconds", symbol, c.start],
      queryFn: () =>
        api.replaySeconds({
          symbol: symbol as string,
          start: new Date(c.start * 1000).toISOString(),
          end: new Date(c.end * 1000).toISOString(),
          res: "1s" as const,
        }),
      staleTime: Infinity,
      retry: false,
    })),
  });

  const data: (ReplaySecondsWindow | undefined)[] = results.map((r) => r.data);
  // useQueries hands back a fresh array each render: re-merge only when a chunk's data lands.
  const sig = `${symbol}:${results.map((r) => r.dataUpdatedAt).join(",")}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const all = useMemo(() => data.flatMap((d) => (d?.available ? d.candles : [])), [sig]);
  const resSec = res ? resSecOf(res) : 1;
  const candles = useMemo(
    () => (active && playSec ? rollUpSeconds(all, resSec, playSec) : []),
    [active, all, resSec, playSec],
  );

  // The playhead's chunks (not the prefetched next one): a failed fetch counts as unavailable.
  const own = results.slice(0, shown.length);
  const settled = own.length > 0 && own.every((r) => r.data !== undefined || r.isError);
  const reason =
    settled && own.every((r) => r.isError || !r.data?.available)
      ? (own.map((r) => (r.isError ? "second bars could not be loaded" : r.data?.reason)).filter(Boolean).pop() ?? "unavailable")
      : null;
  const sourceLabel = data.find((d) => d?.source_label)?.source_label ?? null;
  const loading = active && own.length > 0 && own[own.length - 1].isLoading;

  return {
    candles,
    startSec: shown.length ? shown[0].start : 0,
    endSec: shown.length ? shown[shown.length - 1].end - resSec : 0,
    sourceLabel,
    loading,
    reason,
  };
}
