import { Fragment } from "react";

import { RuleChip } from "@/components/days/RuleChip";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Trade } from "@/lib/api";
import { ruleLabel } from "@/lib/dayPage";
import { groupByDay, visibleColumns } from "@/lib/daysHome";
import { DISPLAY_TZ, formatDayMonth, formatNumber, formatPnl, formatR, pnlColorClass } from "@/lib/format";
import { cn } from "@/lib/utils";

const clockPt = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-US", {
    timeZone: DISPLAY_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });

/**
 * Trades grouped by day, newest day first. Each day header carries the day's net
 * (loud) beside its broken-rule count. Columns with no data in the shown trades
 * are dropped. A row opens the trade review.
 */
export function GroupedTradesTable({
  trades,
  onSelect,
}: {
  trades: Trade[];
  onSelect: (id: number) => void;
}) {
  const groups = groupByDay(trades);
  const cols = visibleColumns(trades);
  const span = 6 + (["exit", "r", "grade", "tags", "rules"] as const).filter((c) => cols.has(c)).length;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Time (PT)</TableHead>
            <TableHead>Contract</TableHead>
            <TableHead>Dir</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Entry</TableHead>
            {cols.has("exit") && <TableHead>Exit</TableHead>}
            <TableHead>Net P&amp;L</TableHead>
            {cols.has("r") && <TableHead>R</TableHead>}
            {cols.has("grade") && <TableHead>Grade</TableHead>}
            {cols.has("tags") && <TableHead>Tags</TableHead>}
            {cols.has("rules") && <TableHead>Rules broken</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map((g) => (
            <Fragment key={g.date}>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableCell colSpan={span} className="py-2">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="num text-body font-medium">{formatDayMonth(g.date)}</span>
                    <span className="num text-label text-muted-foreground">
                      {g.trades.length} {g.trades.length === 1 ? "trade" : "trades"}
                    </span>
                    <span className={cn("num text-value font-semibold", pnlColorClass(g.net))}>
                      {formatPnl(g.net)}
                    </span>
                    {g.brokenCount > 0 && (
                      <RuleChip
                        tone="broken"
                        label={`${g.brokenCount} rule ${g.brokenCount === 1 ? "break" : "breaks"}`}
                      />
                    )}
                  </div>
                </TableCell>
              </TableRow>
              {g.trades.map((t) => {
                const broken = (t.rule_results ?? []).filter((r) => r.kept === false);
                return (
                  <TableRow key={t.id} onClick={() => onSelect(t.id)} className="cursor-pointer text-sm">
                    <TableCell className="num whitespace-nowrap text-muted-foreground">
                      {clockPt(t.open_ts)}
                    </TableCell>
                    <TableCell className="font-medium">{t.contract_label ?? t.symbol}</TableCell>
                    <TableCell className={t.direction === "long" ? "text-gain" : "text-loss"}>
                      {t.direction === "long" ? "Long" : "Short"}
                    </TableCell>
                    <TableCell className="num">{formatNumber(t.qty, 0)}</TableCell>
                    <TableCell className="num">{formatNumber(t.avg_entry)}</TableCell>
                    {cols.has("exit") && <TableCell className="num">{formatNumber(t.avg_exit)}</TableCell>}
                    <TableCell className={cn("num text-value font-medium", pnlColorClass(t.net_pnl))}>
                      {formatPnl(t.net_pnl)}
                    </TableCell>
                    {cols.has("r") && (
                      <TableCell className={cn("num", pnlColorClass(t.r_multiple))}>
                        {t.r_multiple != null ? formatR(t.r_multiple) : ""}
                        {t.r_basis === "assumed" && t.r_multiple != null && (
                          <span className="ml-1 text-label text-muted-foreground">assumed</span>
                        )}
                      </TableCell>
                    )}
                    {cols.has("grade") && (
                      <TableCell>
                        {t.enrichment?.grade ? (
                          <Badge variant="outline" className="num">
                            {t.enrichment.grade}
                          </Badge>
                        ) : null}
                      </TableCell>
                    )}
                    {cols.has("tags") && (
                      <TableCell>
                        <div className="flex max-w-[220px] flex-wrap gap-1">
                          {(t.tags ?? []).slice(0, 3).map((tag) => (
                            <Badge key={tag.id} variant="muted">
                              {tag.name}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                    )}
                    {cols.has("rules") && (
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {broken.map((b) => (
                            <RuleChip key={b.code} tone="broken" label={ruleLabel(b.code)} title={b.detail} />
                          ))}
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
